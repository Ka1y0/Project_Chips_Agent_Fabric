"""Bounded offline policy improvement. Never mutates or authorizes production execution."""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import sqlite3
import time
from collections.abc import Callable
from contextlib import closing
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Protocol

VERSION = "self-improvement-lab/v1"
MAX_EVENTS = 300
MAX_EVENT_BYTES = 65536
APPLICATION_ID = 0x4346494C


class LabError(ValueError):
    """Bounded, content-free experiment validation error."""


def _number(value: object, low: float, high: float) -> float:
    if type(value) not in (int, float):
        raise LabError("expected a finite number in the permitted range")
    try:
        result = float(value)
    except OverflowError as error:
        raise LabError("number out of range") from error
    if not math.isfinite(result) or not low <= result <= high:
        raise LabError("number out of range")
    return result


def _json(value: object) -> str:
    return json.dumps(value, allow_nan=False, sort_keys=True, separators=(",", ":"))


def fingerprint(value: object) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _identifiers(values: tuple[str, ...]) -> None:
    if not isinstance(values, tuple) or not 1 <= len(values) <= 128:
        raise LabError("case identifiers must be a bounded non-empty tuple")
    if any(not isinstance(x, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,96}", x)
           for x in values) or len(set(values)) != len(values):
        raise LabError("invalid or duplicate case identifiers")


@dataclass(frozen=True, slots=True)
class Policy:
    expected_quality: float = 2.0
    monetary_cost: float = 1.0
    latency: float = 1.0

    def __post_init__(self) -> None:
        for name in ("expected_quality", "monetary_cost", "latency"):
            object.__setattr__(self, name, _number(getattr(self, name), 0.125, 16.0))

    @property
    def digest(self) -> str:
        return fingerprint(asdict(self))

    def neighbours(self) -> tuple[Policy, ...]:
        candidates = []
        for name in ("expected_quality", "monetary_cost", "latency"):
            for multiplier in (0.5, 2.0):
                value = getattr(self, name) * multiplier
                if 0.125 <= value <= 16.0:
                    candidates.append(replace(self, **{name: value}))
        return tuple(sorted(candidates, key=lambda candidate: candidate.digest))


@dataclass(frozen=True, slots=True)
class Limits:
    max_evaluations: int = 28
    max_generations: int = 4
    min_gain: float = 0.001
    max_seconds: float = 30.0

    def __post_init__(self) -> None:
        for name, low, high in (("max_evaluations", 3, 128), ("max_generations", 1, 16)):
            value = getattr(self, name)
            if type(value) is not int or not low <= value <= high:
                raise LabError("invalid search budget")
        _number(self.min_gain, 0.000001, 1.0)
        _number(self.max_seconds, 0.001, 300.0)


@dataclass(frozen=True, slots=True)
class Manifest:
    training_ids: tuple[str, ...]
    holdout_ids: tuple[str, ...]
    guard_ids: tuple[str, ...]
    source_digest: str
    evidence_kind: str = "syntheticReplay"

    def __post_init__(self) -> None:
        for values in (self.training_ids, self.holdout_ids, self.guard_ids):
            _identifiers(values)
        all_ids = self.training_ids + self.holdout_ids + self.guard_ids
        if len(set(all_ids)) != len(all_ids):
            raise LabError("training, holdout and guard identifiers must be disjoint")
        if not isinstance(self.source_digest, str) or not re.fullmatch(
            r"[0-9a-f]{64}", self.source_digest
        ):
            raise LabError("source digest must be SHA-256")
        if self.evidence_kind not in {"syntheticReplay", "operatorReviewedReplay"}:
            raise LabError("unsupported evidence kind")

    @property
    def digest(self) -> str:
        return fingerprint(asdict(self))


@dataclass(frozen=True, slots=True)
class Measurement:
    case_ids: tuple[str, ...]
    utilities: tuple[float, ...]
    guard_ids: tuple[str, ...]
    guards: tuple[bool, ...]

    def __post_init__(self) -> None:
        _identifiers(self.case_ids)
        _identifiers(self.guard_ids)
        if not isinstance(self.utilities, tuple) or len(self.utilities) != len(self.case_ids):
            raise LabError("incomplete utility measurements")
        object.__setattr__(self, "utilities", tuple(_number(x, 0.0, 1.0) for x in self.utilities))
        if not isinstance(self.guards, tuple) or len(self.guards) != len(self.guard_ids):
            raise LabError("incomplete guard measurements")
        if any(type(value) is not bool for value in self.guards):
            raise LabError("guard results must be explicit booleans")

    @property
    def score(self) -> float:
        return math.fsum(self.utilities) / len(self.utilities)

    def dominates(self, baseline: Measurement, gain: float = 0.0) -> bool:
        return (
            self.case_ids == baseline.case_ids
            and self.guard_ids == baseline.guard_ids
            and all(self.guards) and all(baseline.guards)
            and all(a >= b for a, b in zip(self.utilities, baseline.utilities, strict=True))
            and self.score >= baseline.score + gain
        )


class Evaluator(Protocol):
    """Trusted offline evaluator, never supplied or modified by a policy candidate."""

    @property
    def manifest(self) -> Manifest: ...

    def measure(self, policy: Policy, partition: str) -> Measurement: ...


class Journal:
    """One exclusive research archive, not Supervisor's authoritative database."""

    def __init__(self, path: Path) -> None:
        path = Path(path)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(fd)
        self.connection = sqlite3.connect(path, timeout=2.0)
        try:
            self.connection.execute(f"PRAGMA application_id={APPLICATION_ID}")
            self.connection.execute("PRAGMA synchronous=FULL")
            self.connection.execute(
                "CREATE TABLE records(sequence INTEGER PRIMARY KEY, kind TEXT NOT NULL, "
                "payload TEXT NOT NULL, digest TEXT NOT NULL)"
            )
            for action in ("UPDATE", "DELETE"):
                self.connection.execute(
                    f"CREATE TRIGGER no_{action.lower()} BEFORE {action} ON records "
                    "BEGIN SELECT RAISE(ABORT, 'research records are append-only'); END"
                )
            self.connection.commit()
        except BaseException:
            self.connection.close()
            raise
        self.sequence = 0
        self.digest = "0" * 64

    def append(self, kind: str, payload: dict) -> None:
        text = _json(payload)
        if self.sequence >= MAX_EVENTS or len(text.encode("utf-8")) > MAX_EVENT_BYTES:
            raise LabError("research journal limit exceeded")
        sequence = self.sequence + 1
        digest = fingerprint([self.digest, sequence, kind, payload])
        with self.connection:
            self.connection.execute(
                "INSERT INTO records VALUES(?,?,?,?)", (sequence, kind, text, digest)
            )
        self.sequence, self.digest = sequence, digest

    def close(self) -> None:
        self.connection.close()


def inspect_archive(path: Path) -> dict:
    """Read a complete bounded archive; an unfinished run is never resumed implicitly."""
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise LabError("expected an existing regular research archive")
    uri = path.resolve().as_uri() + "?mode=ro"
    try:
        with closing(sqlite3.connect(uri, uri=True, timeout=2.0)) as connection:
            connection.execute("PRAGMA query_only=ON")
            if connection.execute("PRAGMA application_id").fetchone()[0] != APPLICATION_ID:
                raise LabError("not a self-improvement archive")
            objects = connection.execute(
                "SELECT type FROM sqlite_master WHERE name='records'"
            ).fetchone()
            if objects != ("table",):
                raise LabError("invalid research archive schema")
            rows = connection.execute(
                "SELECT sequence,kind,substr(payload,1,?),length(payload),digest "
                "FROM records ORDER BY sequence LIMIT ?", (MAX_EVENT_BYTES + 1, MAX_EVENTS + 1)
            ).fetchall()
        if len(rows) > MAX_EVENTS:
            raise LabError("research archive too large")
        previous, events = "0" * 64, []
        for index, (sequence, kind, text, length, digest) in enumerate(rows, 1):
            if sequence != index or length > MAX_EVENT_BYTES:
                raise LabError("invalid research journal bounds")
            payload = json.loads(text)
            if fingerprint([previous, sequence, kind, payload]) != digest:
                raise LabError("research journal integrity mismatch")
            events.append({"sequence": sequence, "kind": kind, "payload": payload})
            previous = digest
        summary = events[-1]["payload"] if events and events[-1]["kind"] == "finished" else {
            "status": "interrupted", "productionChanged": False, "deployable": False
        }
        return {"schemaVersion": VERSION, "summary": summary, "events": events}
    except (sqlite3.Error, TypeError, json.JSONDecodeError, RecursionError) as error:
        raise LabError("unreadable research archive") from error


class _Halt(Exception):
    def __init__(self, reason: str) -> None:
        self.reason = reason


def run_experiment(
    evaluator: Evaluator,
    database: Path,
    *,
    initial: Policy | None = None,
    limits: Limits | None = None,
    stopped: Callable[[], bool] = lambda: False,
) -> dict:
    """Improve only experimental policies; holdout is evaluated once after search is frozen."""
    initial = Policy() if initial is None else initial
    limits = Limits() if limits is None else limits
    if type(initial) is not Policy or type(limits) is not Limits:
        raise LabError("invalid policy or limits")
    manifest = evaluator.manifest
    if type(manifest) is not Manifest:
        raise LabError("invalid evaluator manifest")
    started = time.monotonic()
    count, generations = 0, 0
    champion, baseline, current = initial, None, None
    held_initial, held_champion = None, None
    reason, status = "noProgress", "noImprovement"
    with closing(Journal(database)) as journal:
        journal.append("started", {"schemaVersion": VERSION, "manifest": asdict(manifest),
                                   "initial": asdict(initial), "limits": asdict(limits)})

        def checkpoint() -> None:
            if stopped():
                raise _Halt("stopped")
            if time.monotonic() - started >= limits.max_seconds:
                raise _Halt("timeLimit")
            if evaluator.manifest != manifest:
                raise LabError("evaluator manifest changed")

        def measure(policy: Policy, partition: str) -> Measurement:
            nonlocal count
            checkpoint()
            if count >= limits.max_evaluations:
                raise _Halt("evaluationLimit")
            count += 1
            journal.append("evaluationIntent", {"evaluation": count, "partition": partition,
                                              "policy": asdict(policy), "digest": policy.digest})
            result = evaluator.measure(policy, partition)
            checkpoint()
            expected = manifest.training_ids if partition == "training" else manifest.holdout_ids
            if type(result) is not Measurement or result.case_ids != expected or (
                result.guard_ids != manifest.guard_ids
            ):
                raise LabError("evaluator returned an incomplete or mismatched result")
            journal.append("evaluated", {"evaluation": count, "measurement": asdict(result)})
            return result

        try:
            baseline = current = measure(initial, "training")
            if not all(baseline.guards):
                raise _Halt("baselineGuardFailed")
            seen = {initial.digest}
            for generation in range(1, limits.max_generations + 1):
                checkpoint()
                parent, best, best_result = champion, champion, current
                reason = "noProgress"
                for candidate in parent.neighbours():
                    if candidate.digest in seen:
                        continue
                    # Reserve two evaluations for a single final baseline/champion holdout check.
                    if count >= limits.max_evaluations - 2:
                        reason = "evaluationLimit"
                        break
                    seen.add(candidate.digest)
                    result = measure(candidate, "training")
                    if result.dominates(current, limits.min_gain) and (
                        best == parent or result.score > best_result.score
                    ):
                        best, best_result = candidate, result
                if best == parent:
                    break
                champion, current, generations = best, best_result, generation
                journal.append("generationSelected", {"generation": generation,
                    "parentDigest": parent.digest, "candidateDigest": champion.digest,
                    "policy": asdict(champion), "trainingScore": current.score})
                if reason == "evaluationLimit":
                    break
                reason = "generationLimit"
            if champion != initial:
                # No holdout feedback can influence any later proposal in this experiment.
                journal.append("searchFrozen", {"candidateDigest": champion.digest})
                held_initial = measure(initial, "holdout")
                held_champion = measure(champion, "holdout")
                status = "awaitingReview" if held_champion.dominates(held_initial) else (
                    "rejectedHoldout"
                )
            checkpoint()
        except _Halt as halt:
            status, reason = "stopped", halt.reason
        except Exception:
            # Do not copy callback errors, host paths or private data into public evidence.
            status, reason = "failed", "evaluationOrValidationFailed"
        summary = {"schemaVersion": VERSION, "status": status, "terminationReason": reason,
                   "evidenceKind": manifest.evidence_kind, "manifestDigest": manifest.digest,
                   "searchAlgorithm": "boundedCoordinateSearch/v1", "modelCalls": 0,
                   "evaluations": count, "acceptedGenerations": generations,
                   "initialPolicy": asdict(initial), "candidatePolicy": asdict(champion),
                   "baselineTraining": baseline.score if baseline is not None else None,
                   "candidateTraining": current.score if current is not None else None,
                   "baselineHoldout": held_initial.score if held_initial is not None else None,
                   "candidateHoldout": held_champion.score if held_champion is not None else None,
                   "productionChanged": False, "deployable": False,
                   "humanReviewRequired": True}
        journal.append("finished", summary)
        return summary
