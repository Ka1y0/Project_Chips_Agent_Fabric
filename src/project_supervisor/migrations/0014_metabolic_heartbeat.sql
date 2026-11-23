-- Orthogonal to human lifecycle controls: a timer can never resume a paused Goal.
ALTER TABLE autonomous_goals ADD COLUMN wake_state TEXT NOT NULL DEFAULT 'awake'
    CHECK(wake_state IN ('awake','sleeping'));
ALTER TABLE autonomous_goals ADD COLUMN next_wake_at TEXT;
ALTER TABLE autonomous_goals ADD COLUMN wake_reason TEXT
    CHECK(wake_reason IN ('timer','quotaCooldown','workerHealth','introspection'));
ALTER TABLE autonomous_goals ADD COLUMN cooldown_until TEXT;
ALTER TABLE autonomous_goals ADD COLUMN wake_policy_json TEXT;
ALTER TABLE autonomous_goals ADD COLUMN wake_health_baseline TEXT;
ALTER TABLE autonomous_goals ADD COLUMN last_self_wake_at TEXT;

CREATE INDEX autonomous_goals_wake_due ON autonomous_goals(wake_state,next_wake_at,id);

-- Append-only local evaluations. Budget units are wake reservations, not invented provider usage.
CREATE TABLE heartbeat_ticks (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    tick_id TEXT NOT NULL UNIQUE,
    host_id TEXT NOT NULL REFERENCES autonomous_hosts(host_id),
    evaluated_at TEXT NOT NULL,
    trigger_source TEXT NOT NULL CHECK(trigger_source IN ('poll','manual')),
    result TEXT NOT NULL CHECK(result IN ('NOOP','WOKE')),
    reason TEXT NOT NULL,
    goal_id TEXT REFERENCES autonomous_goals(id),
    lease_generation INTEGER CHECK(lease_generation > 0),
    wake_budget_used INTEGER NOT NULL CHECK(wake_budget_used IN (0,1)),
    inspection_budget_used INTEGER NOT NULL CHECK(inspection_budget_used IN (0,1)),
    local_model_calls INTEGER NOT NULL CHECK(local_model_calls = 0),
    local_tokens INTEGER NOT NULL CHECK(local_tokens = 0),
    CHECK((result='NOOP' AND wake_budget_used=0 AND inspection_budget_used=0
           AND lease_generation IS NULL) OR
          (result='WOKE' AND wake_budget_used=1 AND goal_id IS NOT NULL
           AND lease_generation IS NOT NULL))
);
CREATE INDEX heartbeat_ticks_day ON heartbeat_ticks(evaluated_at,result);
CREATE TRIGGER heartbeat_ticks_no_update BEFORE UPDATE ON heartbeat_ticks
BEGIN SELECT RAISE(ABORT, 'heartbeat ledger is append-only'); END;
CREATE TRIGGER heartbeat_ticks_no_delete BEFORE DELETE ON heartbeat_ticks
BEGIN SELECT RAISE(ABORT, 'heartbeat ledger is append-only'); END;
