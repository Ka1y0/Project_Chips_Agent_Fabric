"""Validated, source-bound advisory memory. Never an approval or an execution authority."""

from __future__ import annotations

import hashlib
import re
import sqlite3
import uuid
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .domain import EventSeverity
from .store import StateStore, compact_json, redact_sensitive, timestamp


class MemoryDraft(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    source_goal_id: str = Field(min_length=1, max_length=200)
    source_event_id: str = Field(min_length=1, max_length=200)
    kind: Literal[
        "conclusion",
        "failureLesson",
        "toolPreference",
        "environment",
        "memoryGap",
        "hypothesis",
        "blocker",
    ]
    content: str = Field(min_length=1, max_length=4000)
    tags: list[str] = Field(default_factory=list, max_length=12)
    importance: int = Field(default=50, ge=0, le=100)

    @model_validator(mode="after")
    def validate_text(self) -> MemoryDraft:
        if not self.content.strip() or any(not t.strip() or len(t) > 80 for t in self.tags):
            raise ValueError("memory text and tags must be bounded and nonempty")
        return self


class MemoryEntry(MemoryDraft):
    schema_version: Literal["memory-entry/v1"] = "memory-entry/v1"
    id: str
    project_id: str
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    created_at: str
    authority: Literal["advisoryOnly"] = "advisoryOnly"


class MemoryRetrieval(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    schema_version: Literal["memory-retrieval/v1"] = "memory-retrieval/v1"
    entries: list[MemoryEntry]
    truncated: bool
    omitted_context_sha256: str | None = Field(default=None, alias="omittedContextSHA256")
    authority: Literal["advisoryOnly"] = "advisoryOnly"


class MemoryRepository:
    def __init__(self, store: StateStore) -> None:
        self.store = store

    def record(self, draft: MemoryDraft) -> MemoryEntry:
        with self.store.transaction() as connection:
            return self._record(connection, draft)

    def _record(self, connection: sqlite3.Connection, draft: MemoryDraft) -> MemoryEntry:
        # Validate both before and after redaction; even IDs cannot smuggle sensitive text.
        draft = MemoryDraft.model_validate(draft.model_dump())
        draft = MemoryDraft.model_validate(redact_sensitive(draft.model_dump()))
        goal = connection.execute(
            "SELECT * FROM autonomous_goals WHERE id=?",
            (draft.source_goal_id,),
        ).fetchone()
        event = connection.execute(
            "SELECT * FROM events WHERE event_id=?",
            (draft.source_event_id,),
        ).fetchone()
        if (
            goal is None
            or goal["state"] not in {"terminated", "stopped"}
            or event is None
            or event["entity_id"] != goal["id"]
            or event["entity_type"] != "goal"
            or event["kind"] not in {"goalTerminated", "goalStopped"}
            or event["project_id"] != goal["project_id"]
        ):
            raise ValueError("memory requires a terminal Goal and its canonical outcome event")
        digest = hashlib.sha256(draft.content.encode()).hexdigest()
        prior = connection.execute(
            "SELECT entry_json FROM memory_entries WHERE source_goal_id=? AND source_event_id=? "
            "AND kind=? AND content_sha256=?",
            (draft.source_goal_id, draft.source_event_id, draft.kind, digest),
        ).fetchone()
        if prior:
            return MemoryEntry.model_validate_json(prior[0])
        entry = MemoryEntry(
            **draft.model_dump(),
            id=f"mem-{uuid.uuid4()}",
            project_id=goal["project_id"],
            content_sha256=digest,
            created_at=timestamp(),
        )
        entry = MemoryEntry.model_validate(redact_sensitive(entry.model_dump()))
        if entry.project_id != goal["project_id"]:
            raise ValueError("memory provenance contains a sensitive project identifier")
        connection.execute(
            "INSERT INTO memory_entries VALUES (?,?,?,?,?,?,?,?)",
            (
                entry.id,
                entry.project_id,
                entry.source_goal_id,
                entry.source_event_id,
                entry.kind,
                digest,
                compact_json(entry.model_dump()),
                entry.created_at,
            ),
        )
        self.store._append_event(
            connection,
            kind="memoryEntryRecorded",
            severity=EventSeverity.INFO,
            entity_type="memory",
            entity_id=entry.id,
            project_id=entry.project_id,
            summary="Validated advisory memory recorded",
            payload={
                "sourceGoalID": entry.source_goal_id,
                "sourceEventID": entry.source_event_id,
                "contentSHA256": digest,
                "kind": entry.kind,
            },
            actor="reflection",
        )
        return entry

    def reflect_terminal_goals(self, *, limit: int = 16) -> int:
        """Deterministic consolidation of durable outcomes, never a model/chat ingestion path."""

        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError("reflection limit must be 1..1000")
        with self.store.transaction() as connection:
            goals = connection.execute(
                "SELECT g.* FROM autonomous_goals g LEFT JOIN reflection_summaries r "
                "ON r.goal_id=g.id WHERE g.state IN ('terminated','stopped') AND r.goal_id IS NULL "
                "ORDER BY g.finished_at,g.id LIMIT ?",
                (limit,),
            ).fetchall()
            for goal in goals:
                event = connection.execute(
                    "SELECT event_id FROM events WHERE entity_id=? AND entity_type='goal' "
                    "AND kind IN ('goalTerminated','goalStopped') ORDER BY sequence DESC LIMIT 1",
                    (goal["id"],),
                ).fetchone()
                if event is None:
                    raise ValueError("terminal Goal is missing its canonical outcome event")
                reason = goal["termination_reason"]
                facts = {
                    "outcome": reason,
                    "iterations": goal["iteration_count"],
                    "failures": goal["failure_count"],
                    "detail": str(redact_sensitive(goal["termination_detail"] or ""))[:2000],
                }
                entry = self._record(
                    connection,
                    MemoryDraft(
                        source_goal_id=goal["id"],
                        source_event_id=event[0],
                        kind="conclusion" if reason == "SUCCESS" else "failureLesson",
                        content=compact_json(facts),
                        importance=70,
                        tags=[str(reason)],
                    ),
                )
                # Tool preferences are observed usage, not recommendations/permission grants.
                workers = connection.execute(
                    "SELECT w.harness,COUNT(*) AS attempts FROM worker_runs r "
                    "JOIN workers w ON w.id=r.worker_id WHERE r.task_id IN ("
                    "SELECT task_id FROM autonomous_actions WHERE goal_id=? UNION "
                    "SELECT task_id FROM autonomy_decision_tasks WHERE goal_id=?) GROUP "
                    "BY w.harness",
                    (goal["id"], goal["id"]),
                ).fetchall()
                ids = [entry.id]
                if workers:
                    ids.append(
                        self._record(
                            connection,
                            MemoryDraft(
                                source_goal_id=goal["id"],
                                source_event_id=event[0],
                                kind="toolPreference",
                                content=compact_json(
                                    {"observedAttempts": [dict(r) for r in workers]}
                                ),
                                importance=30,
                            ),
                        ).id
                    )
                nodes = connection.execute(
                    "SELECT state,COUNT(*) AS count FROM nodes GROUP BY state ORDER BY state"
                ).fetchall()
                ids.append(
                    self._record(
                        connection,
                        MemoryDraft(
                            source_goal_id=goal["id"],
                            source_event_id=event[0],
                            kind="environment",
                            content=compact_json(
                                {
                                    "canonicalNodeStateCounts": [dict(r) for r in nodes],
                                    "observationOnly": True,
                                }
                            ),
                            importance=20,
                        ),
                    ).id
                )
                summary = compact_json(
                    {"goalID": goal["id"], "memoryIDs": ids, "authority": "advisoryOnly"}
                )
                digest = hashlib.sha256(summary.encode()).hexdigest()
                connection.execute(
                    "INSERT INTO reflection_summaries VALUES (?,?,?,?,?)",
                    (goal["id"], event[0], digest, summary, timestamp()),
                )
                self.store._append_event(
                    connection,
                    kind="goalReflectionRecorded",
                    severity=EventSeverity.INFO,
                    entity_type="reflection",
                    entity_id=goal["id"],
                    project_id=goal["project_id"],
                    summary="Canonical outcome consolidated",
                    payload={"contentSHA256": digest},
                    actor="reflection",
                )
        return len(goals)

    def list_entries(self, *, project_id: str | None = None, limit: int = 100) -> list[MemoryEntry]:
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError("memory limit must be 1..1000")
        with self.store.connect() as connection:
            rows = connection.execute(
                "SELECT entry_json FROM memory_entries WHERE (? IS NULL OR project_id=?) "
                "ORDER BY created_at DESC,id LIMIT ?",
                (project_id, project_id, limit),
            ).fetchall()
        entries = [MemoryEntry.model_validate_json(r[0]) for r in rows]
        for entry in entries:
            if hashlib.sha256(entry.content.encode()).hexdigest() != entry.content_sha256:
                raise ValueError("memory content hash mismatch")
        return entries

    def retrieve(
        self, *, project_id: str, query: str, exclude_goal_id: str, max_bytes: int = 8192
    ) -> MemoryRetrieval:
        if type(max_bytes) is not int or max_bytes < 256:
            raise ValueError("memory byte budget must be at least 256")
        terms = set(re.findall(r"\w+", query.casefold()))
        entries = [
            e
            for e in self.list_entries(project_id=project_id, limit=200)
            if e.source_goal_id != exclude_goal_id
        ]
        entries.sort(
            key=lambda e: (
                -(
                    len(
                        terms
                        & set(re.findall(r"\w+", (e.content + " " + " ".join(e.tags)).casefold()))
                    )
                    * 20
                    + e.importance
                ),
                e.id,
            )
        )
        selected: list[MemoryEntry] = []
        omitted: list[dict[str, Any]] = []
        for entry in entries:
            trial = MemoryRetrieval(
                entries=[*selected, entry], truncated=True, omittedContextSHA256="0" * 64
            )
            if len(trial.model_dump_json(by_alias=True).encode()) <= max_bytes:
                selected.append(entry)
            else:
                omitted.append(entry.model_dump())
        return MemoryRetrieval(
            entries=selected,
            truncated=bool(omitted),
            omittedContextSHA256=(
                hashlib.sha256(compact_json(omitted).encode()).hexdigest() if omitted else None
            ),
        )
