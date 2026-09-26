from __future__ import annotations

import hmac
import json
import re
from datetime import datetime
from typing import Any

from project_supervisor.fabric.coordination import (
    CollectiveMessage,
    CoordinationValidationError,
)

MAX_COORDINATION_WIRE_BYTES = 49_152
_FIELDS = {
    "schemaVersion": "schema_version",
    "messageID": "message_id",
    "projectID": "project_id",
    "senderWorkerID": "sender_worker_id",
    "kind": "kind",
    "payload": "payload",
    "goalID": "goal_id",
    "taskID": "task_id",
    "channel": "channel",
    "targetWorkerID": "target_worker_id",
    "threadID": "thread_id",
    "workstreamID": "workstream_id",
    "resourceID": "resource_id",
    "inReplyTo": "in_reply_to",
    "supersedesMessageID": "supersedes_message_id",
    "sourceRunID": "source_run_id",
    "priority": "priority",
    "createdAt": "created_at",
    "expiresAt": "expires_at",
}
_TIMESTAMP = re.compile(
    r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})"
)


def _object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise CoordinationValidationError("coordination wire object contains duplicate keys")
        value[key] = item
    return value


def _constant(_: str) -> None:
    raise CoordinationValidationError("coordination wire must contain finite JSON")


def _parse_time(value: Any) -> datetime:
    if (
        not isinstance(value, str)
        or _TIMESTAMP.fullmatch(value) is None
        or value.endswith("-00:00")
    ):
        raise CoordinationValidationError(
            "coordination wire timestamp must have a known UTC offset"
        )
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, OverflowError) as error:
        raise CoordinationValidationError(
            "coordination wire contains an invalid timestamp"
        ) from error


def decode_collective_message(wire: str | bytes) -> CollectiveMessage:
    """Decode one complete v1 envelope, without authenticating or authorizing its claimed sender.

    A verified digest proves only canonical-content consistency. Runtime integration must bind
    sender/project/run/task identity to its own authenticated context before admitting this object.
    No transport, credential lookup, canonical-state mutation, or dispatch occurs here.
    """
    if not isinstance(wire, (str, bytes)):
        raise CoordinationValidationError("coordination wire must be UTF-8 text or bytes")
    if len(wire) > MAX_COORDINATION_WIRE_BYTES:
        raise CoordinationValidationError("coordination wire exceeds its size limit")
    try:
        if isinstance(wire, bytes):
            text = wire.decode("utf-8")
        else:
            if len(wire.encode("utf-8")) > MAX_COORDINATION_WIRE_BYTES:
                raise CoordinationValidationError("coordination wire exceeds its size limit")
            text = wire
        value = json.loads(text, object_pairs_hook=_object, parse_constant=_constant)
    except CoordinationValidationError:
        raise
    except (TypeError, ValueError, RecursionError) as error:
        raise CoordinationValidationError(
            "coordination wire contains invalid JSON or UTF-8"
        ) from error
    if not isinstance(value, dict) or set(value) != set(_FIELDS) | {"digest"}:
        raise CoordinationValidationError(
            "coordination wire has missing or unknown envelope fields"
        )
    digest = value["digest"]
    if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
        raise CoordinationValidationError("coordination wire digest must be lowercase SHA-256")
    fields = {attribute: value[key] for key, attribute in _FIELDS.items()}
    fields["created_at"] = _parse_time(value["createdAt"])
    fields["expires_at"] = (
        None if value["expiresAt"] is None else _parse_time(value["expiresAt"])
    )
    result = CollectiveMessage(**fields)
    if not hmac.compare_digest(digest, result.digest):
        raise CoordinationValidationError(
            "coordination wire digest does not match canonical content"
        )
    return result
