from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker

from project_supervisor.fabric.coordination import (
    CollectiveMessage,
    CoordinationKind,
    CoordinationValidationError,
)
from project_supervisor.fabric.coordination_wire import (
    MAX_COORDINATION_WIRE_BYTES,
    decode_collective_message,
)


def envelope():
    return CollectiveMessage(
        message_id="message", project_id="project", sender_worker_id="worker",
        source_run_id="run", kind=CoordinationKind.INFO,
        payload={"text": "中文", "items": [1, True, None]},
        created_at=datetime(2026, 9, 26, 9, tzinfo=UTC),
    ).to_protocol()


def encode(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


@pytest.mark.parametrize("as_bytes", [False, True])
def test_round_trip_preserves_canonical_digest_and_non_authority(as_bytes):
    value = envelope()
    wire = encode(value)
    result = decode_collective_message(wire.encode("utf-8") if as_bytes else wire)
    assert result.to_protocol() == value
    assert not result.canonical_authority


@pytest.mark.parametrize("field", ["senderWorkerID", "sourceRunID", "projectID", "payload"])
def test_tampering_without_a_new_digest_is_rejected(field):
    value = envelope()
    value[field] = {"text": "modified"} if field == "payload" else "modified"
    with pytest.raises(CoordinationValidationError, match="digest"):
        decode_collective_message(encode(value))


@pytest.mark.parametrize("field,value", [
    ("authorization", "approved"), ("canonicalAuthority", True), ("admin", True),
])
def test_unknown_authority_fields_are_rejected(field, value):
    data = envelope()
    data[field] = value
    with pytest.raises(CoordinationValidationError, match="fields"):
        decode_collective_message(encode(data))


@pytest.mark.parametrize("missing", ["digest", "schemaVersion", "sourceRunID", "expiresAt"])
def test_complete_envelope_is_required(missing):
    value = envelope()
    del value[missing]
    with pytest.raises(CoordinationValidationError, match="fields"):
        decode_collective_message(encode(value))


@pytest.mark.parametrize("wire", [
    '{"same":1,"same":2}', '{"nested":{"same":1,"same":2}}',
    '{"same":1,"sa\\u006de":2}',
])
def test_duplicate_json_keys_are_never_last_writer_wins(wire):
    with pytest.raises(CoordinationValidationError, match="duplicate"):
        decode_collective_message(wire)


@pytest.mark.parametrize("invalid", [
    b"\xff", "\ud800", "not json", "[]", "null", "true", "[NaN]", "[Infinity]",
    {"already": "decoded"}, b"x" * (MAX_COORDINATION_WIRE_BYTES + 1),
    "[" * 2000 + "]" * 2000,
])
def test_invalid_wire_fails_with_a_bounded_validation_error(invalid):
    with pytest.raises(CoordinationValidationError):
        decode_collective_message(invalid)


@pytest.mark.parametrize("created_at", [
    "2026-09-26", "2026-09-26T09:00:00", "2026-09-26T09:00:00-00:00",
    "2026-09-26T09:00:00.0000001Z", "2026-99-99T09:00:00Z", 42,
])
def test_invalid_or_ambiguous_timestamp_is_rejected(created_at):
    value = envelope()
    value["createdAt"] = created_at
    with pytest.raises(CoordinationValidationError, match="timestamp"):
        decode_collective_message(encode(value))


def test_equivalent_known_timezone_offset_preserves_canonical_digest():
    value = envelope()
    value["createdAt"] = "2026-09-26T11:00:00+02:00"
    assert decode_collective_message(encode(value)).created_at.hour == 9


def test_valid_digest_is_not_authentication():
    # Any caller can calculate a hash. Decoding a self-consistent claimed identity must not
    # manufacture an authenticated principal or an authorization grant.
    value = envelope()
    assert not decode_collective_message(encode(value)).canonical_authority


def test_schema_is_closed_and_accepts_all_serialized_kinds():
    path = Path(__file__).resolve().parents[1] / "schemas"
    schema = json.loads((path / "collective-coordination-message-v1.schema.json").read_text())
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    for kind in CoordinationKind:
        value = CollectiveMessage(
            message_id="message", project_id="project", sender_worker_id="worker",
            kind=kind, workstream_id="stream", created_at=datetime(2026, 9, 26, tzinfo=UTC),
            supersedes_message_id="hold" if kind is CoordinationKind.RELEASE else None,
        ).to_protocol()
        validator.validate(value)
        assert decode_collective_message(encode(value)).kind is kind
    invalid = envelope() | {"authority": "approved"}
    assert not validator.is_valid(invalid)
    invalid = envelope() | {"priority": True}
    assert not validator.is_valid(invalid)
