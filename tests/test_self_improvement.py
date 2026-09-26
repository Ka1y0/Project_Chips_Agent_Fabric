from __future__ import annotations

import sqlite3
from dataclasses import replace

import pytest

from project_supervisor.self_improvement import (
    Journal,
    LabError,
    Limits,
    Manifest,
    Measurement,
    Policy,
    inspect_archive,
    run_experiment,
)


class Fixture:
    manifest = Manifest(("t1", "t2"), ("h1", "h2"), ("safe",), "a" * 64)

    def __init__(self, mode="normal"):
        self.calls = []
        self.mode = mode

    def measure(self, policy, partition):
        self.calls.append((policy, partition))
        ratio = policy.expected_quality / policy.monetary_cost
        values = (float(ratio > 3), float(ratio > 6))
        if self.mode == "flat":
            values = (0.0, 0.0)
        if self.mode == "holdout" and partition == "holdout":
            values = (float(policy == Policy()),) * 2
        if self.mode == "error":
            raise RuntimeError("private detail must not escape")
        ids = self.manifest.training_ids if partition == "training" else self.manifest.holdout_ids
        return Measurement(ids, values, self.manifest.guard_ids, (self.mode != "unsafe",))


def test_two_generations_feed_selected_policy_back_and_holdout_is_final(tmp_path):
    fixture = Fixture()
    path = tmp_path / "lab.db"
    result = run_experiment(fixture, path)
    assert result["status"] == "awaitingReview"
    assert result["baselineTraining"] == 0.0
    assert result["candidateTraining"] == 1.0
    assert result["acceptedGenerations"] == 2
    assert [part for _, part in fixture.calls[-2:]] == ["holdout", "holdout"]
    assert all(part == "training" for _, part in fixture.calls[:-2])
    assert result["evaluations"] == len(fixture.calls) <= 28
    assert result["productionChanged"] is result["deployable"] is False
    events = inspect_archive(path)["events"]
    selected = [event["payload"] for event in events if event["kind"] == "generationSelected"]
    assert selected[1]["parentDigest"] == selected[0]["candidateDigest"]
    assert inspect_archive(path)["summary"] == result


@pytest.mark.parametrize("mode,status", [("flat", "noImprovement"),
    ("holdout", "rejectedHoldout"), ("unsafe", "stopped"), ("error", "failed")])
def test_failures_do_not_become_deployment_permission(tmp_path, mode, status):
    path = tmp_path / "lab.db"
    result = run_experiment(Fixture(mode), path)
    assert result["status"] == status
    assert not result["deployable"]
    assert "private detail" not in str(inspect_archive(path))


@pytest.mark.parametrize("value", [True, None, "1", -1, 0, 17, float("inf"), float("nan")])
def test_policy_bounds(value):
    with pytest.raises(LabError):
        Policy(expected_quality=value)


@pytest.mark.parametrize("changes", [{"max_evaluations": 2}, {"max_evaluations": 129},
    {"max_evaluations": True}, {"max_generations": 0}, {"max_generations": 17},
    {"min_gain": 0}, {"min_gain": float("nan")}, {"max_seconds": 0}])
def test_invalid_limits(changes):
    with pytest.raises(LabError):
        Limits(**changes)


def test_budget_includes_baseline_and_reserves_holdout(tmp_path):
    fixture = Fixture()
    result = run_experiment(fixture, tmp_path / "lab.db", limits=Limits(max_evaluations=4))
    assert len(fixture.calls) <= 4
    assert result["evaluations"] == len(fixture.calls)


def test_stop_before_start_does_not_evaluate(tmp_path):
    fixture = Fixture()
    result = run_experiment(fixture, tmp_path / "lab.db", stopped=lambda: True)
    assert result["status"] == "stopped"
    assert fixture.calls == []


def test_stop_during_evaluation_prevents_candidate_acceptance(tmp_path):
    fixture = Fixture()
    result = run_experiment(fixture, tmp_path / "lab.db", stopped=lambda: len(fixture.calls) > 1)
    assert result["status"] == "stopped"
    assert result["acceptedGenerations"] == 0


def test_manifest_drift_is_not_an_improvement(tmp_path):
    class Drifting(Fixture):
        def measure(self, policy, partition):
            result = super().measure(policy, partition)
            self.manifest = replace(self.manifest, source_digest="b" * 64)
            return result
    result = run_experiment(Drifting(), tmp_path / "lab.db")
    assert result["status"] == "failed"


def test_case_level_regression_cannot_hide_in_mean():
    original = Measurement(("a", "b"), (1.0, 0.0), ("safe",), (True,))
    candidate = replace(original, utilities=(0.9, 1.0))
    assert candidate.score > original.score
    assert not candidate.dominates(original)


@pytest.mark.parametrize("changes", [{"guards": (None,)}, {"utilities": (1.0,)},
    {"utilities": (float("nan"), 0.0)}, {"guards": ()}])
def test_measurements_are_complete(changes):
    with pytest.raises(LabError):
        replace(Measurement(("a", "b"), (1.0, 0.0), ("safe",), (True,)), **changes)


def test_no_overwrite_and_read_does_not_create(tmp_path):
    path = tmp_path / "lab.db"
    with pytest.raises(LabError):
        inspect_archive(path)
    assert not path.exists()
    run_experiment(Fixture(), path)
    before = path.read_bytes()
    with pytest.raises(FileExistsError):
        run_experiment(Fixture(), path)
    inspect_archive(path)
    assert before == path.read_bytes()


def test_interrupted_archive_is_observed_not_reexecuted(tmp_path):
    path = tmp_path / "lab.db"
    journal = Journal(path)
    journal.append("evaluationIntent", {"evaluation": 1})
    journal.close()
    assert inspect_archive(path)["summary"]["status"] == "interrupted"


def test_journal_records_are_append_only(tmp_path):
    path = tmp_path / "lab.db"
    run_experiment(Fixture(), path)
    connection = sqlite3.connect(path)
    try:
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("DELETE FROM records")
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("UPDATE records SET payload='{}'")
    finally:
        connection.close()


def test_repeated_experiments_have_same_semantic_result(tmp_path):
    first = run_experiment(Fixture(), tmp_path / "a.db")
    second = run_experiment(Fixture(), tmp_path / "b.db")
    assert first == second


def test_duplicate_training_and_holdout_ids_rejected():
    with pytest.raises(LabError):
        Manifest(("same",), ("same",), ("safe",), "a" * 64)


def test_unsafe_candidate_is_retained_but_not_selected(tmp_path):
    class UnsafeChild(Fixture):
        def measure(self, policy, partition):
            result = super().measure(policy, partition)
            return replace(result, guards=(policy == Policy(),))
    path = tmp_path / "lab.db"
    result = run_experiment(UnsafeChild(), path)
    assert result["status"] == "noImprovement"
    assert result["acceptedGenerations"] == 0
    assert any(event["payload"]["measurement"]["guards"] == [False]
               for event in inspect_archive(path)["events"] if event["kind"] == "evaluated")


def test_missing_case_cannot_supply_a_high_score(tmp_path):
    class Partial(Fixture):
        def measure(self, policy, partition):
            return Measurement(("t1",), (1.0,), ("safe",), (True,))
    result = run_experiment(Partial(), tmp_path / "lab.db")
    assert result["status"] == "failed"


def test_time_limit_after_callback_does_not_accept_late_result(tmp_path, monkeypatch):
    from project_supervisor import self_improvement
    clock = [0.0]
    monkeypatch.setattr(self_improvement.time, "monotonic", lambda: clock[0])
    class Slow(Fixture):
        def measure(self, policy, partition):
            clock[0] = 31.0
            return super().measure(policy, partition)
    result = run_experiment(Slow(), tmp_path / "lab.db")
    assert result["status"] == "stopped"
    assert result["terminationReason"] == "timeLimit"
    assert result["baselineTraining"] is None


def test_symlink_archive_is_rejected(tmp_path):
    path, link = tmp_path / "lab.db", tmp_path / "link.db"
    run_experiment(Fixture(), path)
    link.symlink_to(path)
    with pytest.raises(LabError):
        inspect_archive(link)
    with pytest.raises(FileExistsError):
        run_experiment(Fixture(), link)
