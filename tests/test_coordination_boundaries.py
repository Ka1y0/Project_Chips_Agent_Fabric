from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from itertools import permutations

import pytest

from project_supervisor.fabric.coordination import (
    CollectiveMessage,
    CoordinationGate,
    CoordinationKind,
    CoordinationValidationError,
)

NOW = datetime(2026, 9, 26, 9, tzinfo=UTC)


def message(identity="hold", kind=CoordinationKind.HOLD, **changes):
    values = dict(
        message_id=identity,
        project_id="project",
        sender_worker_id="worker",
        kind=kind,
        workstream_id="stream",
        source_run_id="run",
        created_at=NOW,
    )
    values.update(changes)
    return CollectiveMessage(**values)


def evaluate(messages, **changes):
    values = dict(project_id="project", workstream_id="stream", now=NOW + timedelta(seconds=10))
    values.update(changes)
    return CoordinationGate.evaluate(messages, **values)


@pytest.mark.parametrize(
    "changed_scope",
    [
        {"channel": "another-channel"},
        {"source_run_id": "another-run"},
        {"source_run_id": None},
        {"goal_id": "goal"},
        {"task_id": "task"},
        {"target_worker_id": "recipient"},
        {"resource_id": "resource"},
    ],
)
def test_release_requires_exact_scope_and_run(changed_scope):
    hold = message()
    release = message(
        "release", CoordinationKind.RELEASE, supersedes_message_id="hold",
        created_at=NOW + timedelta(seconds=1), **changed_scope,
    )
    selection = {key: value for key, value in changed_scope.items()
                 if key not in {"channel", "source_run_id"}}
    assert evaluate([hold, release], **selection).paused


def test_timestamp_tie_is_not_proof_of_a_later_release():
    hold = message()
    release = message("release", CoordinationKind.RELEASE, supersedes_message_id="hold")
    assert evaluate([hold, release]).paused


def test_future_release_fails_closed_instead_of_opening_gate():
    hold = message()
    release = message(
        "release", CoordinationKind.RELEASE, supersedes_message_id="hold",
        created_at=NOW + timedelta(days=1),
    )
    with pytest.raises(CoordinationValidationError, match="future"):
        evaluate([hold, release])


def test_expired_release_does_not_resurrect_its_hold():
    hold = message()
    release = message(
        "release", CoordinationKind.RELEASE, supersedes_message_id="hold",
        created_at=NOW + timedelta(seconds=1), expires_at=NOW + timedelta(seconds=2),
    )
    result = evaluate([hold, release])
    assert result.peer_gate_open
    assert "release" not in result.advisory_message_ids


@pytest.mark.parametrize("changes", [
    {"expires_at": NOW + timedelta(seconds=1)},
    {"workstream_id": "elsewhere"},
    {"target_worker_id": "elsewhere"},
])
def test_conflicting_replay_cannot_hide_behind_filter(changes):
    hold = message()
    conflict = replace(hold, **changes)
    with pytest.raises(CoordinationValidationError, match="replay"):
        evaluate([hold, conflict])


def test_batch_limit_counts_foreign_messages_and_exact_duplicates():
    irrelevant = message(project_id="foreign")
    consumed = []

    def bounded_source():
        for index in range(1026):
            consumed.append(index)
            yield irrelevant

    with pytest.raises(CoordinationValidationError, match="batch"):
        evaluate(bounded_source())
    assert len(consumed) == 1025


@pytest.mark.parametrize("bad", [False, 0, "2026-09-26", datetime(2026, 9, 26)])
def test_invalid_evaluation_clock_is_a_validation_error(bad):
    with pytest.raises(CoordinationValidationError):
        evaluate([], now=bad)


@pytest.mark.parametrize("bad", [False, 0, "2026-09-26"])
def test_invalid_message_clock_is_a_validation_error(bad):
    with pytest.raises(CoordinationValidationError):
        message(created_at=bad)


@pytest.mark.parametrize("bad", [
    [("text", "silently coerced")],
    {1: "numeric key"},
    {"nested": {1: "numeric nested key"}},
    {"nested": {"": "empty key"}},
    {"nested": {"k" * 257: "long key"}},
    {"value": "\ud800"},
    {"value": float("nan")},
    {"value": float("inf")},
    {"value": object()},
])
def test_payload_is_strict_bounded_json(bad):
    with pytest.raises(CoordinationValidationError):
        message(payload=bad)


def test_cyclic_payload_is_a_validation_error():
    cyclic = []
    cyclic.append(cyclic)
    with pytest.raises(CoordinationValidationError):
        message(payload={"cycle": cyclic})


def test_deep_payload_is_rejected_before_unbounded_recursion():
    value = "leaf"
    for _ in range(100):
        value = [value]
    with pytest.raises(CoordinationValidationError, match="depth"):
        message(payload={"nested": value})


def test_payload_node_limit_applies_to_tiny_items():
    with pytest.raises(CoordinationValidationError, match="node"):
        message(payload={"items": [0] * 2049})


def test_non_message_input_is_not_silently_accepted():
    with pytest.raises(CoordinationValidationError):
        evaluate([{}])


def test_expiration_boundary_and_go_remain_advisory():
    hold = message(expires_at=NOW + timedelta(seconds=10))
    go = message("go", CoordinationKind.GO)
    assert evaluate([hold, go], now=NOW + timedelta(seconds=9)).paused
    result = evaluate([hold, go])
    assert result.peer_gate_open and not result.canonical_authority


def test_input_order_and_exact_replay_do_not_change_result():
    hold = message()
    release = message(
        "release", CoordinationKind.RELEASE, supersedes_message_id="hold",
        created_at=NOW + timedelta(seconds=1),
    )
    stop = message("stop", CoordinationKind.STOP)
    results = [evaluate(items).to_protocol() for items in permutations([hold, release, stop])]
    assert all(result == results[0] for result in results)
    assert results[0]["blocked"]
    assert evaluate([hold, release, stop, hold]).to_protocol() == results[0]


def test_payload_is_detached_from_caller_and_output_mutations():
    payload = {"items": [{"answer": 42}]}
    value = message(payload=payload)
    digest = value.digest
    payload["items"][0]["answer"] = 0
    output = value.to_protocol()
    output["payload"]["items"][0]["answer"] = -1
    assert value.to_protocol()["payload"] == {"items": [{"answer": 42}]}
    assert value.digest == digest


@pytest.mark.parametrize("field", ["message_id", "channel", "source_run_id"])
def test_invalid_utf8_identifiers_have_a_validation_error(field):
    with pytest.raises(CoordinationValidationError):
        message(**{field: "\ud800"})


def test_non_iterable_batch_has_a_validation_error():
    with pytest.raises(CoordinationValidationError, match="iterable"):
        evaluate(None)


def test_payload_exact_canonical_byte_boundary():
    # {"x":"..."} contributes eight bytes of JSON framing.
    accepted = message(payload={"x": "x" * (16_384 - 8)})
    assert len(accepted.to_protocol()["payload"]["x"]) == 16_384 - 8
    with pytest.raises(CoordinationValidationError, match="size"):
        message(payload={"x": "x" * (16_384 - 7)})
    with pytest.raises(CoordinationValidationError, match="size"):
        message(payload={"x": "中" * 6000})
