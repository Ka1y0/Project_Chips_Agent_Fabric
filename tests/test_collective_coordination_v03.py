from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from project_supervisor.fabric.coordination import (
    MAX_COORDINATION_PAYLOAD_BYTES,
    CollectiveMessage,
    CoordinationGate,
    CoordinationKind,
    CoordinationValidationError,
)

NOW = datetime(2026, 9, 26, 8, 0, tzinfo=UTC)


def _message(identity: str, kind: CoordinationKind, **kwargs) -> CollectiveMessage:
    return CollectiveMessage(
        message_id=identity,
        project_id="project-fabric",
        sender_worker_id=kwargs.pop("sender_worker_id", "worker-a"),
        kind=kind,
        created_at=kwargs.pop("created_at", NOW),
        **kwargs,
    )


def test_digest_is_canonical_for_payload_order() -> None:
    first = _message("same", CoordinationKind.INFO, payload={"b": 2, "a": 1})
    second = _message("same", CoordinationKind.INFO, payload={"a": 1, "b": 2})
    assert first.digest == second.digest


def test_control_message_requires_narrow_scope() -> None:
    with pytest.raises(CoordinationValidationError, match="workstream or resource"):
        _message("hold", CoordinationKind.HOLD)


def test_go_never_clears_hold_or_grants_authority() -> None:
    hold = _message("hold", CoordinationKind.HOLD, workstream_id="research")
    go = _message(
        "go",
        CoordinationKind.GO,
        workstream_id="research",
        created_at=NOW + timedelta(seconds=1),
    )
    decision = CoordinationGate.evaluate(
        [hold, go],
        project_id="project-fabric",
        workstream_id="research",
        now=NOW + timedelta(seconds=2),
    )

    assert decision.paused
    assert not decision.blocked
    assert not decision.peer_gate_open
    assert not decision.canonical_authority
    assert hold.canonical_authority is False
    assert go.canonical_authority is False
    assert decision.reason_codes == ("PEER_HOLD",)
    assert decision.advisory_message_ids == ("go",)


def test_release_clears_only_same_sender_exact_hold() -> None:
    hold = _message("hold", CoordinationKind.HOLD, workstream_id="research")
    wrong_sender = _message(
        "wrong-release",
        CoordinationKind.RELEASE,
        sender_worker_id="worker-b",
        workstream_id="research",
        supersedes_message_id="hold",
        created_at=NOW + timedelta(seconds=1),
    )
    still_paused = CoordinationGate.evaluate(
        [hold, wrong_sender],
        project_id="project-fabric",
        workstream_id="research",
        now=NOW + timedelta(seconds=2),
    )
    assert still_paused.paused

    exact_release = _message(
        "release",
        CoordinationKind.RELEASE,
        workstream_id="research",
        supersedes_message_id="hold",
        created_at=NOW + timedelta(seconds=2),
    )
    released = CoordinationGate.evaluate(
        [hold, wrong_sender, exact_release],
        project_id="project-fabric",
        workstream_id="research",
        now=NOW + timedelta(seconds=3),
    )
    assert released.peer_gate_open
    assert released.reason_codes == ()


def test_release_cannot_clear_veto_or_stop() -> None:
    veto = _message("veto", CoordinationKind.VETO, resource_id="gpu-1")
    release = _message(
        "release",
        CoordinationKind.RELEASE,
        resource_id="gpu-1",
        supersedes_message_id="veto",
        created_at=NOW + timedelta(seconds=1),
    )
    decision = CoordinationGate.evaluate(
        [veto, release],
        project_id="project-fabric",
        resource_id="gpu-1",
        now=NOW + timedelta(seconds=2),
    )
    assert decision.blocked
    assert decision.reason_codes == ("PEER_VETO",)


def test_expired_hold_is_ignored() -> None:
    hold = _message(
        "hold",
        CoordinationKind.HOLD,
        workstream_id="research",
        expires_at=NOW + timedelta(seconds=1),
    )
    decision = CoordinationGate.evaluate(
        [hold],
        project_id="project-fabric",
        workstream_id="research",
        now=NOW + timedelta(seconds=2),
    )
    assert decision.peer_gate_open


def test_scoped_control_does_not_leak_across_workstreams_or_workers() -> None:
    hold = _message(
        "hold",
        CoordinationKind.HOLD,
        workstream_id="research",
        target_worker_id="worker-z",
    )
    other_stream = CoordinationGate.evaluate(
        [hold],
        project_id="project-fabric",
        workstream_id="coding",
        target_worker_id="worker-z",
        now=NOW,
    )
    other_worker = CoordinationGate.evaluate(
        [hold],
        project_id="project-fabric",
        workstream_id="research",
        target_worker_id="worker-y",
        now=NOW,
    )
    exact = CoordinationGate.evaluate(
        [hold],
        project_id="project-fabric",
        workstream_id="research",
        target_worker_id="worker-z",
        now=NOW,
    )

    assert other_stream.peer_gate_open
    assert other_worker.peer_gate_open
    assert exact.paused


def test_large_payload_is_rejected() -> None:
    with pytest.raises(CoordinationValidationError, match="size limit"):
        _message(
            "large",
            CoordinationKind.RESULT,
            payload={"text": "x" * (MAX_COORDINATION_PAYLOAD_BYTES + 1)},
        )


def test_assign_and_result_are_advisory_only() -> None:
    assign = _message("assign", CoordinationKind.ASSIGN, workstream_id="research")
    result = _message("result", CoordinationKind.RESULT, workstream_id="research")
    decision = CoordinationGate.evaluate(
        [assign, result],
        project_id="project-fabric",
        workstream_id="research",
        now=NOW,
    )

    assert decision.peer_gate_open
    assert decision.advisory_message_ids == ("assign", "result")
    assert assign.canonical_authority is False
    assert result.canonical_authority is False
