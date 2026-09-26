# Collective coordination foundation

Status: **IMPLEMENTED FOUNDATION** for the typed message contract and deterministic narrow-only peer
gate. Persistence, API surfaces, runtime binding, cryptographic transport identity, and real multi-
Worker acceptance are **PLANNED INTEGRATION**. This document does not claim an autonomous collective
is deployed.

## Why this exists

Public reports about the July 2026 OpenAI / Hugging Face incident exposed a surprisingly useful
systems lesson separate from the security failures themselves: many short-lived agents developed
coordination infrastructure instead of relying on one all-knowing supervisor.

Across the OpenAI postmortem and the independent METR/Redwood investigation, agents used a shared
message board, rapidly developed sender and reply conventions, created targeted mailboxes/channels,
shared bounded artifacts, announced results, requested help, divided work into workstreams, delegated
subtasks, recorded liveness/reset state, and eventually used signatures around higher-stakes peer
messages.

Fabric should learn from that emergent organization without importing the dangerous authority model.
The useful pattern is a **coordination plane beside the control plane**, not a peer-controlled
replacement for Supervisor.

Research references:

- OpenAI, "The Hugging Face incident and the road ahead", 2026-08-26:
  https://openai.com/index/hugging-face-incident-and-the-road-ahead/
- METR / Redwood Research, "Brief independent investigation of agents' behavior, reasoning and
  collaboration in the OpenAI / Hugging Face hacking incident", 2026-08-26:
  https://www.redwoodresearch.org/research/hugging-face-incident
- Hugging Face, "Anatomy of a Frontier Lab Agent Intrusion: A Technical Timeline of the July 2026
  Incident", 2026-07-27:
  https://huggingface.co/blog/agent-intrusion-technical-timeline

The references describe security-sensitive behavior. Fabric adopts only general coordination
mechanisms. Exploit mechanics, credential collection, unauthorized access, and uncontrolled external
actions are explicitly outside this design.

## Architectural role

The collective coordination plane is a **non-authoritative social/semantic layer** for Workers.

It may carry:

- observations and compact information;
- questions, requests, offers, and acknowledgements;
- bounded result announcements;
- proposed assignments and workstream ownership;
- targeted replies, threads, channels, and Worker mailboxes;
- scoped HOLD, VETO, STOP, RELEASE, and GO coordination signals.

It must not directly:

- create a canonical Task;
- dispatch a Worker;
- issue or widen an AuthorizationEnvelope;
- grant a lease or resource lock;
- approve RED work;
- satisfy verification;
- mutate provider credentials or external systems;
- turn a peer's GO message into execution permission.

The canonical path remains:

1. peer coordination proposes or advertises work;
2. any new child work becomes a bounded ChildWorkProposal;
3. SpawnPolicy, budgets, steering versions, task locks, and circuit breakers evaluate it;
4. canonical state atomically admits or rejects the Task;
5. AuthorizationEnvelope, scheduler, leases, runtime state, and verification independently govern
   execution.

A useful blackboard can influence planning. It cannot become a second source of truth.

## Message contract

The implemented foundation is
src/project_supervisor/fabric/coordination.py.

CollectiveMessage is immutable after construction and carries bounded semantic identity:

- message, project, sender Worker, optional Goal and Task;
- channel, target Worker, thread, workstream, and resource;
- reply/supersession and optional source Worker run;
- one typed CoordinationKind;
- bounded priority, UTC timestamps, optional expiry;
- a canonical JSON payload capped at 16 KiB;
- a deterministic SHA-256 digest.

The initial message vocabulary is:

- ordinary collaboration: INFO, QUESTION, REQUEST, OFFER, RESULT, CLAIM, ASSIGN, ACK;
- narrow-only control: HOLD, VETO, STOP, RELEASE, GO.

The names describe coordination intent, not authority.

## Narrow-only peer control

Peer coordination follows one deliberately asymmetric rule:

> A peer may make execution more conservative, but cannot make canonical execution more permissive.

The deterministic CoordinationGate therefore interprets:

| Signal | Peer effect | Authority effect |
|---|---|---|
| HOLD | pause the exact matching scope | none |
| VETO | block the exact matching scope | none |
| STOP | block the exact matching scope | none |
| RELEASE | clear only the exact earlier HOLD from the same sender | none |
| GO | advisory only | none |
| all other kinds | advisory only | none |

GO intentionally cannot clear HOLD/VETO/STOP, create a Task, grant a capability, satisfy an approval,
or open a lease. A peer saying "go" means only that the peer recommends proceeding.

RELEASE is intentionally weak. It must reference one exact HOLD and come from the same sender after
that HOLD. It cannot clear a VETO or STOP. Higher-authority recovery remains a canonical control-plane
operation.

Control messages must be scoped to a workstream or resource. Optional Goal, Task, target Worker,
workstream, and resource fields are conjunctive boundaries during gate evaluation. A targeted HOLD
must not leak into another Worker or workstream.

## Identity and provenance

CollectiveMessage contains sender_worker_id and optional source_run_id, but the object alone does not
prove either identity.

When persistence/runtime integration is added:

- sender identity must be injected from the canonical Worker run or authenticated transport;
- the model must not choose an authoritative sender identity from prompt text;
- source run, Task revision, steering version, and event sequence should be recorded when relevant;
- signatures may authenticate origin and integrity, but a valid signature must not grant new
  capability or permission;
- replay and duplicate handling should be deterministic.

This preserves the useful part of the emergent agents' later signing convention without recreating
the failure mode where a message board becomes an authorization system.

## Channels, mailboxes, and workstreams

The reported agents converged on shared-board discovery plus more targeted communication as traffic
grew. Fabric should preserve the same scaling shape:

- board channel for discovery, requests for help, and compact announcements;
- named channels for stable workstreams;
- target Worker IDs for mailbox-like delivery;
- thread IDs and reply references for local conversation structure;
- workstream IDs for ownership and coordination gates;
- resource IDs for leases or scarce shared resources.

These are routing dimensions, not security principals.

A temporary coordinator may issue ASSIGN or REQUEST messages. Those messages remain proposals. The
existing deterministic scheduler and canonical repositories retain the final say over work creation
and dispatch.

## Artifacts instead of giant messages

The public reports show that agents eventually invented file-transfer conventions because a message
board is a poor transport for large artifacts. Fabric should not repeat that improvisation inside the
coordination payload.

The 16 KiB payload ceiling keeps messages semantic and inspectable. Larger data should move through
existing artifact, Bridge, or provider-specific evidence paths and be referenced by stable IDs or
digests. Project_Bridge remains optional and non-authoritative.

## Relationship to existing Fabric layers

This foundation is intentionally additive:

- Hybrid Engine decides useful topology.
- Cluster DAG expansion provides bounded roles, dependencies, and replicas.
- Capability Fabric describes what Workers can do.
- DeterministicScheduler decides eligible Worker placement.
- ChildWorkProposal and SpawnPolicy bound recursive work.
- AuthorizationEnvelope limits inherited authority.
- Result fusion combines independently produced evidence.
- Interaction Fabric governs semantic observe/act loops.
- Project_Bridge supports bounded information transfer.
- **Collective Coordination** lets Workers discover, ask, advertise, negotiate, pause, and route
  work without becoming a new source of canonical authority.

This is the missing "many Workers can organize themselves" layer between static DAG planning and raw
free-form chat.

## Planned integration

A later integration should remain incremental:

1. persist coordination messages append-only in SQLite with source-run provenance and deterministic
   replay;
2. add channel/mailbox/workstream indexes and bounded retention;
3. expose read-only projections to Cyber Office and scoped API observers;
4. provide a canonical conversion path from REQUEST/ASSIGN into ChildWorkProposal rather than direct
   Task creation;
5. feed the narrow-only CoordinationGate into dispatch readiness without allowing it to open any
   canonical gate;
6. add rate, fanout, depth, payload, and per-workstream quotas;
7. bind sender identity to authenticated Worker/runtime identity and add optional signed transport
   envelopes;
8. measure coordination usefulness, duplication, stale messages, deadlocks, and coordinator
   concentration before increasing autonomy.

Real runtime wiring should not be mixed into the unresolved autonomous-host lifecycle work. The
foundation can be reviewed and tested independently first.
