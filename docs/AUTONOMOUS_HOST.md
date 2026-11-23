# Production autonomous host

Status: **V0.2.1 IMPLEMENTED and ACCEPTED in the private development tree**. This is not a V0.2.1
publication claim; public V0.1 history and release artifacts remain separate and unchanged.

The production autonomous host advances durable Goals without relying on a chat session, Codex
session, test harness, Cyber Office, or an in-memory conversation. SQLite and the append-only event
journal remain canonical.

## Runtime boundary

```text
SQLite Goals
  → production autonomous host
  → existing AutonomousIterationEngine
  → evaluator / planner / verifier decision Tasks
  → deterministic Hybrid Engine
  → generic Worker adapters
  → persisted results, verification, resource audit, and next iteration
```

The host does not duplicate autonomous-loop policy. It creates the existing engine with production
decision components, discovers eligible `created` or `running` Goals, claims one durable lease per
Goal, and keeps the lease alive while the engine advances. A successful Task does not make a Goal
successful; only the Goal evaluator/verifier can persist `SUCCESS`.

## Provider decision boundary

Evaluator, planner, and verifier Workers return one strict `autonomy-decision/v1` JSON object.
Malformed JSON, unknown fields, wrong types, invalid roles or labels, contradictory failure reasons,
and unsafe action payloads fail closed. Planner prompts state numeric and boolean wire constraints,
but runtime validation remains authoritative.

Two narrow, semantics-preserving normalizations handle provider formatting observed during V0.4
dogfood without weakening the rest of the schema:

- a non-empty evaluator/verifier `progressFingerprint` longer than 256 characters becomes
  `sha256:<exact-UTF-8-hash>`;
- evaluation `disposition=satisfied` with the exactly redundant `terminationReason=SUCCESS` becomes
  a null reason because success is already implied by the disposition.

Each normalization appends an `autonomyDecisionNormalized` event containing only the field, method,
lengths, and hashes. The decision binding retains the SHA-256 of the exact raw Worker output. Empty,
wrong-type, case-varied, incomplete/terminate, or contradictory reason combinations are not coerced
and still fail closed.

## Ownership and restart behavior

- Each process has a durable `host_id`, heartbeat, start/stop state, process identifier, active Goal
  count, and sanitized last error.
- Each Goal lease records its owner, monotonically increasing generation, expiry, heartbeat,
  current iteration/action, in-flight state, and recovery state.
- A host checks the current unexpired generation before Goal mutations. A stale process that lost
  its lease fails closed instead of continuing to write.
- A second live host cannot claim the same Goal. An expired owner can be recovered by a new host;
  persisted action identities and runtime recovery prevent silent duplicate planning.
- Polling is bounded by a configured interval; the host does not busy-spin.
- `SIGINT` and `SIGTERM` stop new claims, allow a configured grace period, persist host state, and
  leave canonical Goal checkpoints in SQLite for a later process.

Closing the terminal or agent session that started a foreground host normally terminates that host.
The Goal itself remains durable but will not advance until another autonomous host starts. For
unattended operation, an operator-managed service process must keep the host running; portable OS
service installation remains outside this milestone.

## Human control semantics

Goal controls are persisted by the existing Goal service and observed by the running host:

- **SOFT PAUSE** stops new planning/dispatch after safe completion of current work.
- **HARD PAUSE** also requests cancellation of cancellable in-flight work and freezes the persisted
  checkpoint as soon as the adapter boundary permits.
- **RESUME** returns the Goal to runnable state; a host can then rediscover it.
- **STEER** appends sanitized guidance. The next decision cycle reevaluates effective intent while
  preserving still-valid work unless invalidation is justified.
- **STOP** durably records `USER_STOPPED` and prevents further dispatch.

These controls do not grant capabilities, approve RED work, or bypass Worker permissions.

## Resource audit boundary

Every terminal Task is eligible for one idempotent logical `POST_TASK_USAGE_AUDIT`. A startup/recovery
scan fills any audit left incomplete by a process crash. Snapshots retain provider, account/quota
pool, Worker, model, local calls/tokens/cost, quota window/reset, observation time, freshness,
source, confidence, and provenance only when actually observable.

Provenance values are exactly `PROVIDER_REPORTED`, `LOCALLY_MEASURED`, `INFERRED`, and `UNKNOWN`.
Missing quota, plan, model, tokens, cost, reset time, or account scope remains `UNKNOWN`; it is never
converted to zero. The pre-dispatch quota guard can avoid a known scarce or exhausted premium pool
when a capable alternative exists, but it cannot invent capacity or relax scheduler hard constraints.
Freshness, provenance, source, confidence, effective quota state, and normalized health score are
persisted in the routing explanation. A stale observation reverts to UNKNOWN rather than retaining a
previous favorable or exhausted state.

When several autonomous actions share less Worker capacity, a busy otherwise-eligible Worker is a
transient capacity condition, not a permanently blocked Task. The runtime journals
`dispatchDeferred`, keeps the Task READY, waits on a capacity signal with bounded cross-process
polling, and retries from a fresh snapshot. HARD PAUSE/STOP cancellation can wake this wait.

## Isolated read-only trial

From the repository root with the project installed in `.venv`, run:

```sh
.venv/bin/python scripts/observe_office.py
```

Open `http://127.0.0.1:7330/office/` after the first NOOP. This trial explicitly permits
token-free loopback observation in a newly created directory. An ordinary API requires an
`observe:read` bearer in **连接设置**. Human controls additionally require `goal:control` and
an operator-enabled writable API.

The script prints the private trial directory and retains it for inspection. It starts an empty
database, one foreground host and one loopback API. Wake, inspection and exploration budgets are
zero. No Worker or service is installed. Ctrl+C stops both processes. Use `--check` for a
one-shot readiness test, `--port` for a different local port or `--data-dir` for a **new** path.
An existing directory is refused. The empty Worker and memory panels reflect this database.

### Authenticated operator access

For a normal deployment, use an initialized data directory. In a private terminal, run:

```sh
printf 'Initialized Supervisor data directory: '
IFS= read -r DATA_DIR
PS=.venv/bin/project-supervisor
"$PS" --data-dir "$DATA_DIR" token create --label office-read --scope observe:read --expires-in-hours 1
"$PS" --data-dir "$DATA_DIR" serve
```

Paste the displayed token into **连接设置** at the Office URL above. The UI keeps it in memory.
For human controls, an operator must explicitly set `read_only_api` to `false` in that directory's
`config.json` and restart the API. Issue the combined read/control token in the same private
terminal with the same `PS` and `DATA_DIR` variables:

```sh
"$PS" --data-dir "$DATA_DIR" token create --label office-control \
  --scope observe:read --scope goal:control --expires-in-hours 1
```

The API receives the bearer via an Authorization header for REST and the authentication frame for
WebSocket. Keep tokens out of URLs, shell arguments and shared output.

## Operator commands

The following examples use an explicit, operator-owned state directory and project root. Replace the
sample IDs and paths; do not guess an existing deployment's private paths.

```sh
PS=.venv/bin/project-supervisor
DATA_DIR=/path/to/private/supervisor-state
PROJECT_ROOT=/path/to/project

# One-time initialization and canonical records.
"$PS" --data-dir "$DATA_DIR" init
"$PS" --data-dir "$DATA_DIR" project create \
  --id example-project \
  --name "Example project" \
  --root "$PROJECT_ROOT" \
  --goal "Durable project purpose"
"$PS" --data-dir "$DATA_DIR" --json goal create \
  --id example-goal \
  --project example-project \
  --intent "Implement and verify the requested outcome"
```

Start exactly one Goal in the foreground:

```sh
"$PS" --data-dir "$DATA_DIR" --json autonomous run example-goal
```

The selected database must already contain at least one operator-reviewed, healthy Worker and node
whose capabilities satisfy the decision/action Tasks. V0.2.1 does not add an implicit Worker
registration or authentication command: installed executables are not automatically trusted
Workers, and the host fails closed when no persisted Worker can be reconstructed.

Or run a long-lived host that discovers all eligible Goals, with bounded concurrency:

```sh
"$PS" --data-dir "$DATA_DIR" autonomous serve --max-concurrent-goals 2
```

Worker calls remain bounded. The provider-neutral default is 120 seconds; an operator may configure
1–3600 seconds in `config.json` with `worker_timeout_seconds` or through
`PROJECT_SUPERVISOR_WORKER_TIMEOUT_SECONDS`. Increasing it does not disable cancellation or the
adapter deadline and should be justified by observed workload duration.

Persisted native Workers use portable PATH discovery by default. When an explicit executable is
required, scope the override to one persisted Worker or harness identity:

```sh
"$PS" --data-dir "$DATA_DIR" autonomous serve \
  --executable-override WORKER_OR_HARNESS=/absolute/reviewed/executable
```

An explicit Local Worker endpoint uses `--local-worker-endpoint WORKER_ID=URL`. Every explicit
endpoint, including loopback, requires a bearer resolved in process memory from the short-lived
`PROJECT_SUPERVISOR_LOCAL_WORKER_TOKEN` environment variable or the macOS Keychain service
`project-supervisor-local-worker` with account equal to `WORKER_ID`; it is never a CLI argument.
For a protocol-v2 production runtime, bind that persisted Worker to one operator-reviewed
server-side profile with `--local-worker-driver WORKER_ID=DRIVER_ID`. The selection is process-local,
is included in the durable adapter identity and request digest, and cannot be supplied by Goal/Task
text. A configured driver refuses protocol-v1 fallback so work cannot silently reach the daemon's
legacy default.
`--allow-mock-worker` is test-only and must not be used to claim real Worker acceptance.

Inspect live/durable state without requiring the REST process:

```sh
"$PS" --data-dir "$DATA_DIR" --json goal get example-goal
"$PS" --data-dir "$DATA_DIR" --json autonomous status --goal example-goal
"$PS" --data-dir "$DATA_DIR" --json tasks --project example-project
"$PS" --data-dir "$DATA_DIR" --json logs --after 0 --limit 200
"$PS" --data-dir "$DATA_DIR" --json telemetry --goal example-goal
"$PS" --data-dir "$DATA_DIR" --json resources --goal example-goal
```

Human controls use the canonical Goal commands:

```sh
"$PS" --data-dir "$DATA_DIR" --json goal pause example-goal \
  --mode soft --reason "Finish current work, then pause"
"$PS" --data-dir "$DATA_DIR" --json goal pause example-goal \
  --mode hard --reason "Cancel cancellable work and freeze"
"$PS" --data-dir "$DATA_DIR" --json goal resume example-goal \
  --reason "Operator approved continuation"
"$PS" --data-dir "$DATA_DIR" --json goal steer example-goal \
  --instruction "Preserve valid work and prioritize the new constraint"
"$PS" --data-dir "$DATA_DIR" --json goal stop example-goal \
  --reason "Operator intentionally stopped this Goal"
```

`project-supervisor serve` is a separate REST/WebSocket observer/control process. It does not
advance Goals. The `autonomous run` or `autonomous serve` process must remain running for forward
progress. Cyber Office and REST/WebSocket are optional for execution. A configured remote Worker
service and its private authenticated transport must remain reachable while its Tasks run; native
provider CLIs run as host child processes and need no separate daemon unless their provider requires
one.

Closing a Codex session has no effect on canonical Goal state. It does stop forward progress if the
autonomous host was attached to that session and the process is terminated with it. A separately
supervised autonomous-host process continues independently.

### REST/WebSocket observation and control

Start the separate loopback REST/WebSocket process with:

```sh
"$PS" --data-dir "$DATA_DIR" serve
```

The V0.2.1 read surfaces include `GET /v1/goals/{goal_id}`, `GET /v1/runs`,
`GET /v1/runs/{run_id}`, `GET /v1/autonomy/hosts`,
`GET /v1/autonomy/hosts/{host_id}`, `GET /v1/autonomy/leases`,
`GET /v1/telemetry`, `GET /v1/resources/snapshots`, and `GET /v1/resources/aggregate`. Query names
use protocol casing, for example `goalID`, `taskID`, `workerID`, `accountScope`, `quotaPoolID`,
`observedAfter`, and `observedBefore`.

When Goal mutation is explicitly enabled, the control routes are `POST /v1/goals`,
`POST /v1/goals/{goal_id}/pause`, `POST /v1/goals/{goal_id}/resume`,
`POST /v1/goals/{goal_id}/steer`, and `POST /v1/goals/{goal_id}/stop`. Observation requires the
configured read authorization. The database-wide brake uses `POST /v1/exploration/control`.
All control mutations additionally require `goal:control`. Do not place a
bearer in documentation, a URL, source, events, or evidence.

## Related documents

- [`AUTONOMOUS_ITERATION.md`](AUTONOMOUS_ITERATION.md) — Goal loop, controls, and guards
- [`RECOVERY.md`](RECOVERY.md) — canonical restart procedure
- [`SCHEDULER.md`](SCHEDULER.md) — deterministic Hybrid Engine behavior
- [`../SECURITY.md`](../SECURITY.md) — non-negotiable security boundaries

## Metabolic heartbeat (local deterministic implementation)

Migration 0014 keeps `wake_state` orthogonal to human Goal lifecycle state. Existing Goals default
 to awake. Every local poll records NOOP/WOKE and zero local model calls/tokens; wake reservations
are counted separately from actual Worker consumption in the existing telemetry/resource ledger.
The ledger is append-only. A wake, generation claim, budget reservation and event commit together.
Expired generations cannot renew. Sleep never overrides SOFT PAUSE, HARD PAUSE or STOP.

Explicitly schedule an idle Goal (UTC timestamps use `Z`):

```sh
"$PS" --data-dir "$DATA_DIR" goal sleep example-goal \
  --wake-reason timer --next-wake-at 2030-01-01T00:00:00Z
"$PS" --data-dir "$DATA_DIR" autonomous serve \
  --metabolic-interval-seconds 30 --max-daily-wakes 24 --wake-backoff-seconds 60
"$PS" --data-dir "$DATA_DIR" --json autonomous heartbeat --goal example-goal
```

`--iteration-sleep-seconds SECONDS` optionally checkpoints ordinary work between completed
iterations. It is disabled by default. `quotaCooldown` sleep requires `--worker`, `--quota-pool`
and `--cooldown-until`; expiration alone never invents quota. Only fresh AVAILABLE/WARNING evidence
from the existing QuotaGuard plus healthy canonical Worker/node state allows waking. `workerHealth`
requires `--worker` and a change from the persisted baseline to healthy/idle canonical state.
No heartbeat probes a provider, opens a socket or constructs an engine on NOOP.

Periodic advisory inspection requires both a Goal sleep policy with `--wake-reason introspection`,
`--next-wake-at`, `--inspection-interval-seconds` (at least 60), and host `--enable-introspection`.
It reserves one of `--max-daily-inspections` (default 4) in addition to the daily wake budget.
Limits are database-wide, evaluated by UTC day, and survive host restart; backoff is per Goal.
Operators must use consistent limits across hosts sharing a database. Goal iteration/token/cost
budgets remain enforced, including fail-closed unknown budget observations.

Inspection uses one idempotent ProductionAutonomyDriver evaluation Task: RESEARCH/REVIEW, GREEN,
SINGLE, code_write_required=False. It cannot plan actions or apply a model's proposed SUCCESS.
Persisted mode prevents restart from falling through to unrestricted planning. The inspection
checkpoint and next sleep commit atomically; existing Task/run recovery handles interrupted calls.
Soft pause permits current work to finish; hard pause/stop request runtime cancellation. Adapter
cancellation remains best-effort as in the existing runtime contract. No system service is installed,
and REST defaults remain read-only and loopback.
