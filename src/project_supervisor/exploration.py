"""Opt-in, budget-reserved exploration proposals with a one-evaluation execution ceiling."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .autonomy import GoalBudget
from .domain import EventSeverity, PermissionClass, TaskLabel, TaskRequirements
from .exploration_control import read_control
from .heartbeat import MetabolicHeartbeat, SleepPolicy, parse_time, utc_time
from .memory import MemoryEntry
from .resource_usage import (
    PreDispatchQuotaGuard,
    QuotaGuardCandidate,
    QuotaState,
    ResourceUsageRepository,
)
from .scheduler import DeterministicScheduler
from .store import StateStore, compact_json, redact_sensitive


class ExplorationConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, allow_inf_nan=False)

    enabled: bool = False
    exploration_budget_cap: int = Field(default=2, ge=0, le=100)
    cooldown_seconds: float = Field(default=3600.0, ge=60)


class ExplorationProposal(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    schema_version: Literal["exploration-proposal/v1"] = "exploration-proposal/v1"
    id: str
    source_memory_id: str
    source_goal_id: str
    goal_id: str
    reason: Literal["memoryGap", "hypothesis", "blocker", "failureLesson", "environment"]
    intent: str = Field(min_length=1, max_length=4500)
    permission_class: Literal["green"] = "green"
    labels: list[Literal["research", "review"]] = Field(
        default_factory=lambda: ["research", "review"], min_length=1, max_length=2
    )
    code_write_required: bool = Field(default=False, json_schema_extra={"const": False})
    state: Literal["reservedForReadOnlyEvaluation"] = "reservedForReadOnlyEvaluation"
    created_at: str

    @field_validator("code_write_required")
    @classmethod
    def forbid_writes(cls, value: bool) -> bool:
        if value:
            raise ValueError("exploration cannot request code writing")
        return value


class ExplorationService:
    def __init__(self, store: StateStore) -> None:
        self.store = store

    def propose_idle(
        self,
        host_id: str,
        config: ExplorationConfig,
        *,
        now: datetime | None = None,
    ) -> ExplorationProposal | None:
        config = ExplorationConfig.model_validate(config.model_dump())
        if not config.enabled:
            return None
        instant = now or datetime.now(UTC)
        stamp = utc_time(instant)
        with self.store.transaction() as connection:
            host = connection.execute(
                "SELECT state FROM autonomous_hosts WHERE host_id=?", (host_id,)
            ).fetchone()
            if host is None or host[0] != "running":
                raise ValueError("exploration requires a live running host")
            if read_control(connection).state == "paused":
                return None
            if (
                connection.execute(
                    "SELECT 1 FROM autonomous_goals WHERE state IN "
                    "('softPaused','hardPaused') "
                    "OR (state IN ('created','running') AND wake_state='awake') LIMIT 1"
                ).fetchone()
                or connection.execute(
                    "SELECT 1 FROM autonomous_goal_leases WHERE state='owned' AND expires_at>?",
                    (stamp,),
                ).fetchone()
            ):
                return None
            rows = connection.execute(
                "SELECT created_at FROM exploration_proposals ORDER BY created_at DESC"
            ).fetchall()
            day = utc_time(
                instant.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
            )
            if sum(row[0] >= day for row in rows) >= config.exploration_budget_cap:
                return None
            if rows and instant < parse_time(rows[0][0]) + timedelta(
                seconds=config.cooldown_seconds
            ):
                return None
            memory = connection.execute(
                "SELECT m.entry_json FROM memory_entries m JOIN autonomous_goals g "
                "ON g.id=m.source_goal_id LEFT JOIN exploration_proposals p ON "
                "p.source_memory_id=m.id "
                "WHERE p.id IS NULL AND g.origin='human' AND g.state='terminated' "
                "AND m.kind IN ('memoryGap','hypothesis','blocker','failureLesson','environment') "
                "ORDER BY m.created_at,m.id LIMIT 1"
            ).fetchone()
            if memory is None or not self._quota_available(instant):
                return None
            entry = MemoryEntry.model_validate_json(memory[0])
            if hashlib.sha256(entry.content.encode()).hexdigest() != entry.content_sha256:
                raise ValueError("exploration memory hash mismatch")
            proposal = ExplorationProposal.model_validate(
                redact_sensitive(
                    {
                        "id": f"explore-{uuid.uuid4()}",
                        "source_memory_id": entry.id,
                        "source_goal_id": entry.source_goal_id,
                        "goal_id": f"goal-{uuid.uuid4()}",
                        "reason": entry.kind,
                        "created_at": stamp,
                        "intent": "Read-only research/review of this advisory evidence. "
                        "Report uncertainty; "
                        "do not modify files, invoke tools, or grant permissions.\n"
                        + entry.content,
                    }
                )
            )
            connection.execute(
                "INSERT INTO autonomous_goals(id,project_id,intent,effective_intent,state,"
                "budgets_json,created_at,updated_at,origin) VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    proposal.goal_id,
                    entry.project_id,
                    proposal.intent,
                    proposal.intent,
                    "created",
                    compact_json(GoalBudget(max_iterations=1, max_tasks=1).to_dict()),
                    stamp,
                    stamp,
                    "exploration",
                ),
            )
            connection.execute(
                "INSERT INTO exploration_proposals VALUES (?,?,?,?,?,?,?,?)",
                (
                    proposal.id,
                    entry.id,
                    entry.source_goal_id,
                    proposal.goal_id,
                    host_id,
                    1,
                    proposal.model_dump_json(),
                    stamp,
                ),
            )
            goal = MetabolicHeartbeat._goal(connection, proposal.goal_id)
            MetabolicHeartbeat(self.store)._sleep(
                connection,
                goal,
                SleepPolicy(
                    wake_reason="introspection",
                    next_wake_at=stamp,
                    inspection_interval_seconds=60.0,
                ),
                stamp,
                None,
            )
            self.store._append_event(
                connection,
                kind="explorationGoalProposed",
                severity=EventSeverity.NOTICE,
                entity_type="goal",
                entity_id=proposal.goal_id,
                project_id=entry.project_id,
                summary="One read-only exploration evaluation reserved",
                payload=proposal.model_dump(),
                actor="exploration",
            )
        return proposal

    def _quota_available(self, now: datetime) -> bool:
        repository = ResourceUsageRepository(self.store)
        latest = repository.latest_by_worker([w["id"] for w in self.store.list_workers()])
        requirements = TaskRequirements(
            labels=frozenset({TaskLabel.RESEARCH, TaskLabel.REVIEW}),
            permission_class=PermissionClass.GREEN,
            privacy_sensitive=True,
            code_write_required=False,
        )
        scheduler = DeterministicScheduler()
        candidates = []
        for worker in self.store.worker_snapshots():
            snapshot = latest.get(worker.id)
            if snapshot is None or scheduler.constraint_rejections(requirements, worker):
                continue
            candidates.append(
                QuotaGuardCandidate(
                    worker_id=worker.id,
                    quota_pool_id=snapshot.observation.quota_pool_id,
                    provider=worker.provider.value,
                )
            )
        decision = PreDispatchQuotaGuard(repository).evaluate(candidates, as_of=now)
        return any(
            evidence.quota_state in {QuotaState.AVAILABLE, QuotaState.WARNING}
            and evidence.observed_at is not None
            and evidence.observed_at <= now
            for evidence in decision.evidence_by_worker.values()
        )

    def status(self, *, now: datetime | None = None) -> dict[str, Any]:
        instant = now or datetime.now(UTC)
        day = utc_time(instant.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0))
        with self.store.connect() as connection:
            connection.execute("BEGIN")
            rows = connection.execute(
                "SELECT proposal_json,created_at FROM exploration_proposals ORDER BY "
                "created_at DESC"
            ).fetchall()
            hosts = connection.execute(
                "SELECT host_id,metadata_json FROM autonomous_hosts WHERE state='running'"
            ).fetchall()
            control = read_control(connection)
            brakes = connection.execute(
                "SELECT id,state FROM autonomous_goals WHERE state IN "
                "('softPaused','hardPaused')"
            ).fetchall()
        return {
            "utcDay": day[:10],
            "budgetUnit": "oneReadOnlyEvaluationGoal",
            "usedToday": sum(row["created_at"] >= day for row in rows),
            "policies": [
                {
                    "hostID": r["host_id"],
                    "policy": json.loads(r["metadata_json"]).get("exploration"),
                }
                for r in hosts
            ],
            "globalControl": control.model_dump(),
            "humanBrakes": [dict(r) for r in brakes],
            "proposals": [
                ExplorationProposal.model_validate_json(r[0]).model_dump() for r in rows[:100]
            ],
        }
