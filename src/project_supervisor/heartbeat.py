"""Local metabolic checks; only a fenced wake reservation can reach the expensive stage."""

from __future__ import annotations

import hashlib
import sqlite3
import uuid
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .domain import EventSeverity
from .exploration_control import read_control
from .resource_usage import (
    PreDispatchQuotaGuard,
    QuotaGuardCandidate,
    QuotaState,
    ResourceUsageRepository,
)
from .store import StateStore, compact_json

if TYPE_CHECKING:
    from .autonomous_host import GoalLeaseClaim


class HeartbeatConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, allow_inf_nan=False)

    interval_seconds: float = Field(default=30.0, gt=0)
    max_daily_wakes: int = Field(default=24, ge=0)
    backoff_seconds: float = Field(default=60.0, gt=0)
    iteration_sleep_seconds: float | None = Field(default=None, gt=0)
    introspection_enabled: bool = False
    max_daily_inspections: int = Field(default=4, ge=0)


def utc_time(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("an aware UTC clock is required")
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def parse_time(value: str) -> datetime:
    if not value.endswith("Z"):
        raise ValueError("timestamp must use UTC Z notation")
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.utcoffset() != timedelta(0):
        raise ValueError("timestamp must be UTC")
    return result


class SleepPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, allow_inf_nan=False)

    schema_version: Literal["goal-sleep/v1"] = "goal-sleep/v1"
    wake_reason: Literal["timer", "quotaCooldown", "workerHealth", "introspection"]
    next_wake_at: str | None = None
    cooldown_until: str | None = None
    worker_id: str | None = Field(default=None, min_length=1, max_length=200)
    quota_pool_id: str | None = Field(default=None, min_length=1, max_length=200)
    inspection_interval_seconds: float | None = Field(default=None, ge=60)

    @field_validator("next_wake_at", "cooldown_until")
    @classmethod
    def validate_time(cls, value: str | None) -> str | None:
        return utc_time(parse_time(value)) if value is not None else None

    @model_validator(mode="after")
    def validate_condition(self) -> SleepPolicy:
        if self.wake_reason in {"timer", "introspection"} and self.next_wake_at is None:
            raise ValueError("timer and introspection require next_wake_at")
        if self.wake_reason in {"quotaCooldown", "workerHealth"} and self.worker_id is None:
            raise ValueError("resource wake requires a persisted worker_id")
        if self.wake_reason == "quotaCooldown":
            if self.cooldown_until is None or self.quota_pool_id is None:
                raise ValueError("quota wake requires cooldown_until and quota_pool_id")
        elif self.quota_pool_id is not None:
            raise ValueError("quota_pool_id is only valid for quotaCooldown")
        if self.wake_reason == "introspection":
            if self.inspection_interval_seconds is None:
                raise ValueError("introspection requires an interval")
        elif self.inspection_interval_seconds is not None:
            raise ValueError("inspection interval is only valid for introspection")
        return self


class HeartbeatTick(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    schema_version: Literal["heartbeat-tick/v1"] = "heartbeat-tick/v1"
    tick_id: str
    host_id: str
    evaluated_at: str
    trigger_source: Literal["poll", "manual"]
    result: Literal["NOOP", "WOKE"]
    reason: str
    goal_id: str | None
    lease_generation: int | None = Field(ge=1)
    wake_budget_used: int = Field(ge=0, le=1)
    inspection_budget_used: int = Field(ge=0, le=1)
    local_model_calls: Literal[0] = 0
    local_tokens: Literal[0] = 0

    @field_validator("local_model_calls", "local_tokens", mode="before")
    @classmethod
    def strict_zero(cls, value: Any) -> int:
        if type(value) is not int or value != 0:
            raise ValueError("deterministic heartbeat counters must be the integer zero")
        return value

    @model_validator(mode="after")
    def validate_result(self) -> HeartbeatTick:
        parse_time(self.evaluated_at)
        if self.result == "NOOP":
            if self.wake_budget_used or self.inspection_budget_used or self.lease_generation:
                raise ValueError("NOOP cannot reserve a wake")
        elif self.goal_id is None or self.lease_generation is None or self.wake_budget_used != 1:
            raise ValueError("WOKE requires a Goal lease and budget reservation")
        return self


class MetabolicHeartbeat:
    """SQLite-only fast path. No adapters, model calls, network probes, or inferred capacity."""

    def __init__(self, store: StateStore) -> None:
        from .autonomous_host import AutonomousHostRepository

        self.store = store
        self.hosts = AutonomousHostRepository(store)
        self.resources = ResourceUsageRepository(store)

    @staticmethod
    def _assert_claim(connection: sqlite3.Connection, claim: GoalLeaseClaim, now: str) -> None:
        from .autonomous_host import GoalLeaseLost

        row = connection.execute(
            "SELECT 1 FROM autonomous_goal_leases WHERE goal_id=? AND host_id=? "
            "AND generation=? AND state='owned' AND expires_at>?",
            (claim.goal_id, claim.host_id, claim.generation, now),
        ).fetchone()
        if row is None:
            raise GoalLeaseLost("metabolic transition lost its unexpired Goal lease")

    def enter_sleep(
        self,
        goal_id: str,
        policy: SleepPolicy,
        *,
        claim: GoalLeaseClaim | None = None,
        now: datetime | None = None,
    ) -> None:
        """Configure an idle Goal, or checkpoint an owned Goal at a safe iteration boundary."""

        policy = SleepPolicy.model_validate(policy.model_dump())
        instant = utc_time(now or datetime.now(UTC))
        with self.store.transaction() as connection:
            goal = self._goal(connection, goal_id)
            if goal["state"] not in {"created", "running"}:
                raise ValueError("only runnable Goals may enter sleep")
            if claim is not None:
                if claim.goal_id != goal_id:
                    raise ValueError("sleep claim must belong to the Goal")
                self._assert_claim(connection, claim, instant)
            elif connection.execute(
                "SELECT 1 FROM autonomous_goal_leases WHERE goal_id=? AND state='owned'",
                (goal_id,),
            ).fetchone():
                raise ValueError("an owned Goal requires its current lease to enter sleep")
            if connection.execute(
                "SELECT 1 FROM autonomous_iterations WHERE goal_id=? "
                "AND state NOT IN ('completed','interrupted')",
                (goal_id,),
            ).fetchone():
                raise ValueError("sleep requires a completed iteration boundary")
            baseline = self._health(connection, policy.worker_id)[0] if policy.worker_id else None
            self._sleep(connection, goal, policy, instant, baseline)

    def _sleep(
        self,
        connection: sqlite3.Connection,
        goal: dict[str, Any],
        policy: SleepPolicy,
        now: str,
        baseline: str | None,
    ) -> None:
        connection.execute(
            "UPDATE autonomous_goals SET wake_state='sleeping',next_wake_at=?,wake_reason=?,"
            "cooldown_until=?,wake_policy_json=?,wake_health_baseline=?,updated_at=?,"
            "version=version+1 WHERE id=?",
            (
                policy.next_wake_at,
                policy.wake_reason,
                policy.cooldown_until,
                compact_json(policy.model_dump()),
                baseline,
                now,
                goal["id"],
            ),
        )
        self.store._append_event(
            connection,
            kind="goalEnteredSleep",
            severity=EventSeverity.INFO,
            entity_type="goal",
            entity_id=goal["id"],
            project_id=goal["project_id"],
            summary="Goal entered metabolic sleep",
            payload=policy.model_dump(),
            actor="heartbeat",
        )

    def tick(
        self,
        host_id: str,
        *,
        config: HeartbeatConfig,
        lease_ttl_seconds: float,
        capacity: int = 1,
        now: datetime | None = None,
        trigger_source: Literal["poll", "manual"] = "poll",
    ) -> list[GoalLeaseClaim]:
        """Reserve wake budgets and claim generations in the same serialized transaction."""

        config = HeartbeatConfig.model_validate(config.model_dump())
        instant = now or datetime.now(UTC)
        stamp = utc_time(instant)
        claims: list[GoalLeaseClaim] = []
        with self.store.transaction() as connection:
            host = connection.execute(
                "SELECT state FROM autonomous_hosts WHERE host_id=?",
                (host_id,),
            ).fetchone()
            if host is None or host["state"] not in {"starting", "running"}:
                raise ValueError("heartbeat requires a running host")
            goals = connection.execute(
                "SELECT * FROM autonomous_goals WHERE wake_state='sleeping' ORDER BY updated_at,id"
            ).fetchall()
            if not goals:
                self._record(connection, host_id, stamp, trigger_source, "noSleepingGoals", None)
            for row in goals:
                goal = dict(row)
                policy = SleepPolicy.model_validate_json(goal["wake_policy_json"] or "null")
                # Reject corrupt duplicated scheduling columns rather than guessing intent.
                if any(
                    goal[key] != getattr(policy, key)
                    for key in ("wake_reason", "next_wake_at", "cooldown_until")
                ):
                    raise ValueError("persisted sleep policy does not match Goal schedule")
                reason = self._blocked(connection, goal, policy, config, instant)
                if reason is None and len(claims) >= capacity:
                    reason = "hostCapacity"
                claim = None
                if reason is None:
                    connection.execute(
                        "UPDATE autonomous_goals SET wake_state='awake' WHERE id=?", (goal["id"],)
                    )
                    claim = self.hosts._acquire_goal(
                        connection,
                        goal["id"],
                        host_id,
                        lease_ttl_seconds=lease_ttl_seconds,
                        now_value=instant,
                    )
                    if claim is None:
                        # Should only be possible if a lease was already owned; never leave awake.
                        raise RuntimeError("wake lease claim failed closed")
                    self._assert_claim(connection, claim, stamp)
                    connection.execute(
                        "UPDATE autonomous_goals SET last_self_wake_at=?,updated_at=?,"
                        "version=version+1 WHERE id=?",
                        (stamp, stamp, goal["id"]),
                    )
                    self.store._append_event(
                        connection,
                        kind="goalSelfAwakened",
                        severity=EventSeverity.NOTICE,
                        entity_type="goal",
                        entity_id=goal["id"],
                        project_id=goal["project_id"],
                        summary="Goal self-awakened under a fenced lease",
                        payload={
                            "reason": policy.wake_reason,
                            "hostID": host_id,
                            "generation": claim.generation,
                        },
                        actor="heartbeat",
                    )
                    claims.append(claim)
                self._record(
                    connection,
                    host_id,
                    stamp,
                    trigger_source,
                    reason or policy.wake_reason,
                    goal["id"],
                    claim=claim,
                    inspection=policy.wake_reason == "introspection",
                )
        return claims

    def _blocked(
        self,
        connection: sqlite3.Connection,
        goal: dict[str, Any],
        policy: SleepPolicy,
        config: HeartbeatConfig,
        now: datetime,
    ) -> str | None:
        stamp = utc_time(now)
        if goal["state"] not in {"created", "running"}:
            return "humanControlOrTerminal"
        if goal["origin"] == "exploration" and read_control(connection).state == "paused":
            return "globalExplorationPaused"
        if connection.execute(
            "SELECT 1 FROM autonomous_goal_leases WHERE goal_id=? AND state='owned' "
            "AND expires_at>?",
            (goal["id"], stamp),
        ).fetchone():
            return "leaseOwned"
        if policy.wake_reason == "introspection" and not config.introspection_enabled:
            return "introspectionDisabled"
        if goal["last_self_wake_at"] is not None:
            previous = parse_time(goal["last_self_wake_at"])
            if now < previous + timedelta(seconds=config.backoff_seconds):
                return "backoff"
        # A future ledger entry (e.g. clock rollback) is also counted, conservatively.
        budget = connection.execute(
            "SELECT COALESCE(SUM(wake_budget_used),0), "
            "COALESCE(SUM(inspection_budget_used),0) FROM heartbeat_ticks WHERE evaluated_at>=?",
            (utc_time(now.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)),),
        ).fetchone()
        if budget[0] >= config.max_daily_wakes:
            return "dailyWakeBudget"
        if policy.wake_reason == "introspection" and budget[1] >= config.max_daily_inspections:
            return "dailyInspectionBudget"
        if policy.next_wake_at and now < parse_time(policy.next_wake_at):
            return "timerPending"
        if policy.cooldown_until and now < parse_time(policy.cooldown_until):
            return "cooldownPending"
        if policy.wake_reason in {"quotaCooldown", "workerHealth"}:
            fingerprint, healthy, provider = self._health(connection, policy.worker_id)
            if not healthy:
                return "workerUnavailable"
            if policy.wake_reason == "workerHealth":
                return None if fingerprint != goal["wake_health_baseline"] else "healthUnchanged"
            row = self.resources._latest_pool_row(connection, policy.quota_pool_id)
            snapshot = self.resources._snapshot_from_row(row) if row is not None else None
            candidate = QuotaGuardCandidate(
                worker_id=policy.worker_id,
                quota_pool_id=policy.quota_pool_id,
                provider=provider,
            )
            evidence = PreDispatchQuotaGuard._effective_evidence(candidate, snapshot, now)
            if (
                evidence.quota_state not in {QuotaState.AVAILABLE, QuotaState.WARNING}
                or evidence.observed_at is None
                or evidence.observed_at > now
            ):
                return "quotaRecoveryUnproven"
        return None

    @staticmethod
    def _health(
        connection: sqlite3.Connection,
        worker_id: str | None,
    ) -> tuple[str, bool, str]:
        row = connection.execute(
            "SELECT w.state,w.resource_state,w.provider,n.state AS node_state "
            "FROM workers w JOIN nodes n ON n.id=w.node_id WHERE w.id=?",
            (worker_id,),
        ).fetchone()
        if row is None:
            raise ValueError("sleep references an unknown Worker")
        fingerprint = hashlib.sha256(compact_json(dict(row)).encode()).hexdigest()
        healthy = (
            row["state"] == "idle"
            and row["node_state"] == "online"
            and row["resource_state"] in {"available", "warning"}
        )
        return fingerprint, healthy, str(row["provider"])

    def finish_inspection(
        self,
        claim: GoalLeaseClaim,
        iteration_id: str,
        *,
        now: datetime | None = None,
    ) -> None:
        """Commit the inspection checkpoint and re-sleep together, including after restart."""

        instant = now or datetime.now(UTC)
        stamp = utc_time(instant)
        with self.store.transaction() as connection:
            self._assert_claim(connection, claim, stamp)
            goal = self._goal(connection, claim.goal_id)
            policy = SleepPolicy.model_validate_json(goal["wake_policy_json"] or "null")
            if policy.wake_reason != "introspection":
                raise ValueError("inspection requires an introspection policy")
            iteration = connection.execute(
                "SELECT * FROM autonomous_iterations WHERE id=? AND goal_id=?",
                (iteration_id, claim.goal_id),
            ).fetchone()
            if iteration is None or iteration["state"] != "evaluating":
                raise ValueError("inspection checkpoint is not evaluating")
            if iteration["evaluation_json"] is None:
                raise ValueError("inspection has no durable evaluation")
            connection.execute(
                "UPDATE autonomous_iterations SET state='completed',completed_at=?,updated_at=? "
                "WHERE id=?",
                (stamp, stamp, iteration_id),
            )
            if goal["origin"] == "exploration":
                connection.execute(
                    "UPDATE autonomous_goals SET state='terminated',pause_mode=NULL,"
                    "termination_reason='ITERATION_LIMIT',termination_detail=?,finished_at=?,"
                    "updated_at=?,version=version+1 WHERE id=? AND state IN ('created','running')",
                    ("One advisory exploration evaluation completed", stamp, stamp, claim.goal_id),
                )
                if goal["state"] in {"created", "running"}:
                    self.store._append_event(
                        connection,
                        kind="goalTerminated",
                        severity=EventSeverity.NOTICE,
                        entity_type="goal",
                        entity_id=claim.goal_id,
                        project_id=goal["project_id"],
                        summary="Bounded exploration evaluation completed",
                        payload={"reason": "ITERATION_LIMIT"},
                        actor="exploration",
                    )
                return
            next_policy = SleepPolicy.model_validate(
                {
                    **policy.model_dump(),
                    "next_wake_at": utc_time(
                        instant + timedelta(seconds=policy.inspection_interval_seconds)
                    ),
                }
            )
            self._sleep(connection, goal, next_policy, stamp, None)

    @staticmethod
    def _goal(connection: sqlite3.Connection, goal_id: str) -> dict[str, Any]:
        row = connection.execute("SELECT * FROM autonomous_goals WHERE id=?", (goal_id,)).fetchone()
        if row is None:
            raise KeyError(goal_id)
        return dict(row)

    def _record(
        self,
        connection: sqlite3.Connection,
        host_id: str,
        stamp: str,
        trigger_source: str,
        reason: str,
        goal_id: str | None,
        *,
        claim: GoalLeaseClaim | None = None,
        inspection: bool = False,
    ) -> None:
        tick = HeartbeatTick(
            tick_id=f"tick-{uuid.uuid4()}",
            host_id=host_id,
            evaluated_at=stamp,
            trigger_source=trigger_source,
            result="WOKE" if claim else "NOOP",
            reason=reason,
            goal_id=goal_id,
            lease_generation=claim.generation if claim else None,
            wake_budget_used=int(claim is not None),
            inspection_budget_used=int(claim is not None and inspection),
        )
        values = tick.model_dump(exclude={"schema_version"})
        connection.execute(
            f"INSERT INTO heartbeat_ticks({','.join(values)}) "
            f"VALUES ({','.join('?' for _ in values)})",
            tuple(values.values()),
        )
        self.store._append_event(
            connection,
            kind="heartbeatTickEvaluated",
            severity=EventSeverity.INFO,
            entity_type="heartbeatTick",
            entity_id=tick.tick_id,
            summary=f"Metabolic heartbeat {tick.result}",
            payload=tick.model_dump(),
            actor=f"autonomous-host:{host_id}",
        )

    def list_ticks(self, *, goal_id: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError("limit must be an integer between 1 and 1000")
        query = "SELECT * FROM heartbeat_ticks"
        parameters: list[Any] = []
        if goal_id is not None:
            query += " WHERE goal_id=?"
            parameters.append(goal_id)
        with self.store.connect() as connection:
            return [
                dict(row)
                for row in connection.execute(
                    query + " ORDER BY sequence DESC LIMIT ?",
                    (*parameters, limit),
                ).fetchall()
            ]
