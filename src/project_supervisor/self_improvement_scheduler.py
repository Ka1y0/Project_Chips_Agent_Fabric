"""Offline self-improvement replay through Fabric's actual deterministic scheduler."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import asdict, dataclass, is_dataclass, replace
from datetime import datetime
from enum import Enum
from pathlib import Path

from . import domain, hybrid, scheduler
from .domain import (
    ApprovalState,
    ExecutionTopology,
    Harness,
    ModelDescriptor,
    NodeState,
    PermissionClass,
    Provider,
    ResourceState,
    TaskLabel,
    TaskRequirements,
    WorkerSnapshot,
    WorkerState,
)
from .fabric import capabilities
from .scheduler import DeterministicScheduler, SchedulerConfig, SchedulerWeights
from .self_improvement import (
    LabError,
    Limits,
    Manifest,
    Measurement,
    Policy,
    fingerprint,
    inspect_archive,
    run_experiment,
)


@dataclass(frozen=True, slots=True)
class RoutingCase:
    case_id: str
    requirements: TaskRequirements
    workers: tuple[WorkerSnapshot, ...]
    expected: tuple[str, ...]


def _plain(value):
    if is_dataclass(value):
        return _plain(asdict(value))
    if isinstance(value, dict):
        return {key: _plain(item) for key, item in sorted(value.items())}
    if isinstance(value, (tuple, list)):
        return [_plain(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return sorted(_plain(item) for item in value)
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def candidate_scheduler(policy: Policy) -> DeterministicScheduler:
    """Only three soft weights vary. All hard constraints and other weights stay intact."""
    return DeterministicScheduler(SchedulerConfig(weights=replace(
        SchedulerWeights(), expected_quality=policy.expected_quality,
        monetary_cost=policy.monetary_cost, latency=policy.latency,
    )))


def _worker(identity: str, quality: float, cost: float) -> WorkerSnapshot:
    return WorkerSnapshot(
        id=identity, node_id="fixture-node", harness=Harness.MOCK, provider=Provider.MOCK,
        model=ModelDescriptor("fixture-model", "Fixture", Provider.MOCK,
                              context_window_tokens=4096),
        state=WorkerState.IDLE, node_state=NodeState.ONLINE,
        resource_state=ResourceState.AVAILABLE, capabilities=frozenset({"coding"}),
        code_write_allowed=True, privacy_allowed=True, quality_score=quality,
        monetary_cost_score=cost, expected_latency_seconds=10, reliability_score=0.9,
    )


class SchedulerReplay:
    """Fixed, synthetic fixtures. No inference, network, source edit, or production dispatch."""

    def __init__(self) -> None:
        requirements = TaskRequirements(
            labels=frozenset({TaskLabel.CODING}),
            required_capabilities=frozenset({"coding"}), code_write_required=True,
        )

        def preference(identity: str, quality_gap: float) -> RoutingCase:
            return RoutingCase(identity, requirements, (
                _worker("expert", 0.9, 0.0), _worker("economy", 0.9 - quality_gap, 1.0),
            ), ("expert",))

        self.training = (preference("train-quality-1", 0.30),
                         preference("train-quality-2", 0.15))
        self.holdout = (preference("holdout-quality-1", 0.26),
                        preference("holdout-quality-2", 0.13))
        normal = _worker("guard-worker", 0.9, 1.0)
        self.guards = (
            RoutingCase("guard-red", replace(requirements, permission_class=PermissionClass.RED,
                         approval_state=ApprovalState.PENDING), (normal,), ()),
            RoutingCase("guard-local-write", requirements,
                         (replace(normal, provider=Provider.LOCAL, harness=Harness.LOCAL_WORKER,
                                  code_write_allowed=False),), ()),
            RoutingCase("guard-offline", requirements,
                         (replace(normal, node_state=NodeState.OFFLINE),), ()),
            RoutingCase("guard-quota", requirements,
                         (replace(normal, resource_state=ResourceState.BUDGET_EXHAUSTED),), ()),
            RoutingCase("guard-privacy", replace(requirements, privacy_sensitive=True),
                         (replace(normal, privacy_allowed=False),), ()),
            RoutingCase("guard-capability", requirements,
                         (replace(normal, capabilities=frozenset()),), ()),
            RoutingCase("guard-context", replace(requirements, minimum_context_tokens=8192),
                         (normal,), ()),
        )
        modules = (scheduler, domain, hybrid, capabilities)
        code_hashes = {module.__name__: hashlib.sha256(
            Path(module.__file__).read_bytes()
        ).hexdigest() for module in modules}
        code_hashes[__name__] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
        self._code_hashes = code_hashes

    @property
    def manifest(self) -> Manifest:
        # Recompute fixture identity so even accidental in-process corpus changes abort the run.
        return Manifest(
            tuple(case.case_id for case in self.training),
            tuple(case.case_id for case in self.holdout),
            tuple(case.case_id for case in self.guards),
            fingerprint({"cases": _plain((self.training, self.holdout, self.guards)),
                         "code": self._code_hashes}),
        )

    @staticmethod
    def _decision(engine: DeterministicScheduler, case: RoutingCase):
        return engine.schedule(task_id=case.case_id, requirements=case.requirements,
                               topology=ExecutionTopology.SINGLE, workers=case.workers)

    def measure(self, policy: Policy, partition: str) -> Measurement:
        if partition not in {"training", "holdout"}:
            raise LabError("invalid evaluation partition")
        engine = candidate_scheduler(policy)
        cases = self.training if partition == "training" else self.holdout
        utilities = tuple(float(self._decision(engine, case).selected_worker_ids == case.expected)
                          for case in cases)
        guards = []
        for case in self.guards:
            result = self._decision(engine, case)
            unchanged = self._decision(DeterministicScheduler(), case)
            guards.append(result.selected_worker_ids == case.expected and
                          result.rejected == unchanged.rejected)
        return Measurement(tuple(case.case_id for case in cases), utilities,
                           tuple(case.case_id for case in self.guards), tuple(guards))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Bounded offline scheduler improvement laboratory")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--demo", action="store_true", help="run synthetic scheduler replay only")
    mode.add_argument("--inspect", action="store_true", help="inspect existing research evidence")
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--max-evaluations", type=int, default=28)
    parser.add_argument("--max-generations", type=int, default=4)
    args = parser.parse_args(argv)
    try:
        if args.inspect:
            report = inspect_archive(args.database)
        else:
            report = run_experiment(SchedulerReplay(), args.database, limits=Limits(
                max_evaluations=args.max_evaluations, max_generations=args.max_generations,
            ))
        print(json.dumps(report, allow_nan=False, sort_keys=True))
        status = report.get("status", report.get("summary", {}).get("status"))
        return 0 if status in {"awaitingReview", "noImprovement"} else 1
    except (OSError, LabError):
        print(json.dumps({"status": "error", "reason": "invalidInputOrArchive",
                          "productionChanged": False}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
