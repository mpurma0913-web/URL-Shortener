"""SQLite unit-of-work and link repository. Domain errors survive transaction rollback."""
from __future__ import annotations

import hashlib
import json
import random
import sqlite3
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, TypeVar

from shortener.config import Settings
from shortener.domain import Clock, CodeGenerator, CreateResult, DomainError, Link

T = TypeVar("T")


class Database:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.path = settings.database_path

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, isolation_level=None,
                                     timeout=self.settings.busy_timeout_ms / 1000)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute(f"PRAGMA busy_timeout={self.settings.busy_timeout_ms}")
        return connection

    def execute(self, operation: Callable[[sqlite3.Connection], T], *, write: bool = False) -> T:
        for attempt in range(self.settings.database_attempts):
            connection: sqlite3.Connection | None = None
            try:
                connection = self.connect()
                if write:
                    connection.execute("BEGIN IMMEDIATE")
                result = operation(connection)
                if write:
                    connection.commit()
                return result
            except sqlite3.OperationalError as exc:
                if connection is not None:
                    connection.rollback()
                code = getattr(exc, "sqlite_errorcode", 0) & 0xFF
                if code not in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED}:
                    raise DomainError(503, "storage_unavailable", "Storage is unavailable") from exc
                if attempt + 1 == self.settings.database_attempts:
                    raise DomainError(503, "storage_busy", "Storage is busy; retry later") from exc
                time.sleep(0.025 * (2 ** attempt) + random.uniform(0, 0.01))  # nosec: jitter, not a secret
            except sqlite3.DatabaseError as exc:
                if connection is not None:
                    connection.rollback()
                raise DomainError(503, "storage_unavailable", "Storage is unavailable") from exc
            except Exception:
                if connection is not None:
                    connection.rollback()
                raise
            finally:
                if connection is not None:
                    connection.close()
        raise AssertionError("Retry budget must be positive")

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if not self.path.exists():
            self.path.touch(mode=0o600, exist_ok=True)
        self.execute(lambda c: c.execute("PRAGMA journal_mode=DELETE").fetchone())
        self.execute(lambda c: c.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations (version TEXT PRIMARY KEY, checksum TEXT NOT NULL)"), write=True)
        for migration in sorted((Path(__file__).parent / "migrations").glob("*.sql")):
            sql = migration.read_text(encoding="utf-8")
            checksum = hashlib.sha256(sql.encode()).hexdigest()

            def apply(c: sqlite3.Connection, name: str = migration.name, text: str = sql,
                      digest: str = checksum) -> None:
                previous = c.execute("SELECT checksum FROM schema_migrations WHERE version=?", (name,)).fetchone()
                if previous is not None:
                    if previous["checksum"] != digest:
                        raise DomainError(503, "migration_drift", "Applied migration checksum has changed")
                    return
                statement = ""
                for line in text.splitlines(keepends=True):
                    statement += line
                    if sqlite3.complete_statement(statement):
                        c.execute(statement)
                        statement = ""
                if statement.strip():
                    raise ValueError(f"Incomplete migration: {name}")
                c.execute("INSERT INTO schema_migrations(version,checksum) VALUES(?,?)", (name, digest))
            self.execute(apply, write=True)

    def healthy(self) -> bool:
        expected = {p.name for p in (Path(__file__).parent / "migrations").glob("*.sql")}
        def probe(c: sqlite3.Connection) -> bool:
            versions = {r[0] for r in c.execute("SELECT version FROM schema_migrations")}
            if versions != expected:
                raise DomainError(503, "schema_not_ready", "Database schema is not current")
            c.execute("SELECT code FROM links LIMIT 1").fetchone()
            c.execute("SELECT id FROM workflows LIMIT 1").fetchone()
            return True
        return self.execute(probe)


def link_from_row(row: sqlite3.Row | None) -> Link:
    if row is None:
        raise DomainError(404, "link_not_found", "Short link was not found")
    return Link(code=row["code"], url=row["url"], created_at=row["created_at"],
                expires_at=row["expires_at"], revoked=bool(row["revoked"]),
                total_redirects=row["total_redirects"], last_redirect_at=row["last_redirect_at"])


class SqliteLinkRepository:
    def __init__(self, database: Database, clock: Clock, generator: CodeGenerator) -> None:
        self.database = database
        self.clock = clock
        self.generator = generator

    def create(self, url: str, expires_at: float | None, idempotency_key: str | None = None) -> CreateResult:
        key_hash = hashlib.sha256(idempotency_key.encode()).hexdigest() if idempotency_key is not None else None
        payload_hash = hashlib.sha256(json.dumps({"url": url, "expires_at": expires_at},
                                                sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        def insert(c: sqlite3.Connection) -> CreateResult:
            if key_hash is not None:
                previous = c.execute("SELECT payload_hash,code FROM idempotency_keys WHERE key_hash=?", (key_hash,)).fetchone()
                if previous is not None:
                    if previous["payload_hash"] != payload_hash:
                        raise DomainError(409, "idempotency_conflict", "This idempotency key was used with a different request")
                    return CreateResult(link_from_row(c.execute("SELECT * FROM links WHERE code=?", (previous["code"],)).fetchone()), replayed=True)
            now = self.clock.now()
            if expires_at is not None and not now < expires_at <= now + 365 * 86400:
                raise DomainError(422, "invalid_expiration", "Expiry must be future and within 365 days")
            for _ in range(5):
                code = self.generator.generate()
                try:
                    c.execute("INSERT INTO links(code,url,created_at,expires_at) VALUES(?,?,?,?)",
                              (code, url, now, expires_at))
                except sqlite3.IntegrityError as exc:
                    if getattr(exc, "sqlite_errorcode", 0) not in {sqlite3.SQLITE_CONSTRAINT_PRIMARYKEY, sqlite3.SQLITE_CONSTRAINT_UNIQUE}:
                        raise
                    continue
                if key_hash is not None:
                    c.execute("INSERT INTO idempotency_keys(key_hash,payload_hash,code,created_at) VALUES(?,?,?,?)",
                              (key_hash, payload_hash, code, now))
                return CreateResult(Link(code, url, now, expires_at))
            raise DomainError(503, "code_exhausted", "Could not allocate a unique code; retry later")
        return self.database.execute(insert, write=True)

    def get(self, code: str) -> Link:
        return self.database.execute(lambda c: link_from_row(c.execute("SELECT * FROM links WHERE code=?", (code,)).fetchone()))

    def resolve(self, code: str, *, count: bool = True) -> Link:
        def resolve_and_record(c: sqlite3.Connection) -> Link:
            link = link_from_row(c.execute("SELECT * FROM links WHERE code=?", (code,)).fetchone())
            now = self.clock.now()
            link.require_active(now)
            if count:
                day = datetime.fromtimestamp(now, UTC).date().isoformat()
                c.execute("UPDATE links SET total_redirects=total_redirects+1,last_redirect_at=? WHERE code=?", (now, code))
                c.execute("INSERT INTO daily_clicks(code,day,redirects) VALUES(?,?,1) "
                          "ON CONFLICT(code,day) DO UPDATE SET redirects=redirects+1", (code, day))
            return link
        return self.database.execute(resolve_and_record, write=count)

    def revoke(self, code: str) -> None:
        def revoke_link(c: sqlite3.Connection) -> None:
            link_from_row(c.execute("SELECT * FROM links WHERE code=?", (code,)).fetchone())
            c.execute("UPDATE links SET revoked=1 WHERE code=?", (code,))
        self.database.execute(revoke_link, write=True)

    def stats(self, code: str, days: int) -> dict[str, Any]:
        def snapshot(c: sqlite3.Connection) -> dict[str, Any]:
            # A read transaction keeps the lifetime and daily counters in one consistent snapshot.
            c.execute("BEGIN")
            link = link_from_row(c.execute("SELECT * FROM links WHERE code=?", (code,)).fetchone())
            today = datetime.fromtimestamp(self.clock.now(), UTC).date()
            first = today - timedelta(days=days - 1)
            rows = c.execute("SELECT day,redirects FROM daily_clicks WHERE code=? AND day>=? AND day<=? ORDER BY day",
                             (code, first.isoformat(), today.isoformat())).fetchall()
            counts = {row["day"]: row["redirects"] for row in rows}
            return {"code": code, "measurement": "committed_get_redirects", "timezone": "UTC",
                    "total_redirects": link.total_redirects, "last_redirect_at": link.last_redirect_at,
                    "daily": [{"day": (first + timedelta(days=i)).isoformat(),
                               "redirects": counts.get((first + timedelta(days=i)).isoformat(), 0)} for i in range(days)]}
        return self.database.execute(snapshot)

    def consume_rate_limit(self, subject_hash: str, limit: int) -> None:
        now = self.clock.now()
        window = int(now) // 60
        def consume(c: sqlite3.Connection) -> None:
            c.execute("DELETE FROM rate_limits WHERE window<?", (window - 2,))
            row = c.execute("SELECT count FROM rate_limits WHERE subject_hash=? AND window=?", (subject_hash, window)).fetchone()
            if row is not None and row["count"] >= limit:
                raise DomainError(429, "rate_limited", "Request rate limit exceeded")
            c.execute("INSERT INTO rate_limits(subject_hash,window,count) VALUES(?,?,1) "
                      "ON CONFLICT(subject_hash,window) DO UPDATE SET count=count+1", (subject_hash, window))
        self.database.execute(consume, write=True)
