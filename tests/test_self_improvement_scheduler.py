from __future__ import annotations

import json
from dataclasses import asdict

import pytest

from project_supervisor.scheduler import SchedulerWeights
from project_supervisor.self_improvement import Policy, inspect_archive, run_experiment
from project_supervisor.self_improvement_scheduler import (
    SchedulerReplay,
    candidate_scheduler,
    main,
)


def test_real_scheduler_improves_synthetic_policy_over_two_generations(tmp_path):
    original = SchedulerWeights()
    suite = SchedulerReplay()
    path = tmp_path / "scheduler.db"
    result = run_experiment(suite, path)
    print("SELF_IMPROVEMENT_REPLAY " + json.dumps(result, sort_keys=True))
    assert result["status"] == "awaitingReview"
    assert result["baselineTraining"] == result["baselineHoldout"] == 0.0
    assert result["candidateTraining"] == result["candidateHoldout"] == 1.0
    assert result["acceptedGenerations"] == 2
    assert result["modelCalls"] == 0
    assert result["evidenceKind"] == "syntheticReplay"
    assert not result["productionChanged"] and not result["deployable"]
    assert SchedulerWeights() == original
    assert inspect_archive(path)["summary"] == result


@pytest.mark.parametrize("policy", [Policy(), Policy(16, 0.125, 16), Policy(0.125, 16, 0.125)])
def test_weight_extremes_cannot_change_hard_constraint_rejections(policy):
    suite = SchedulerReplay()
    assert all(suite.measure(policy, "training").guards)
    assert all(suite.measure(policy, "holdout").guards)
    weights = asdict(candidate_scheduler(policy).config.weights)
    for key, original in asdict(SchedulerWeights()).items():
        if key not in {"expected_quality", "monetary_cost", "latency"}:
            assert weights[key] == original


def test_manifest_identifies_fixture_changes():
    suite = SchedulerReplay()
    initial = suite.manifest
    suite.holdout = tuple(reversed(suite.holdout))
    assert suite.manifest.digest != initial.digest


def test_cli_requires_explicit_mode_and_rejects_overwrite(tmp_path, capsys):
    with pytest.raises(SystemExit):
        main(["--database", str(tmp_path / "no-mode.db")])
    assert not (tmp_path / "no-mode.db").exists()
    capsys.readouterr()
    path = tmp_path / "demo.db"
    assert main(["--demo", "--database", str(path)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "awaitingReview"
    assert main(["--inspect", "--database", str(path)]) == 0
    assert json.loads(capsys.readouterr().out)["summary"] == result
    before = path.read_bytes()
    assert main(["--demo", "--database", str(path)]) == 2
    assert json.loads(capsys.readouterr().err)["reason"] == "invalidInputOrArchive"
    assert path.read_bytes() == before


def test_cli_does_not_open_production_database(tmp_path, capsys):
    path = tmp_path / "not-lab.db"
    path.write_bytes(b"untouched operator data")
    assert main(["--inspect", "--database", str(path)]) == 2
    capsys.readouterr()
    assert path.read_bytes() == b"untouched operator data"
