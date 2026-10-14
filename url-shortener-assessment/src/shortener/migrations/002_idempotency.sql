CREATE TABLE idempotency_keys (
    key_hash TEXT PRIMARY KEY,
    payload_hash TEXT NOT NULL,
    code TEXT NOT NULL REFERENCES links(code),
    created_at REAL NOT NULL
);
