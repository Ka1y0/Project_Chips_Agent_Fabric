# ADR 0003: Two-stage metabolic heartbeat

Status: implemented, deterministic local tests; no unattended deployment claim.

## Decision

Sleep is orthogonal to human Goal state. Migration 0014 defaults existing Goals to awake, preserving
V0 behavior. Only a locally configured schedule and canonical evidence can wake a sleeping Goal.
A SQLite IMMEDIATE transaction validates the strict sleep contract, checks controls and database-wide
UTC-day budgets, reserves the budget, and invokes the existing generation claim path. Lease expiry
is a hard fence, including renewal. State, append-only tick ledger and normalized events commit
atomically. Silent polls are NOOP with exactly zero model calls and tokens.

The optional second stage is a single idempotent ProductionAutonomyDriver evaluation, with GREEN,
RESEARCH/REVIEW and no code writing. It is advisory: it cannot plan actions or apply SUCCESS.
Inspection mode is persisted, and its completed checkpoint plus next sleep are committed together.
The existing runtime owns dispatch constraints, Worker permissions, cancellation and usage audits.
Actual provider usage stays in telemetry; wake budget units are explicitly separate reservations.

## Tradeoffs

Schedules are operator policy, never model output. Unknown quota stays unknown; an elapsed reset
estimate alone is not evidence of restored capacity. Health checks read canonical node/Worker state,
not network probes. Different processes share the ledger but must use the same operator-selected
limits. Poll records grow with configured interval and Goal count; retention/export needs an explicit
future archival policy, not deletion or rewriting history.

A foreground host must remain running. This change neither installs an OS service nor weakens the
loopback/authentication defaults. External provider cancellation remains best-effort. Tests use Mock
or deterministic local subprocesses and do not establish real-provider acceptance.
