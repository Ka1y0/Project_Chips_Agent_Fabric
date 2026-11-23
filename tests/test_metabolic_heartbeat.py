from __future__ import annotations

import asyncio
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError
from test_autonomy_driver import _store

from project_supervisor.adapters.mock import MockBehavior
from project_supervisor.autonomous_host import (
    AutonomousHost,
    AutonomousHostConfig,
    AutonomousHostRepository,
    GoalLeaseLost,
)
from project_supervisor.autonomy import (
    AutonomousIterationEngine,
    GoalService,
    SupervisorRuntimeDispatcher,
)
from project_supervisor.autonomy_driver import (
    ProductionAutonomyDriver,
    reconstruct_adapter_registry,
)
from project_supervisor.cli import main
from project_supervisor.domain import (
    EvidenceConfidence,
    ExecutionTopology,
    Harness,
    NodeState,
    TaskState,
)
from project_supervisor.heartbeat import (
    HeartbeatConfig,
    HeartbeatTick,
    MetabolicHeartbeat,
    SleepPolicy,
)
from project_supervisor.resource_usage import (
    QuotaState,
    ResourceObservation,
    ResourceUsageRepository,
    ResourceUsageService,
    UsageMetric,
    UsageProvenance,
)
from project_supervisor.runtime import SupervisorRuntime
from project_supervisor.scheduler import DeterministicScheduler
from project_supervisor.store import StateStore

NOW = datetime.now(UTC).replace(microsecond=0)


@pytest.fixture
def store(tmp_path: Path) -> StateStore:
    value = _store(tmp_path, harness=Harness.MOCK)
    GoalService(value).create_goal(project_id="project-1", intent="inspect", goal_id="goal-1")
    for host_id in ("host-1", "host-2"):
        AutonomousHostRepository(value).register_host(host_id, process_id=101)
    return value


def sleep(store: StateStore, **kwargs) -> MetabolicHeartbeat:
    metabolism = MetabolicHeartbeat(store)
    metabolism.enter_sleep(
        "goal-1",
        SleepPolicy(
            **{
                "wake_reason": "timer",
                "next_wake_at": NOW.isoformat().replace("+00:00", "Z"),
                **kwargs,
            }
        ),
    )
    return metabolism


def tick(metabolism: MetabolicHeartbeat, **kwargs):
    return metabolism.tick(
        "host-1",
        **{
            "config": HeartbeatConfig(),
            "lease_ttl_seconds": 30.0,
            "now": NOW,
            **kwargs,
        },
    )


def test_timer_noop_then_atomic_wake_and_restart(store: StateStore) -> None:
    metabolism = sleep(store)
    assert tick(metabolism, now=NOW - timedelta(seconds=1)) == []
    assert metabolism.list_ticks()[0]["local_tokens"] == 0
    assert AutonomousHostRepository(store).eligible_goal_ids(limit=10) == []
    claims = tick(MetabolicHeartbeat(StateStore(store.path)))
    assert len(claims) == 1
    assert claims[0].generation == 1
    row = metabolism.list_ticks()[0]
    assert row["result"] == "WOKE" and row["wake_budget_used"] == 1
    kinds = [e["kind"] for e in store.list_events(limit=1000)]
    assert {"goalEnteredSleep", "goalSelfAwakened", "heartbeatTickEvaluated"} <= set(kinds)
    with store.connect() as connection, pytest.raises(sqlite3.IntegrityError, match="append-only"):
        connection.execute("DELETE FROM heartbeat_ticks")


@pytest.mark.parametrize("control", ["soft", "hard", "stop"])
def test_controls_override_due_timer(store: StateStore, control: str) -> None:
    metabolism = sleep(store)
    goals = GoalService(store)
    if control == "stop":
        goals.stop("goal-1", reason="human")
    else:
        goals.pause("goal-1", control)
    assert tick(metabolism) == []
    assert metabolism.list_ticks()[0]["reason"] == "humanControlOrTerminal"
    assert goals.get_goal("goal-1")["wake_state"] == "sleeping"


def test_two_independent_connections_claim_once(store: StateStore) -> None:
    sleep(store)

    def attempt(host_id: str):
        return MetabolicHeartbeat(StateStore(store.path)).tick(
            host_id,
            config=HeartbeatConfig(),
            lease_ttl_seconds=30.0,
            now=NOW,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, ["host-1", "host-2"]))
    assert sum(map(len, results)) == 1
    assert sum(row["wake_budget_used"] for row in MetabolicHeartbeat(store).list_ticks()) == 1


def test_expired_generation_cannot_renew_or_sleep(store: StateStore) -> None:
    first = AutonomousHostRepository(store).try_acquire_goal(
        "goal-1",
        "host-1",
        lease_ttl_seconds=30.0,
    )
    with store.transaction() as connection:
        connection.execute("UPDATE autonomous_goal_leases SET expires_at='2000-01-01T00:00:00Z'")
    repo = AutonomousHostRepository(store)
    assert not repo.heartbeat_goal(first, lease_ttl_seconds=30.0)
    second = repo.try_acquire_goal("goal-1", "host-2", lease_ttl_seconds=30.0)
    assert second.generation == first.generation + 1
    with pytest.raises(GoalLeaseLost):
        sleep_policy = SleepPolicy(wake_reason="timer", next_wake_at="2030-01-01T00:00:00Z")
        MetabolicHeartbeat(store).enter_sleep("goal-1", sleep_policy, claim=first)


def test_daily_budget_and_backoff_survive_restart(store: StateStore) -> None:
    metabolism = sleep(store)
    first = tick(metabolism)[0]
    AutonomousHostRepository(store).release_goal(first)
    sleep(store)
    assert tick(metabolism, now=NOW + timedelta(seconds=1)) == []
    assert metabolism.list_ticks()[0]["reason"] == "backoff"
    assert (
        tick(
            MetabolicHeartbeat(StateStore(store.path)),
            config=HeartbeatConfig(max_daily_wakes=1),
            now=NOW + timedelta(seconds=61),
        )
        == []
    )
    assert metabolism.list_ticks()[0]["reason"] == "dailyWakeBudget"
    assert len(tick(metabolism, now=NOW + timedelta(days=1))) == 1


def test_invalid_policy_and_event_failure_roll_back(store: StateStore, monkeypatch) -> None:
    metabolism = sleep(store)
    original = store._append_event

    def fail(connection, **kwargs):
        if kwargs["kind"] == "heartbeatTickEvaluated":
            raise RuntimeError("journal unavailable")
        return original(connection, **kwargs)

    monkeypatch.setattr(store, "_append_event", fail)
    with pytest.raises(RuntimeError, match="journal unavailable"):
        tick(metabolism)
    assert GoalService(store).get_goal("goal-1")["wake_state"] == "sleeping"
    assert AutonomousHostRepository(store).get_goal_lease("goal-1") is None
    assert metabolism.list_ticks() == []
    with store.transaction() as connection:
        connection.execute("UPDATE autonomous_goals SET wake_policy_json='{}'")
    with pytest.raises(ValidationError):
        tick(metabolism)


def test_health_change_requires_recovery(store: StateStore) -> None:
    with store.transaction() as connection:
        connection.execute("UPDATE nodes SET state='offline'")
    worker_id = store.list_workers()[0]["id"]
    metabolism = sleep(store, wake_reason="workerHealth", next_wake_at=None, worker_id=worker_id)
    assert tick(metabolism) == []
    with store.transaction() as connection:
        connection.execute("UPDATE nodes SET state=?", (NodeState.ONLINE.value,))
    assert len(tick(metabolism)) == 1


def test_quota_recovery_requires_fresh_evidence(store: StateStore, tmp_path: Path) -> None:
    from project_supervisor.domain import TaskLabel, TaskRecord, TaskRequirements

    task = TaskRecord(
        state=TaskState.DRAFT,
        topology=ExecutionTopology.SINGLE,
        id="quota-task",
        project_id="project-1",
        title="evidence",
        description="quota",
        requirements=TaskRequirements(labels=frozenset({TaskLabel.RESEARCH})),
    )
    store.create_task(task, "#quota")
    worker = store.list_workers()[0]
    metabolism = sleep(
        store,
        wake_reason="quotaCooldown",
        next_wake_at=None,
        cooldown_until=NOW.isoformat().replace("+00:00", "Z"),
        worker_id=worker["id"],
        quota_pool_id="pool-1",
    )
    assert tick(metabolism) == []
    assert metabolism.list_ticks()[0]["reason"] == "quotaRecoveryUnproven"
    service = ResourceUsageService(ResourceUsageRepository(store))
    for index, (state, fresh) in enumerate(
        [(QuotaState.EXHAUSTED, True), (QuotaState.AVAILABLE, False), (QuotaState.AVAILABLE, True)]
    ):
        service.audit_task_terminal(
            task_id=task.id,
            run_id=None,
            terminal_state="failed",
            audit_key=f"quota-{index}",
            observations=(
                ResourceObservation(
                    provider=worker["provider"],
                    quota_pool_id="pool-1",
                    worker_id=worker["id"],
                    remaining=UsageMetric.known(
                        0 if state is QuotaState.EXHAUSTED else 10,
                        "calls",
                        UsageProvenance.PROVIDER_REPORTED,
                    ),
                    quota_state=state,
                    quota_state_provenance=UsageProvenance.PROVIDER_REPORTED,
                    source="test",
                    confidence=EvidenceConfidence.EXACT,
                    observed_at=NOW - timedelta(seconds=10 - index),
                    fresh_until=NOW + timedelta(seconds=300)
                    if fresh
                    else NOW - timedelta(seconds=1),
                ),
            ),
        )
        claims = tick(metabolism)
        assert bool(claims) == (index == 2)


async def test_idle_host_never_constructs_engine(store: StateStore) -> None:
    sleep(store, next_wake_at="2099-01-01T00:00:00Z")

    def fail(_goal_id):
        raise AssertionError("NOOP reached engine/adapter")

    host = AutonomousHost(
        store=store,
        engine_factory=fail,
        config=AutonomousHostConfig(
            poll_interval_seconds=0.01, metabolic=HeartbeatConfig(interval_seconds=0.01)
        ),
    )
    serving = asyncio.create_task(host.serve())
    async with asyncio.timeout(2):
        while len(host.metabolism.list_ticks()) < 2:
            await asyncio.sleep(0.01)
    host.request_shutdown()
    await serving
    assert all(row["result"] == "NOOP" for row in host.metabolism.list_ticks())


def inspection_host(store: StateStore, tmp_path: Path, *, delay=0.0, text=None):
    worker_id = store.list_workers()[0]["id"]
    output = json.dumps(
        {
            "schemaVersion": "autonomy-decision/v1",
            "decisionType": "evaluation",
            "goalID": "goal-1",
            "iterationSequence": 1,
            "steerVersion": 0,
            "disposition": "satisfied",
            "summary": "advisory",
            "progressFingerprint": "x",
            "terminationReason": None,
            "facts": {},
        }
    )
    runtime = SupervisorRuntime(
        store=store,
        scheduler=DeterministicScheduler(),
        evidence_root=tmp_path / "evidence",
        adapters=reconstruct_adapter_registry(
            store,
            allow_mock=True,
            mock_behaviors={worker_id: MockBehavior(text=text or output, delay_seconds=delay)},
        ),
    )
    driver = ProductionAutonomyDriver(runtime)

    def factory(_):
        return AutonomousIterationEngine(
            store=store,
            evaluator=driver,
            planner=driver,
            verifier=driver,
            dispatcher=SupervisorRuntimeDispatcher(runtime),
        )

    return AutonomousHost(
        store=store,
        engine_factory=factory,
        config=AutonomousHostConfig(metabolic=HeartbeatConfig(introspection_enabled=True)),
    ), runtime


async def test_inspection_is_read_only_and_resleeps(store: StateStore, tmp_path: Path) -> None:
    metabolism = sleep(store, wake_reason="introspection", inspection_interval_seconds=60.0)
    assert tick(metabolism) == []
    host, runtime = inspection_host(store, tmp_path)
    claims = tick(metabolism, config=host.config.metabolic, now=datetime.now(UTC))
    await host._run_claimed(claims[0])
    goal = GoalService(store).get_goal("goal-1")
    assert goal["wake_state"] == "sleeping" and goal["termination_reason"] is None
    tasks = store.list_tasks()
    assert len(tasks) == 1
    assert tasks[0]["permission_class"] == "green" and not tasks[0]["code_write_required"]
    assert set(json.loads(tasks[0]["labels_json"])) == {"research", "review"}
    with store.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM autonomous_actions").fetchone()[0] == 0
    assert not runtime._active


@pytest.mark.parametrize("control", ["soft", "hard", "stop"])
async def test_inspection_obeys_controls_during_worker_call(
    store: StateStore,
    tmp_path: Path,
    control: str,
) -> None:
    metabolism = sleep(store, wake_reason="introspection", inspection_interval_seconds=60.0)
    host, runtime = inspection_host(store, tmp_path, delay=0.1)
    claim = tick(metabolism, config=host.config.metabolic, now=datetime.now(UTC))[0]
    running = asyncio.create_task(host._run_claimed(claim))
    async with asyncio.timeout(2):
        while not runtime._active:
            await asyncio.sleep(0.005)
    goals = GoalService(store)
    if control == "stop":
        goals.stop("goal-1", reason="human")
    else:
        goals.pause("goal-1", control)
    await asyncio.gather(running, return_exceptions=True)
    await runtime.wait_for_active()
    state = goals.get_goal("goal-1")["state"]
    assert state == {"soft": "softPaused", "hard": "hardPaused", "stop": "stopped"}[control]
    assert len(store.list_tasks()) == 1
    assert tick(metabolism, now=NOW + timedelta(days=1), config=host.config.metabolic) == []


@pytest.mark.parametrize(
    "value",
    [
        {"wake_reason": "timer", "next_wake_at": "bad"},
        {"wake_reason": "timer", "next_wake_at": "2030-01-01T00:00:00Z", "extra": 1},
        {
            "wake_reason": "introspection",
            "next_wake_at": "2030-01-01T00:00:00Z",
            "inspection_interval_seconds": "60",
        },
    ],
)
def test_sleep_policy_strict(value) -> None:
    with pytest.raises(ValidationError):
        SleepPolicy.model_validate(value)


def test_contracts_and_cli_are_read_only(store: StateStore, tmp_path: Path, capsys) -> None:
    metabolism = sleep(store)
    tick(metabolism)
    for name, model in [("goal-sleep-v1", SleepPolicy), ("heartbeat-tick-v1", HeartbeatTick)]:
        schema = json.loads((Path("schemas") / f"{name}.schema.json").read_text())
        assert schema["additionalProperties"] is False
        Draft202012Validator.check_schema(schema)
        assert schema["properties"] == model.model_json_schema()["properties"]
    # init uses supervisor.db; use explicit config matching the fixture instead.
    config = tmp_path / "config.json"
    from project_supervisor.config import initialize_config

    initialize_config(tmp_path)
    with store.connect() as connection, sqlite3.connect(tmp_path / "supervisor.db") as target:
        connection.backup(target)
    before = StateStore(tmp_path / "supervisor.db").highest_event_sequence()
    assert (
        main(
            [
                "--data-dir",
                str(tmp_path),
                "--config",
                str(config),
                "--json",
                "autonomous",
                "heartbeat",
            ]
        )
        == 0
    )
    assert "ticks" in json.loads(capsys.readouterr().out)
    assert StateStore(tmp_path / "supervisor.db").highest_event_sequence() == before


async def test_malformed_inspection_never_plans_or_retries_provider(
    store: StateStore,
    tmp_path: Path,
) -> None:
    metabolism = sleep(store, wake_reason="introspection", inspection_interval_seconds=60.0)
    host, runtime = inspection_host(store, tmp_path, text='{"unsafe":true}')
    claim = tick(metabolism, config=host.config.metabolic, now=datetime.now(UTC))[0]
    with pytest.raises(ValidationError):
        await host._run_claimed(claim)
    assert len(store.list_worker_runs()) == 1
    with store.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM autonomous_actions").fetchone()[0] == 0
    assert not runtime._active


def test_inspection_budget_is_separate_and_capacity_is_noop(store: StateStore) -> None:
    metabolism = sleep(store, wake_reason="introspection", inspection_interval_seconds=60.0)
    config = HeartbeatConfig(introspection_enabled=True, max_daily_inspections=0)
    assert tick(metabolism, config=config) == []
    assert metabolism.list_ticks()[0]["reason"] == "dailyInspectionBudget"
    assert tick(metabolism, config=HeartbeatConfig(introspection_enabled=True), capacity=0) == []
    assert metabolism.list_ticks()[0]["reason"] == "hostCapacity"
    assert not store.list_tasks()


@pytest.mark.parametrize("value", [False, 0.0, "0", 1])
def test_heartbeat_zero_token_counter_rejects_coercion(value):
    from project_supervisor.heartbeat import HeartbeatTick

    with pytest.raises(ValueError):
        HeartbeatTick(
            tick_id="tick",
            host_id="host",
            evaluated_at="2026-01-01T00:00:00Z",
            trigger_source="poll",
            result="NOOP",
            reason="timerPending",
            goal_id=None,
            lease_generation=None,
            wake_budget_used=0,
            inspection_budget_used=0,
            local_tokens=value,
        )
