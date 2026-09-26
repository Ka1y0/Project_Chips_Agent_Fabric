# ADR 0003: Controlled self-improvement begins with offline scheduler policy search

Status: experimental implementation; production integration not approved

## Decision

Introduce an explicitly invoked research laboratory that evaluates variants of Fabric's own
Scheduler scoring policy. Candidate policies change only three numeric soft weights. The experiment
uses the actual `DeterministicScheduler`; it does not replace hard constraints, invoke Workers,
change model weights, rewrite source, or mutate Supervisor's canonical database.

The loop is executable, not only a proposal schema:

1. Pin a manifest of code fingerprints, case identities, expected outcomes and guard cases.
2. Measure the starting policy on the training partition.
3. Generate bounded neighbouring policies from the current experimental champion.
4. Measure candidates and retain every positive/negative observation.
5. Accept a strict improvement only if no individual training case regresses and every guard passes.
6. Use the selected candidate as the parent of the next generation.
7. Freeze search, then measure the original and final policies on holdout once each.
8. Emit an awaiting-review recommendation only when holdout does not regress. Never deploy it.

This is **bounded system-level policy self-improvement**, not demonstrated strong recursive
self-improvement. The search algorithm is fixed coordinate search, not a self-rewriting optimizer.
No improvement in the system's ability to improve itself, acceleration, open-ended intelligence
growth, generalization to production tasks, or foundation-model training is claimed.

## Why this boundary

The existing scheduling ADR requires deterministic decisions and hard constraints before scoring.
Offline search can compare alternative configurations without silently making production adaptive.
The existing coordination and autonomous-host integration blockers remain separate. A benchmark
passing cannot authorize a release or repair an unrelated failed host acceptance gate.

The Darwin Goedel Machine paper illustrates agent-software modification and empirical evaluation:
https://arxiv.org/abs/2505.22954
The authors also document evaluator/reward manipulation:
https://sakana.ai/dgm/
Those motivate fixing the evaluator outside the candidate search space, retaining negative results,
and keeping review authority external. This implementation does not reproduce DGM or its results.

## Evidence and isolation

`self_improvement.py` owns the pure policy contract, bounded loop and exclusive research archive.
`self_improvement_scheduler.py` supplies a fixed synthetic replay evaluator around the real Scheduler.
A policy is three finite numbers, not Python, a prompt, a shell command, a path, or an authorization.
Isolation here means separate immutable candidate configurations and no execution capability. It is
not an OS sandbox for adversarial Python. The evaluator and Python caller are trusted application
code. A custom evaluator must never be downloaded or accepted from a candidate/model payload.

The synthetic example deliberately expresses a preference for quality over monetary cost in two
trade-off cases. Two separately named holdout cases vary the trade-off thresholds. This demonstrates
mechanics only. They are public, closely related, and neither hidden nor evidence of broad
out-of-sample performance. Repeated manual experiments against these fixtures do not create new
independent holdout evidence. Real adoption requires an operator-reviewed replay corpus, independently
measured outcomes, predeclared utility/cost budgets, and external acceptance gates.

Seven guard cases exercise RED approval, local code-write denial, offline nodes, exhausted resources,
privacy, capability mismatch and context limits. Each requires both the expected empty selection and
exactly the unchanged Scheduler's rejection records. They are regression samples, not a safety proof.

## Persistence and stop semantics

Each run creates one **new** owner-only SQLite research artifact at an explicit path. Existing files,
including Supervisor databases, are never overwritten. The archive has append-only rows with hash
chaining, intent-before-evaluation records, measured outcomes, selected lineage and a final summary.
This artifact is evidence only; it never becomes production Task/Goal/authorization state.

An interrupted archive can be inspected read-only, but never implicitly resumed or re-evaluated.
Missing terminal evidence is `interrupted`, not success. Hash chaining detects accidental alteration;
it is not authentication, and an owner who rewrites all hashes can forge an archive. No credential or
signature handling is implemented. Filesystem ownership is not a multi-tenant trust boundary.

Limits bound evaluations (including the original and both holdout evaluations), generations and a
cooperative elapsed deadline. Candidate replay is deduplicated within a run. A caller-supplied stop
predicate is checked before and after evaluation. In-process callbacks are trusted and must return:
the deadline is NOT a hard preemption guarantee for a hung evaluator or filesystem. No daemon or
scheduled task is installed. There is no restart, network, account, permission or deployment action.

## Use

From an installed development checkout on Python 3.12+:

```sh
python -m project_supervisor.self_improvement_scheduler \
  --demo --database /EXPLICIT/NEW/DIRECTORY/scheduler-experiment.sqlite
python -m project_supervisor.self_improvement_scheduler \
  --inspect --database /EXPLICIT/NEW/DIRECTORY/scheduler-experiment.sqlite
pytest -q tests/test_self_improvement.py tests/test_self_improvement_scheduler.py
```

Create the parent directory yourself. On Windows use `py -3.12 -m ...` and an explicit Windows path.
The first command evaluates fixtures only, consumes no model API calls and writes only the named new
archive. The second does not initialize or migrate a database. Exit zero means a completed experiment
or readable completed result, not deployment approval. Every summary has `productionChanged=false`,
`deployable=false`, `humanReviewRequired=true`, and an explicit evidence kind.

## Remaining integration

Not implemented: a real workload collector, agent-generated source/prompt patches, OS-sandboxed code
evaluation, code-edit Worker handoffs, production Goal-loop wiring, cryptographic evaluator attestation,
automatic PR creation by the experiment, approval consumption, deployment, or rollback execution.
The initial policy is retained as an experimental rollback reference, not an implemented deployment
rollback facility. Existing typed experiment handoffs remain the eventual route for authorized code
candidates; do not build a competing authorization mechanism in this lab.

A future source-edit phase must keep candidate write scope separate from evaluator tests, approval,
credentials, runtime and deployment; run trustworthy external evaluation; bind approval to exact
candidate/baseline/evaluator identities; recheck the current production head; and retain rollback.
This ADR is not permission to enable that phase or to weaken the current limits.
