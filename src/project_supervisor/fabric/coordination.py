from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Any

type JSONScalar = str | int | float | bool | None
type JSONValue = JSONScalar | list["JSONValue"] | dict[str, "JSONValue"]

COLLECTIVE_MESSAGE_VERSION = "collective-coordination-message/v1"
COLLECTIVE_GATE_VERSION = "collective-coordination-gate/v1"
MAX_COORDINATION_PAYLOAD_BYTES = 16_384


class CoordinationValidationError(ValueError):
    """Raised when an untrusted peer-coordination message is not safely bounded."""


class CoordinationKind(StrEnum):
    INFO = "info"
    QUESTION = "question"
    REQUEST = "request"
    OFFER = "offer"
    RESULT = "result"
    CLAIM = "claim"
    ASSIGN = "assign"
    ACK = "ack"
    HOLD = "hold"
    VETO = "veto"
    STOP = "stop"
    RELEASE = "release"
    GO = "go"


class CoordinationEffect(StrEnum):
    ADVISORY = "advisory"
    PAUSE = "pause"
    BLOCK = "block"


def _require_identifier(value: str, field_name: str, *, maximum: int = 256) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CoordinationValidationError(f"{field_name} must be a non-empty string")
    if len(value) > maximum:
        raise CoordinationValidationError(f"{field_name} exceeds its length limit")
    return value


def _normalize_json(value: Any) -> JSONValue:
    try:
        encoded = json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return json.loads(encoded)
    except (TypeError, ValueError) as error:
        raise CoordinationValidationError(
            "coordination payload must be finite canonical JSON"
        ) from error


def _freeze_json(value: JSONValue) -> Any:
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze_json(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze_json(item) for item in value)
    return value


def _thaw_json(value: Any) -> JSONValue:
    if isinstance(value, Mapping):
        return {str(key): _thaw_json(item) for key, item in sorted(value.items())}
    if isinstance(value, tuple):
        return [_thaw_json(item) for item in value]
    return value


def _canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _normalize_time(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise CoordinationValidationError(f"{field_name} must be timezone-aware")
    return value.astimezone(UTC)


@dataclass(frozen=True, slots=True)
class CollectiveMessage:
    """One bounded, non-authoritative peer-coordination message.

    Sender identity must be bound by the canonical runtime or persistence layer. A model-provided
    sender_worker_id is never sufficient evidence of identity or authority on its own.
    """

    message_id: str
    project_id: str
    sender_worker_id: str
    kind: CoordinationKind
    payload: Mapping[str, Any] = field(default_factory=dict, repr=False, compare=False)
    goal_id: str | None = None
    task_id: str | None = None
    channel: str = "board"
    target_worker_id: str | None = None
    thread_id: str | None = None
    workstream_id: str | None = None
    resource_id: str | None = None
    in_reply_to: str | None = None
    supersedes_message_id: str | None = None
    source_run_id: str | None = None
    priority: int = 0
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    expires_at: datetime | None = None
    schema_version: str = COLLECTIVE_MESSAGE_VERSION
    digest: str = field(init=False)

    def __post_init__(self) -> None:
        for name in ("message_id", "project_id", "sender_worker_id", "channel"):
            _require_identifier(getattr(self, name), name)
        for name in (
            "goal_id",
            "task_id",
            "target_worker_id",
            "thread_id",
            "workstream_id",
            "resource_id",
            "in_reply_to",
            "supersedes_message_id",
            "source_run_id",
        ):
            value = getattr(self, name)
            if value is not None:
                _require_identifier(value, name)
        if self.schema_version != COLLECTIVE_MESSAGE_VERSION:
            raise CoordinationValidationError("unsupported collective message schema version")
        try:
            kind = CoordinationKind(self.kind)
        except (TypeError, ValueError) as error:
            raise CoordinationValidationError("unsupported coordination kind") from error
        object.__setattr__(self, "kind", kind)
        if isinstance(self.priority, bool) or not isinstance(self.priority, int):
            raise CoordinationValidationError("priority must be an integer")
        if not 0 <= self.priority <= 9:
            raise CoordinationValidationError("priority must be between 0 and 9")

        created_at = _normalize_time(self.created_at, "created_at")
        object.__setattr__(self, "created_at", created_at)
        if self.expires_at is not None:
            expires_at = _normalize_time(self.expires_at, "expires_at")
            if expires_at <= created_at:
                raise CoordinationValidationError("expires_at must be later than created_at")
            object.__setattr__(self, "expires_at", expires_at)

        if kind in {
            CoordinationKind.HOLD,
            CoordinationKind.VETO,
            CoordinationKind.STOP,
            CoordinationKind.RELEASE,
            CoordinationKind.GO,
        } and self.workstream_id is None and self.resource_id is None:
            raise CoordinationValidationError(
                "control messages must target a workstream or resource"
            )
        if kind is CoordinationKind.RELEASE and self.supersedes_message_id is None:
            raise CoordinationValidationError("release must reference the hold it supersedes")

        normalized_payload = _normalize_json(dict(self.payload))
        if not isinstance(normalized_payload, dict):
            raise CoordinationValidationError("coordination payload must be a JSON object")
        if any(not key.strip() or len(key) > 256 for key in normalized_payload):
            raise CoordinationValidationError(
                "coordination payload keys must be bounded non-empty strings"
            )
        payload_bytes = _canonical_json_bytes(normalized_payload)
        if len(payload_bytes) > MAX_COORDINATION_PAYLOAD_BYTES:
            raise CoordinationValidationError("coordination payload exceeds its size limit")
        object.__setattr__(self, "payload", _freeze_json(normalized_payload))
        object.__setattr__(
            self,
            "digest",
            hashlib.sha256(\n                _canonical_json_bytes(self.to_protocol(include_digest=False))\n            ).hexdigest(),
        )

    @property
    def canonical_authority(self) -> bool:
        return False

    @property
    def effect(self) -> CoordinationEffect:
        if self.kind is CoordinationKind.HOLD:
            return CoordinationEffect.PAUSE
        if self.kind in {CoordinationKind.VETO, CoordinationKind.STOP}:
            return CoordinationEffect.BLOCK
        return CoordinationEffect.ADVISORY

    def is_expired(self, now: datetime | None = None) -> bool:
        observed = _normalize_time(now or datetime.now(UTC), "now")
        return self.expires_at is not None and observed >= self.expires_at

    def to_protocol(self, *, include_digest: bool = True) -> dict[str, Any]:
        value: dict[str, Any] = {
            "schemaVersion": self.schema_version,
            "messageID": self.message_id,
            "projectID": self.project_id,
            "senderWorkerID": self.sender_worker_id,
            "kind": self.kind.value,
            "payload": _thaw_json(self.payload),
            "goalID": self.goal_id,
            "taskID": self.task_id,
            "channel": self.channel,
            "targetWorkerID": self.target_worker_id,
            "threadID": self.thread_id,
            "workstreamID": self.workstream_id,
            "resourceID": self.resource_id,
            "inReplyTo": self.in_reply_to,
            "supersedesMessageID": self.supersedes_message_id,
            "sourceRunID": self.source_run_id,
            "priority": self.priority,
            "createdAt": _timestamp(self.created_at),
            "expiresAt": _timestamp(self.expires_at) if self.expires_at is not None else None,
        }
        if include_digest:
            value["digest"] = self.digest
        return value


@dataclass(frozen=True, slots=True)
class CoordinationGateDecision:
    """Peer coordination can narrow execution, but it can never grant execution authority."""

    paused: bool
    blocked: bool
    reason_codes: tuple[str, ...]
    active_control_message_ids: tuple[str, ...]
    advisory_message_ids: tuple[str, ...]
    schema_version: str = COLLECTIVE_GATE_VERSION

    @property
    def peer_gate_open(self) -> bool:
        """Whether peer coordination imposes no pause/block.

        This is deliberately not named authorized: canonical authorization, leases, policy,
        verification and runtime state must still independently permit execution.
        """

        return not self.paused and not self.blocked

    @property
    def canonical_authority(self) -> bool:
        return False

    def to_protocol(self) -> dict[str, Any]:
        return {
            "schemaVersion": self.schema_version,
            "paused": self.paused,
            "blocked": self.blocked,
            "peerGateOpen": self.peer_gate_open,
            "reasonCodes": list(self.reason_codes),
            "activeControlMessageIDs": list(self.active_control_message_ids),
            "advisoryMessageIDs": list(self.advisory_message_ids),
            "canonicalAuthority": False,
        }


class CoordinationGate:
    """Reduce peer messages into a deterministic narrow-only coordination gate."""

    @staticmethod
    def evaluate(
        messages: Iterable[CollectiveMessage],
        *,
        project_id: str,
        goal_id: str | None = None,
        task_id: str | None = None,
        target_worker_id: str | None = None,
        workstream_id: str | None = None,
        resource_id: str | None = None,
        now: datetime | None = None,
    ) -> CoordinationGateDecision:
        _require_identifier(project_id, "project_id")
        for name, value in (
            ("goal_id", goal_id),
            ("task_id", task_id),
            ("target_worker_id", target_worker_id),
            ("workstream_id", workstream_id),
            ("resource_id", resource_id),
        ):
            if value is not None:
                _require_identifier(value, name)
        observed = _normalize_time(now or datetime.now(UTC), "now")

        relevant = [
            message
            for message in messages
            if message.project_id == project_id
            and not message.is_expired(observed)
            and CoordinationGate._matches_scope(
                message,
                goal_id=goal_id,
                task_id=task_id,
                target_worker_id=target_worker_id,
                workstream_id=workstream_id,
                resource_id=resource_id,
            )
        ]
        relevant.sort(key=lambda message: (message.created_at, message.message_id))

        by_id = {message.message_id: message for message in relevant}
        released_holds: set[str] = set()
        advisory: list[str] = []

        for message in relevant:
            if message.kind is CoordinationKind.RELEASE:
                prior = by_id.get(message.supersedes_message_id or "")
                if (
                    prior is not None
                    and prior.kind is CoordinationKind.HOLD
                    and prior.sender_worker_id == message.sender_worker_id
                    and prior.created_at <= message.created_at
                ):
                    released_holds.add(prior.message_id)
                advisory.append(message.message_id)
            elif message.effect is CoordinationEffect.ADVISORY:
                advisory.append(message.message_id)

        active: list[CollectiveMessage] = []
        for message in relevant:
            if message.kind is CoordinationKind.HOLD and message.message_id in released_holds:
                continue
            if message.effect is not CoordinationEffect.ADVISORY:
                active.append(message)

        reason_codes: list[str] = []
        if any(message.kind is CoordinationKind.STOP for message in active):
            reason_codes.append("PEER_STOP")
        if any(message.kind is CoordinationKind.VETO for message in active):
            reason_codes.append("PEER_VETO")
        if any(message.kind is CoordinationKind.HOLD for message in active):
            reason_codes.append("PEER_HOLD")

        blocked = "PEER_STOP" in reason_codes or "PEER_VETO" in reason_codes
        paused = "PEER_HOLD" in reason_codes
        return CoordinationGateDecision(
            paused=paused,
            blocked=blocked,
            reason_codes=tuple(reason_codes),
            active_control_message_ids=tuple(message.message_id for message in active),
            advisory_message_ids=tuple(advisory),
        )

    @staticmethod
    def _matches_scope(
        message: CollectiveMessage,
        *,
        goal_id: str | None,
        task_id: str | None,
        target_worker_id: str | None,
        workstream_id: str | None,
        resource_id: str | None,
    ) -> bool:
        for message_value, selected_value in (
            (message.goal_id, goal_id),
            (message.task_id, task_id),
            (message.target_worker_id, target_worker_id),
            (message.workstream_id, workstream_id),
            (message.resource_id, resource_id),
        ):
            if message_value is not None and message_value != selected_value:
                return False
        return True
