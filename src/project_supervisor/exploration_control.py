"""Durable human control of spontaneous exploration across one database."""

from __future__ import annotations

import sqlite3
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .domain import EventSeverity
from .store import StateStore, redact_sensitive


class ExplorationControlState(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    state: Literal["open", "paused"]
    revision: int = Field(ge=0)


class ExplorationControlRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    state: Literal["open", "paused"]
    expected_revision: int = Field(ge=0)
    reason: str = Field(min_length=1, max_length=1000, pattern=r"\S")


class StaleControlRevision(ValueError):
    pass


def read_control(connection: sqlite3.Connection) -> ExplorationControlState:
    row = connection.execute(
        "SELECT state,revision FROM exploration_control WHERE singleton=1"
    ).fetchone()
    if row is None:
        raise RuntimeError("missing global exploration control")
    return ExplorationControlState.model_validate(dict(row))


def set_control(store: StateStore, request: ExplorationControlRequest) -> ExplorationControlState:
    request = ExplorationControlRequest.model_validate(request.model_dump())
    with store.transaction() as connection:
        current = read_control(connection)
        if current.revision != request.expected_revision:
            raise StaleControlRevision("exploration control changed; refresh before confirming")
        if current.state != request.state:
            connection.execute(
                "UPDATE exploration_control SET state=?,revision=revision+1 WHERE singleton=1",
                (request.state,),
            )
            current = read_control(connection)
            store._append_event(
                connection,
                kind="globalExplorationPaused"
                if current.state == "paused"
                else "globalExplorationResumed",
                severity=EventSeverity.NOTICE,
                entity_type="explorationControl",
                entity_id="global",
                summary="Global exploration control changed",
                payload={
                    **current.model_dump(),
                    "reason": redact_sensitive(request.reason.strip()),
                },
                actor="human:api",
            )
        return current
