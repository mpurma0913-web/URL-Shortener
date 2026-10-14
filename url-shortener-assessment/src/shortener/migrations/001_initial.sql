CREATE TABLE links (
    code TEXT PRIMARY KEY CHECK(length(code) = 8),
    url TEXT NOT NULL,
    created_at REAL NOT NULL,
    expires_at REAL,
    revoked INTEGER NOT NULL DEFAULT 0 CHECK(revoked IN (0,1)),
    total_redirects INTEGER NOT NULL DEFAULT 0 CHECK(total_redirects >= 0),
    last_redirect_at REAL
);
CREATE TABLE daily_clicks (
    code TEXT NOT NULL REFERENCES links(code),
    day TEXT NOT NULL,
    redirects INTEGER NOT NULL CHECK(redirects >= 0),
    PRIMARY KEY (code, day)
);
CREATE TABLE rate_limits (
    subject_hash TEXT NOT NULL,
    window INTEGER NOT NULL,
    count INTEGER NOT NULL CHECK(count >= 0),
    PRIMARY KEY (subject_hash, window)
);
CREATE INDEX rate_limits_window_idx ON rate_limits(window);
