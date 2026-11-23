# Memory, reflection and bounded exploration

Status: **IMPLEMENTED**, deterministic local/Mock contracts. No real-provider or deployment claim.
Migration 0015 adds immutable, source-bound memory and reflection records, an exploration reservation
ledger, and a derived Bridge inbox/outbox. Existing Goal origin defaults to `human`.

## Advisory memory

`MemoryRepository.record(MemoryDraft)` accepts strictly validated structured data only, verifies a
terminal Goal and its exact `goalTerminated`/`goalStopped` event, applies `redact_sensitive`, validates
again, and hashes the resulting content. State and audit event commit together. Duplicate content at
the same source is idempotent; revisions append a new entry. There is no chat/raw-response ingestion
endpoint. Memory is canonical as a recorded advisory artifact, never as an approval, fact oracle,
permission grant, or executable instruction.

The idle host deterministically consolidates terminal outcomes, observed harness usage and canonical
node-state counts. No model performs this consolidation. Tool preferences describe observed attempts,
not a ranking of capabilities or authorization. Reflection preserves source event and content hashes.

Driver retrieval is project-scoped (cross-Goal, not cross-project), excludes the current Goal, ranks
lexical overlap plus bounded importance deterministically, and fits a separate UTF-8 budget no larger
than a quarter of `max_context_bytes`. Memory is discarded before the Goal when the full context is
oversized; `omittedContextSHA256` records omitted context. The bounded candidate window is the latest
200 entries. This is transparent lexical retrieval, not an embedding/semantic-search claim.

## Isolated read-only trial

From the repository root with the project installed in `.venv`, run:

```sh
.venv/bin/python scripts/observe_office.py
```

Open `http://127.0.0.1:7330/office/` after the first NOOP. This trial explicitly permits
token-free loopback observation in a newly created directory. An ordinary API requires an
`observe:read` bearer in **连接设置**. Human controls additionally require `goal:control` and
an operator-enabled writable API.

See [authenticated startup and token creation](AUTONOMOUS_HOST.md#authenticated-operator-access).

## Exploration reservations

Use the initialized `DATA_DIR` from the operator guide. Both host `--enable-exploration` and `--enable-introspection` are required for automatic proposals:

```sh
.venv/bin/project-supervisor --data-dir "$DATA_DIR" autonomous serve \
  --enable-introspection --enable-exploration \
  --exploration-budget-cap 2 --exploration-cooldown-seconds 3600
```

Default: disabled. A local idle check may turn one unhandled memory gap, hypothesis, blocker, failure
lesson, or environment observation from a terminal human-origin Goal into a persisted proposal and
Goal. The same SQLite transaction reserves its budget. No live Goal/lease may be executing. An active SOFT PAUSE or HARD PAUSE blocks new proposals until the Goal leaves that state.
Historical `stopped` Goals with reason `USER_STOPPED` retain their terminal state and history.
Other eligible human-origin evidence can seed a new proposal while those Goals remain in SQLite.

Migration 0016 adds a database-wide, initially open exploration control. Pausing it blocks new
proposals, wakes and dispatch of exploration Goals. In-flight exploration requests cancellation
through the existing runtime and adapter. Resuming reopens eligibility under the existing Goal
controls, hard budgets, leases and cooldowns. A cancelled evaluation closes as `BLOCKED` on recovery. Its budget reservation and run history persist.
The exploration projection exposes `globalControl` (state and revision) and `humanBrakes`
(active paused Goals). The two controls are independent.
No model proposes new privileges. Entries from exploration Goals cannot recursively seed exploration.

`exploration_budget_cap` is a database-wide UTC-day hard cap in **one-read-only-evaluation Goal
reservations**, not a fabricated dollar/token ceiling. Backoff is global and persists across hosts.
A fresh known AVAILABLE/WARNING QuotaGuard candidate must satisfy scheduler constraints. UNKNOWN or
stale quota never finances exploration. Ordinary wake/inspection budgets apply in addition. Use the
same configured limits for all hosts sharing a database; their policies are observable separately.

Exploration mode is durable. Ordinary action loops reject it; only one GREEN RESEARCH/REVIEW
ProductionAutonomyDriver evaluation Task may execute, with code writing disabled and one Worker
attempt maximum. It completes with ITERATION_LIMIT, not self-attested SUCCESS, and creates no action
Tasks. Interrupted/ambiguous external execution uses existing recovery and does not grant a retry.
Actual invocation costs/tokens remain in existing telemetry and retain UNKNOWN when unobservable.

## Read-only observation

```sh
.venv/bin/project-supervisor --data-dir "$DATA_DIR" --json memory --project PROJECT_ID
.venv/bin/project-supervisor --data-dir "$DATA_DIR" --json exploration
.venv/bin/project-supervisor --data-dir "$DATA_DIR" --json bridge
```

REST: `GET /v1/memory`, `/v1/exploration`, `/v1/bridge`, `/v1/autonomy/heartbeat`, behind existing
`observe:read` authorization. The console uses `POST /v1/exploration/control` with exactly `state` (`paused` or
`open`), `expected_revision` and a nonempty `reason`. The mutation requires enabled controls
and a `goal:control` bearer. A stale revision returns HTTP 409. Each transition commits with
a redacted `globalExplorationPaused` or `globalExplorationResumed` event.
