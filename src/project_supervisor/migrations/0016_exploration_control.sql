CREATE TABLE exploration_control (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    state TEXT NOT NULL CHECK (state IN ('open', 'paused')),
    revision INTEGER NOT NULL CHECK (revision >= 0)
);
INSERT INTO exploration_control VALUES (1, 'open', 0);
