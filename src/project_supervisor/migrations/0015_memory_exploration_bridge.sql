ALTER TABLE autonomous_goals ADD COLUMN origin TEXT NOT NULL DEFAULT 'human'
    CHECK(origin IN ('human','exploration'));

CREATE TABLE memory_entries (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id),
    source_goal_id TEXT NOT NULL REFERENCES autonomous_goals(id),
    source_event_id TEXT NOT NULL REFERENCES events(event_id),
    kind TEXT NOT NULL,
    content_sha256 TEXT NOT NULL CHECK(length(content_sha256)=64),
    entry_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(source_goal_id,source_event_id,kind,content_sha256)
);
CREATE INDEX memory_entries_project ON memory_entries(project_id,created_at,id);
CREATE TABLE reflection_summaries (
    goal_id TEXT PRIMARY KEY REFERENCES autonomous_goals(id),
    source_event_id TEXT NOT NULL REFERENCES events(event_id),
    content_sha256 TEXT NOT NULL,
    summary_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TRIGGER memory_no_update BEFORE UPDATE ON memory_entries
BEGIN SELECT RAISE(ABORT,'memory is append-only'); END;
CREATE TRIGGER memory_no_delete BEFORE DELETE ON memory_entries
BEGIN SELECT RAISE(ABORT,'memory is append-only'); END;
CREATE TRIGGER reflection_no_update BEFORE UPDATE ON reflection_summaries
BEGIN SELECT RAISE(ABORT,'reflections are append-only'); END;
CREATE TRIGGER reflection_no_delete BEFORE DELETE ON reflection_summaries
BEGIN SELECT RAISE(ABORT,'reflections are append-only'); END;

CREATE TABLE exploration_proposals (
    id TEXT PRIMARY KEY,
    source_memory_id TEXT NOT NULL UNIQUE REFERENCES memory_entries(id),
    source_goal_id TEXT NOT NULL REFERENCES autonomous_goals(id),
    goal_id TEXT NOT NULL UNIQUE REFERENCES autonomous_goals(id),
    host_id TEXT NOT NULL REFERENCES autonomous_hosts(host_id),
    budget_used INTEGER NOT NULL CHECK(budget_used=1),
    proposal_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX exploration_budget_day ON exploration_proposals(created_at);
CREATE TRIGGER exploration_no_update BEFORE UPDATE ON exploration_proposals
BEGIN SELECT RAISE(ABORT,'exploration ledger is append-only'); END;
CREATE TRIGGER exploration_no_delete BEFORE DELETE ON exploration_proposals
BEGIN SELECT RAISE(ABORT,'exploration ledger is append-only'); END;

CREATE TABLE bridge_messages (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id TEXT NOT NULL,
    direction TEXT NOT NULL CHECK(direction IN ('inbound','outbound')),
    peer_node_id TEXT NOT NULL REFERENCES nodes(id),
    kind TEXT NOT NULL CHECK(kind IN ('nodeStatus','delegationProposal')),
    envelope_sha256 TEXT NOT NULL,
    content_sha256 TEXT NOT NULL,
    content_json TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(direction,peer_node_id,message_id)
);
CREATE TRIGGER bridge_no_update BEFORE UPDATE ON bridge_messages
BEGIN SELECT RAISE(ABORT,'Bridge receipts are append-only'); END;
CREATE TRIGGER bridge_no_delete BEFORE DELETE ON bridge_messages
BEGIN SELECT RAISE(ABORT,'Bridge receipts are append-only'); END;
