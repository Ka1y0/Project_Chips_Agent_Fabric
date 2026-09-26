# Collective coordination protocol hardening

Status: **IMPLEMENTED FOUNDATION**. This adds bounded wire decoding and strengthens the pure peer
reducer. It does not add persistence, transport authentication, runtime dispatch, or a public API.
The existing v1 message fields and canonical digest representation are retained. Validation and
release semantics are deliberately stricter within the unmerged V0.3 foundation.

## Exact release, not permission by resemblance

A RELEASE must reference a HOLD with a strictly earlier timestamp. The following fields must match
exactly, including null versus non-null values:

- project, sender Worker, and source Worker run;
- channel, Goal, Task, and target Worker;
- workstream and resource.

A task-specific release cannot remove a workstream-wide hold, and a new run cannot borrow an old
run's sender name to release its hold. Both source-run IDs may remain null for the existing pure
fixtures; null does not prove identity. Threads and reply IDs are conversational context, not an
execution scope. Timestamp ties are not proof of ordering and do not release a hold. A future
integration may use canonical sequence provenance instead, with its own reviewed contract.

RELEASE still cannot clear VETO or STOP. GO, ASSIGN, CLAIM, and RESULT remain advisory. Neither a
matching identifier nor a matching SHA-256 digest authenticates a sender or grants authority.

## Time and retention

The reducer samples one timezone-aware clock. Relevant future-dated messages raise a validation
error rather than being applied early. Invalid clocks, including false/zero values, are rejected.

A valid RELEASE is a historical event. Its announcement may expire, but its effect on the exact HOLD
must not expire and resurrect that hold. Callers must retain the release alongside its hold until
both can be safely compacted using canonical state. Replaying the complete history produces the same
result independent of delivery order or exact duplicate delivery.

HOLD, VETO, and peer STOP retain their explicit TTL behavior, if one was supplied. They are not the
operator's durable Goal STOP. Expiry only removes that peer constraint; it never satisfies any
canonical approval, lease, verification, or dispatch requirement.

## Complete bounded snapshots

Message IDs are checked for conflicting definitions across the selected project before time or
scope filtering. A replay cannot hide by changing expiry or moving the same ID into another scope.
Exact replays deduplicate; conflicts fail closed. IDs in different projects remain separate.

The reducer consumes at most 1,024 messages, plus one lookahead item needed to detect overflow. The
limit counts duplicates and unrelated-project messages, not just accepted controls. Overflow raises
an error; it never returns an apparently open gate from a truncated input. Input generators must
also be bounded by their caller; this is not a hard time limit for a generator that blocks.

The caller must supply a complete relevant snapshot, including release history. A bounded reducer
cannot detect a silently omitted STOP, a missing page, or an upstream-deleted release. Runtime
integration must verify snapshot completeness and perform the peer check alongside canonical
readiness in the correct transaction. A parser or reducer exception must block/escalate rather than
be converted into an empty/open result.

## Payload validation before serialization

The payload is an actual JSON object, not a list of pairs coerced into one. At every object level,
keys must be non-empty bounded strings. Values are finite JSON scalars, objects, or arrays. Local
Python tuples are normalized to JSON arrays for compatibility.

Limits are 16 KiB of canonical UTF-8 payload, depth 16 (root at depth zero), and 2,048 value/container
nodes. Size is accounted during traversal. Cycles, unsupported values, invalid Unicode, and
non-string keys are rejected before canonical serialization. Callers cannot mutate the frozen
payload through their original objects or through the detached result of `to_protocol()`.

## Strict wire decoder and schema

`project_supervisor.fabric.coordination_wire.decode_collective_message` accepts one UTF-8 string or
byte sequence of at most 48 KiB. The complete envelope is the output of `to_protocol()` including its
SHA-256 digest and nullable fields. `to_protocol(include_digest=False)` is for internal hashing, not
this wire contract.

The decoder rejects duplicate JSON keys at every level, unknown or missing envelope fields,
non-finite values, invalid UTF-8, malformed digests, and digest/content mismatches. Timestamps use the
Python-datetime-compatible RFC 3339 subset with an explicit known offset and at most six fractional
digits. Unknown `-00:00` offsets and leap-second timestamps are not accepted. Equivalent known
offsets normalize to the same UTC value and canonical digest.

The closed schema is `schemas/collective-coordination-message-v1.schema.json`. It describes structure
and field constraints. Runtime Python validation additionally enforces byte/depth/node budgets,
finite-number behavior, timestamp ordering, and canonical digest equality. Schema acceptance is not
sender authentication, payload sanitization, or permission to execute payload text.

## Integration still required

The decoder intentionally performs no I/O and knows no authenticated principal. Before admitting
messages, a future runtime must derive project/Worker/run/Task/steering identities from its own
canonical and authenticated context, enforce membership and anti-spam quotas, retain audited history,
and recheck Goal state, lease generation, and authorization at dispatch. None is inferred from the
wire's claimed identity, digest, or peer GO. Existing host lifecycle investigation remains separate.

## Reproduction

```sh
PYTHONDONTWRITEBYTECODE=1 pytest -q -p no:cacheprovider \
  tests/test_collective_coordination_v03.py \
  tests/test_coordination_boundaries.py tests/test_coordination_wire.py
ruff check --no-cache .
```

The initial 37-case boundary selection was run against the exact original coordination blob
`d9a059a6dba44171d4bcbe930a50a4edf554758d`: 28 cases failed and 9 passed. This is a count of test cases,
not 28 independent vulnerabilities. The original ten tests are retained unchanged. Subsequent test
counts and full-suite status belong to the exact reviewed commit and its CI run, not to this document.
