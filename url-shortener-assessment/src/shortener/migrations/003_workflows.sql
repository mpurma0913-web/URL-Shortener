CREATE TABLE workflows (
    id TEXT PRIMARY KEY,
    version INTEGER NOT NULL,
    created_at REAL NOT NULL,
    state_json TEXT NOT NULL,
    state_mac TEXT NOT NULL
);
CREATE INDEX workflows_created_idx ON workflows(created_at);
CREATE TABLE workflow_audit (
    run_id TEXT NOT NULL REFERENCES workflows(id),
    sequence INTEGER NOT NULL,
    event_json TEXT NOT NULL,
    previous_hash TEXT NOT NULL,
    event_hash TEXT NOT NULL,
    PRIMARY KEY (run_id, sequence)
);
