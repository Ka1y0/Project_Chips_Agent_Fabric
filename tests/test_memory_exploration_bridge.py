from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator
from pydantic import ValidationError
from test_autonomous_host import make_engine
from test_autonomy_driver import _store

from project_supervisor.api import APISettings, create_app
from project_supervisor.api.projections import SupervisorProjection
from project_supervisor.autonomous_host import AutonomousHostRepository
from project_supervisor.autonomy import GoalService, TerminationReason
from project_supervisor.autonomy_driver import ProductionAutonomyDriver, _bounded_json
from project_supervisor.bridge.model import BridgeEnvelope, StructuredArtifact
from project_supervisor.bridge.node_exchange import (
    DelegationPayload,
    NodeBridgeExchange,
    VerifiedPeer,
)
from project_supervisor.domain import (
    EvidenceConfidence,
    ExecutionTopology,
    Harness,
    NodeState,
    TaskLabel,
    TaskRecord,
    TaskRequirements,
    TaskState,
)
from project_supervisor.exploration import ExplorationConfig, ExplorationService
from project_supervisor.exploration_control import (
    ExplorationControlRequest,
    ExplorationControlState,
    set_control,
)
from project_supervisor.heartbeat import HeartbeatConfig, MetabolicHeartbeat
from project_supervisor.memory import MemoryDraft, MemoryEntry, MemoryRepository, MemoryRetrieval
from project_supervisor.protocols.capabilities import CapabilityGrant
from project_supervisor.protocols.identity import NodePublicIdentity
from project_supervisor.protocols.transport import TransportHealth, TransportStatus
from project_supervisor.resource_usage import (
    QuotaState,
    ResourceObservation,
    ResourceUsageRepository,
    ResourceUsageService,
    UsageProvenance,
)
from project_supervisor.store import StateStore


@pytest.fixture
def store(tmp_path: Path) -> StateStore:
    value = _store(tmp_path, harness=Harness.MOCK)
    GoalService(value).create_goal(
        project_id="project-1", intent="research blockers", goal_id="source"
    )
    make_engine(value)._terminate("source", TerminationReason.BLOCKED, "canonical blocker")
    repository = AutonomousHostRepository(value)
    repository.register_host("host-1", process_id=100)
    repository.heartbeat_host("host-1", active_goal_count=0)
    return value


def draft(store, **overrides):
    event = next(e for e in store.list_events(limit=1000) if e["kind"] == "goalTerminated")
    return MemoryDraft.model_validate(
        {
            "source_goal_id": "source",
            "source_event_id": event["event_id"],
            "kind": "blocker",
            "content": "research blocker",
            **overrides,
        }
    )


def task(store, task_id="task-1"):
    row = TaskRecord(
        id=task_id,
        project_id="project-1",
        title="Review",
        description="Read-only review",
        state=TaskState.DRAFT,
        topology=ExecutionTopology.SINGLE,
        requirements=TaskRequirements(labels=frozenset({TaskLabel.REVIEW})),
    )
    store.create_task(row, f"#{task_id}")
    return row


def quota(store, *, now, fresh=True):
    row = task(store, "quota-task")
    worker = store.list_workers()[0]
    ResourceUsageService(ResourceUsageRepository(store)).audit_task_terminal(
        task_id=row.id,
        run_id=None,
        terminal_state="failed",
        audit_key="quota-evidence",
        observations=(
            ResourceObservation(
                provider=worker["provider"],
                worker_id=worker["id"],
                quota_pool_id="pool",
                quota_state=QuotaState.AVAILABLE,
                quota_state_provenance=UsageProvenance.PROVIDER_REPORTED,
                confidence=EvidenceConfidence.EXACT,
                source="fixture",
                observed_at=now - timedelta(seconds=5),
                fresh_until=now + timedelta(hours=4) if fresh else now - timedelta(seconds=1),
            ),
        ),
    )


def test_memory_is_redacted_bound_idempotent_and_append_only(store):
    repo = MemoryRepository(store)
    entry = repo.record(draft(store, content="password=mock-sensitive-password"))
    assert "mock-sensitive-password" not in entry.content
    assert entry.content_sha256 == hashlib.sha256(entry.content.encode()).hexdigest()
    assert repo.record(draft(store, content="password=mock-sensitive-password")).id == entry.id
    with pytest.raises(ValueError):
        repo.record(draft(store, source_goal_id="not-the-source"))
    with store.connect() as connection, pytest.raises(Exception, match="append-only"):
        connection.execute("UPDATE memory_entries SET kind='conclusion'")
    assert repo.reflect_terminal_goals() == 1
    assert repo.reflect_terminal_goals() == 0


@pytest.mark.parametrize("change", [{"importance": "50"}, {"extra": True}, {"content": ""}])
def test_memory_strict(store, change):
    with pytest.raises(ValidationError):
        draft(store, **change)


def test_memory_retrieval_byte_budget_project_scope_and_hash(store):
    repo = MemoryRepository(store)
    for index in range(8):
        repo.record(draft(store, content=f"research blocker {index} " + "x" * 1000))
    result = repo.retrieve(
        project_id="project-1", query="research blocker", exclude_goal_id="next", max_bytes=2500
    )
    assert result.entries and result.truncated
    assert len(result.model_dump_json(by_alias=True).encode()) <= 2500
    assert result.omitted_context_sha256
    assert not repo.retrieve(project_id="other", query="research", exclude_goal_id="next").entries
    assert not repo.retrieve(
        project_id="project-1", query="research", exclude_goal_id="source"
    ).entries
    bounded = json.loads(
        _bounded_json(
            {
                "context": {
                    "goal": {"id": "keep-me"},
                    "longTermMemory": result.model_dump(by_alias=True),
                    "events": ["x" * 1000] * 10,
                }
            },
            4096,
        )
    )
    assert bounded["context"]["goal"]["id"] == "keep-me"
    assert bounded["context"]["omittedContextSHA256"]


def test_driver_context_includes_advisory_cross_goal_memory(store, tmp_path):
    from project_supervisor.runtime import AdapterRegistry, SupervisorRuntime
    from project_supervisor.scheduler import DeterministicScheduler

    MemoryRepository(store).record(draft(store))
    GoalService(store).create_goal(
        project_id="project-1", intent="research blocker", goal_id="next"
    )
    context = make_engine(store)._context("next")
    runtime = SupervisorRuntime(
        store=store,
        adapters=AdapterRegistry(),
        scheduler=DeterministicScheduler(),
        evidence_root=tmp_path,
    )
    value = ProductionAutonomyDriver(runtime)._context_value(context)
    assert value["longTermMemory"]["authority"] == "advisoryOnly"
    assert value["longTermMemory"]["entries"][0]["source_goal_id"] == "source"


def test_exploration_budget_quota_controls_and_readonly_goal(store):
    now = datetime.now(UTC)
    MemoryRepository(store).record(draft(store))
    service = ExplorationService(store)
    config = ExplorationConfig(enabled=True, exploration_budget_cap=1)
    assert service.propose_idle("host-1", config, now=now) is None  # UNKNOWN quota
    quota(store, now=now)
    proposal = service.propose_idle("host-1", config, now=now)
    assert proposal and proposal.code_write_required is False
    goal = GoalService(store).get_goal(proposal.goal_id)
    assert goal["origin"] == "exploration" and goal["wake_state"] == "sleeping"
    assert goal["wake_reason"] == "introspection"
    assert service.propose_idle("host-1", config, now=now + timedelta(hours=2)) is None
    assert service.status(now=now)["usedToday"] == 1


@pytest.mark.parametrize("mode", ["soft", "hard"])
def test_active_pause_blocks_but_historical_stop_allows_new_exploration(store, mode):
    now = datetime.now(UTC)
    quota(store, now=now)
    MemoryRepository(store).record(draft(store))
    goals = GoalService(store)
    goals.create_goal(project_id="project-1", intent="human work", goal_id="human")
    service = ExplorationService(store)
    config = ExplorationConfig(enabled=True, exploration_budget_cap=1)
    goals.pause("human", mode)
    assert service.propose_idle("host-1", config, now=now) is None
    status = service.status(now=now)
    assert status["usedToday"] == 0
    assert status["humanBrakes"] == [{"id": "human", "state": f"{mode}Paused"}]
    goals.stop("human", reason="finished with this goal")
    proposal = service.propose_idle("host-1", config, now=now)
    assert proposal and proposal.goal_id != "human"
    assert goals.get_goal("human")["termination_reason"] == "USER_STOPPED"
    assert service.status(now=now)["usedToday"] == 1
    assert service.status(now=now)["humanBrakes"] == []


def test_global_control_survives_reopen_and_gates_proposal_and_wake(store):
    now = datetime.now(UTC)
    quota(store, now=now)
    MemoryRepository(store).record(draft(store))
    service = ExplorationService(store)
    config = ExplorationConfig(enabled=True, exploration_budget_cap=1)

    def change(state, revision):
        return set_control(
            store,
            ExplorationControlRequest(
                state=state,
                expected_revision=revision,
                reason="operator control",
            ),
        )

    change("paused", 0)
    assert ExplorationService(StateStore(store.path)).status()["globalControl"] == {
        "state": "paused",
        "revision": 1,
    }
    assert service.propose_idle("host-1", config, now=now) is None
    assert service.status(now=now)["usedToday"] == 0
    change("open", 1)
    proposal = service.propose_idle("host-1", config, now=now)
    assert proposal
    change("paused", 2)
    heartbeat = MetabolicHeartbeat(store)
    options = dict(config=HeartbeatConfig(introspection_enabled=True), lease_ttl_seconds=30)
    assert not heartbeat.tick("host-1", now=now, **options)
    tick = heartbeat.list_ticks(goal_id=proposal.goal_id)[0]
    assert tick["reason"] == "globalExplorationPaused" and tick["wake_budget_used"] == 0
    change("open", 3)
    assert heartbeat.tick("host-1", now=now + timedelta(seconds=1), **options)
    assert service.status(now=now)["usedToday"] == 1


async def test_global_pause_during_exploration_cancels_once_and_resume_clears_blocker(
    store, tmp_path
):
    from test_metabolic_heartbeat import inspection_host

    from project_supervisor.autonomy_driver import DecisionDriverError

    now = datetime.now(UTC)
    quota(store, now=now)
    MemoryRepository(store).record(draft(store))
    proposal = ExplorationService(store).propose_idle(
        "host-1", ExplorationConfig(enabled=True), now=now
    )
    host, runtime = inspection_host(store, tmp_path, delay=1)
    repo = AutonomousHostRepository(store)
    claim = MetabolicHeartbeat(store).tick(
        "host-1", config=host.config.metabolic, lease_ttl_seconds=30, now=now
    )[0]
    running = asyncio.create_task(host._run_claimed(claim))
    async with asyncio.timeout(5):
        while not runtime._active:
            await asyncio.sleep(0.005)
    set_control(
        store, ExplorationControlRequest(state="paused", expected_revision=0, reason="hold")
    )
    with pytest.raises(DecisionDriverError, match="global exploration pause"):
        await running
    assert repo.eligible_goal_ids(limit=10) == []
    assert repo.try_acquire_goal(proposal.goal_id, "host-1", lease_ttl_seconds=30) is None
    set_control(
        store, ExplorationControlRequest(state="open", expected_revision=1, reason="resume")
    )
    claim = repo.try_acquire_goal(proposal.goal_id, "host-1", lease_ttl_seconds=30)
    result = await host._run_claimed(claim)
    assert result["termination_reason"] == "BLOCKED"
    with store.connect() as connection:
        assert (
            connection.execute(
                "SELECT state FROM autonomous_iterations WHERE goal_id=?", (proposal.goal_id,)
            ).fetchone()["state"]
            == "completed"
        )
    assert len(store.list_worker_runs()) == 1 and not runtime._active
    assert repo.eligible_goal_ids(limit=10) == []


def test_exploration_control_api_auth_schema_conflict_and_audit(store):
    path = "/v1/exploration/control"
    body = {"state": "paused", "expected_revision": 0, "reason": "password=fixture-private"}

    def headers(scopes):
        _, token = store.issue_api_token(label="local test", scopes=scopes)
        return {"Authorization": f"Bearer {token}"}

    reader = headers({"observe:read"})
    operator = headers({"observe:read", "goal:control"})
    with TestClient(create_app(store)) as client:
        assert client.post(path, json=body, headers=operator).status_code == 403
    with TestClient(create_app(store, APISettings(allow_goal_mutations=True))) as client:
        assert client.post(path, json=body).status_code == 401
        assert client.post(path, json=body, headers=reader).status_code == 403
        for invalid in (
            {"state": "stop"},
            {"expected_revision": "0"},
            {"extra": 1},
            {"reason": " "},
        ):
            assert client.post(path, json={**body, **invalid}, headers=operator).status_code == 422

        def post(**overrides):
            return client.post(path, json={**body, **overrides}, headers=operator)

        assert post().json()["data"] == {"state": "paused", "revision": 1}
        assert post(state="open").status_code == 409
        assert post(expected_revision=1).json()["data"]["revision"] == 1
        assert post(state="open", expected_revision=1).json()["data"] == {
            "state": "open",
            "revision": 2,
        }
        assert (
            client.get("/v1/exploration", headers=reader).json()["data"]["globalControl"][
                "revision"
            ]
            == 2
        )
    events = [e for e in store.list_events(limit=100) if e["kind"].startswith("globalExploration")]
    assert sorted(e["kind"] for e in events) == [
        "globalExplorationPaused",
        "globalExplorationResumed",
    ]
    assert "fixture-private" not in json.dumps(events)


def test_global_control_rolls_back_if_event_fails(store, monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError("audit unavailable")

    monkeypatch.setattr(store, "_append_event", fail)
    with pytest.raises(RuntimeError, match="audit unavailable"):
        set_control(
            store, ExplorationControlRequest(state="paused", expected_revision=0, reason="hold")
        )
    assert ExplorationService(store).status()["globalControl"] == {"state": "open", "revision": 0}


class FakeTransport:
    def __init__(self, identity, now):
        self.peer = VerifiedPeer(
            identity,
            TransportStatus(
                transport_id="fixture-private",
                provider="fixture",
                authenticated=True,
                encrypted=True,
                peer_identity=identity.node_id,
                reachable=True,
                health=TransportHealth.HEALTHY,
                observed_at=now,
            ),
        )
        self.encoded = b""

    async def verified_peer(self):
        return self.peer

    async def send(self, encoded):
        self.encoded = encoded

    async def receive(self):
        return self.encoded


def bridge_pair(store):
    now = datetime.now(UTC)
    identities = []
    for number, node in enumerate(["node-a", "node-b"]):
        store.upsert_node(
            node_id=node,
            hostname="fixture",
            display_name=node,
            role="worker",
            state=NodeState.ONLINE,
        )
        identity = NodePublicIdentity(
            node_id=node,
            key_id=f"key-{node}",
            algorithm="fixture",
            public_key_fingerprint="sha256:" + str(number + 1) * 64,
        )
        store.record_node_public_identity(identity)
        identities.append(identity)
    outbound, inbound = FakeTransport(identities[1], now), FakeTransport(identities[0], now)
    sender = NodeBridgeExchange(store, local_node_id="node-a", transport=outbound, enabled=True)
    receiver = NodeBridgeExchange(store, local_node_id="node-b", transport=inbound, enabled=True)
    return now, sender, receiver, outbound, inbound


async def test_bridge_status_roundtrip_is_derived_and_replay_idempotent(store):
    now, sender, receiver, outbound, inbound = bridge_pair(store)
    before = [(w["id"], w["state"]) for w in store.list_workers()]
    message = await sender.send_status(now=now)
    inbound.encoded = outbound.encoded
    assert await receiver.receive(now=now) == message
    assert await receiver.receive(now=now) == message
    messages = SupervisorProjection(store).bridge()["messages"]
    assert len(messages) == 2
    assert [(w["id"], w["state"]) for w in store.list_workers()] == before
    assert store.list_tasks() == []


@pytest.mark.parametrize(
    "mode",
    [
        "unauthenticated",
        "stale",
        "identity",
        "malformed",
        "extra",
        "hash",
        "sender",
        "expired",
        "version",
    ],
)
async def test_bridge_fail_closed(store, mode):
    now, sender, receiver, outbound, inbound = bridge_pair(store)
    await sender.send_status(now=now)
    envelope = BridgeEnvelope.from_bytes(outbound.encoded)
    inbound.encoded = outbound.encoded
    if mode == "unauthenticated":
        inbound.peer = replace(
            inbound.peer, transport=replace(inbound.peer.transport, authenticated=False)
        )
    elif mode == "stale":
        inbound.peer = replace(
            inbound.peer,
            transport=replace(inbound.peer.transport, observed_at=now - timedelta(minutes=1)),
        )
    elif mode == "identity":
        inbound.peer = replace(
            inbound.peer,
            identity=replace(inbound.peer.identity, public_key_fingerprint="sha256:" + "f" * 64),
        )
    elif mode == "malformed":
        inbound.encoded = b'{"password":"fixture-must-not-be-persisted"}'
    elif mode == "extra":
        content = {**envelope.payload.content, "shell": "forbidden"}
        inbound.encoded = replace(
            envelope,
            payload=StructuredArtifact(
                envelope.payload.artifact_id, envelope.payload.artifact_type, content
            ),
        ).to_bytes()
    elif mode == "hash":
        inbound.encoded = outbound.encoded.replace(b'"online"', b'"offline"')
    elif mode == "sender":
        inbound.encoded = replace(envelope, sender_id="node-b").to_bytes()
    elif mode == "version":
        inbound.encoded = replace(envelope, bridge_version="unknown").to_bytes()
    else:
        now += timedelta(minutes=6)
        inbound.peer = replace(
            inbound.peer, transport=replace(inbound.peer.transport, observed_at=now)
        )
    with pytest.raises(ValueError, match="rejected"):
        await receiver.receive(now=now)
    assert len(SupervisorProjection(store).bridge()["messages"]) == 1
    assert "fixture-must-not-be-persisted" not in json.dumps(store.list_events(limit=1000))


async def test_delegation_requires_local_task_and_bilateral_scoped_grants(store):
    now, sender, receiver, outbound, inbound = bridge_pair(store)
    task(store)
    with pytest.raises(ValueError, match="grant"):
        await sender.send_delegation("task-1", now=now)
    for capability, peer in [
        ("bridge.delegate.send", "node-b"),
        ("bridge.delegate.receive", "node-a"),
    ]:
        store.record_capability_grant(
            CapabilityGrant(
                grant_id=capability,
                capability=capability,
                subject=peer,
                requested_by="operator",
                task_id="task-1",
                issued_by="operator",
                issued_at=now - timedelta(seconds=1),
                expires_at=now + timedelta(minutes=5),
            )
        )
    await sender.send_delegation("task-1", now=now)
    inbound.encoded = outbound.encoded
    await receiver.receive(now=now)
    assert store.get_task("task-1")["state"] == "draft"  # Receipt does not dispatch.
    store.revoke_capability_grant("bridge.delegate.receive", revoked_by="operator")
    with pytest.raises(ValueError, match="rejected"):
        await receiver.receive(now=now)
    with pytest.raises(ValidationError):
        DelegationPayload.model_validate(
            {
                "task_id": "task-1",
                "task_sha256": "0" * 64,
                "labels": ["coding"],
                "code_write_required": True,
                "observed_at": "x",
                "expires_at": "x",
            }
        )


def test_readonly_api_auth_and_schemas(store):
    MemoryRepository(store).record(draft(store))
    cursor = store.highest_event_sequence()
    with TestClient(create_app(store)) as client:
        for path in ["memory", "exploration", "bridge", "autonomy/heartbeat"]:
            assert client.get(f"/v1/{path}").status_code == 401
    with TestClient(create_app(store, allow_unauthenticated_loopback=True)) as client:
        for path in ["memory", "exploration", "bridge", "autonomy/heartbeat"]:
            assert client.get(f"/v1/{path}").status_code == 200
            assert client.post(f"/v1/{path}", json={}).status_code == 405
    assert store.highest_event_sequence() == cursor
    for name, model in [
        ("memory-entry-v1", MemoryEntry),
        ("memory-retrieval-v1", MemoryRetrieval),
        ("exploration-control-request-v1", ExplorationControlRequest),
        ("exploration-control-state-v1", ExplorationControlState),
    ]:
        schema = json.loads(Path(f"schemas/{name}.schema.json").read_text())
        Draft202012Validator.check_schema(schema)
        assert schema["additionalProperties"] is False
        assert {k: v for k, v in schema.items() if k != "$schema"} == model.model_json_schema()


async def test_exploration_executes_once_through_mock_driver_and_cannot_plan(store, tmp_path):
    from test_metabolic_heartbeat import inspection_host

    now = datetime.now(UTC)
    quota(store, now=now)
    MemoryRepository(store).record(draft(store))
    proposal = ExplorationService(store).propose_idle(
        "host-1",
        ExplorationConfig(enabled=True),
        now=now,
    )
    output = json.dumps(
        {
            "schemaVersion": "autonomy-decision/v1",
            "decisionType": "evaluation",
            "goalID": proposal.goal_id,
            "iterationSequence": 1,
            "steerVersion": 0,
            "disposition": "incomplete",
            "summary": "advisory finding",
            "progressFingerprint": "finding",
            "facts": {},
        }
    )
    host, runtime = inspection_host(store, tmp_path, text=output)
    claim = MetabolicHeartbeat(store).tick(
        "host-1",
        config=HeartbeatConfig(introspection_enabled=True),
        lease_ttl_seconds=30.0,
        now=datetime.now(UTC),
    )[0]
    with pytest.raises(ValueError, match="read-only"):
        await make_engine(store).run_once(proposal.goal_id)
    await host._run_claimed(claim)
    goal = GoalService(store).get_goal(proposal.goal_id)
    assert goal["state"] == "terminated" and goal["termination_reason"] == "ITERATION_LIMIT"
    assert len(store.list_worker_runs()) == 1 and not runtime._active
    decision = next(t for t in store.list_tasks() if t["id"] != "quota-task")
    assert store.task_attempt_limit(decision["id"], 3) == 1


def test_exploration_cooldown_and_concurrent_reservations(store):
    from concurrent.futures import ThreadPoolExecutor

    now = datetime.now(UTC)
    quota(store, now=now)
    repo = MemoryRepository(store)
    repo.record(draft(store, content="first research blocker"))
    repo.record(draft(store, content="second research blocker"))
    config = ExplorationConfig(enabled=True, exploration_budget_cap=2)

    def propose(_):
        return ExplorationService(StateStore(store.path)).propose_idle("host-1", config, now=now)

    with ThreadPoolExecutor(max_workers=2) as pool:
        values = list(pool.map(propose, range(2)))
    assert sum(v is not None for v in values) == 1
    assert (
        ExplorationService(store).propose_idle("host-1", config, now=now + timedelta(seconds=10))
        is None
    )


@pytest.mark.parametrize("value", [True, 0, "false"])
def test_exploration_write_permission_is_strict_and_always_false(value):
    from project_supervisor.exploration import ExplorationProposal

    with pytest.raises(ValidationError):
        ExplorationProposal(
            id="p",
            source_memory_id="m",
            source_goal_id="source",
            goal_id="g",
            reason="blocker",
            intent="Review only",
            created_at="2026-01-01T00:00:00Z",
            code_write_required=value,
        )
