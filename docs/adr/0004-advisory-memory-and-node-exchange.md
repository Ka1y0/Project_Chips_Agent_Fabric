# ADR 0004: Advisory memory, bounded exploration and derived node exchange

Status: implemented local prototype; deployment and real transport acceptance remain separate.

SQLite and append-only events stay authoritative. Memory is validated, redacted, hash-linked to a
terminal Goal/event and explicitly advisory. Relevance retrieval is project-scoped and byte-bounded.
Exploration reserves a separate persistent daily budget before creating a Goal, requires known quota,
respects a database-wide human brake, and can execute only one read-only evaluation. Persistence of
origin prevents restart from upgrading exploration into production action planning.

Bridge remains optional. Verified identity comes only from an operator-injected private transport,
not the envelope or hash. Pre-enrolled identities, strict versions/payloads, expiry, replay checks and
bilateral task-scoped grants are mandatory. Received state is a derived claim; received delegations
are reviewable references, never implicit dispatch. Bridge exposes no trust enrollment or Goal mutation API.
