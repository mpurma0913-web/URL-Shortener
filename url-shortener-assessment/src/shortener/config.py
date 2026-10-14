"""Validated configuration. Credentials are mandatory and never generated silently."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit


@dataclass(frozen=True)
class Settings:
    database_path: Path = Path("data/shortener.db")
    api_key: str = ""
    reviewer_key: str = ""
    audit_key: str = ""
    base_url: str = "http://127.0.0.1:8000"
    allowed_hosts: tuple[str, ...] = ("127.0.0.1", "localhost")
    rate_limit: int = 120
    redirect_limit: int = 300
    body_limit: int = 16384
    busy_timeout_ms: int = 250
    database_attempts: int = 3

    def __post_init__(self) -> None:
        for value in (self.api_key, self.reviewer_key):
            if len(value) < 32 or value.startswith("REPLACE_") or not value.isascii():
                raise ValueError("Distinct ASCII API_KEY and REVIEWER_KEY of at least 32 characters are required")
        if self.audit_key and (len(self.audit_key) < 32 or not self.audit_key.isascii() or self.audit_key.startswith("REPLACE_")):
            raise ValueError("AUDIT_KEY must contain at least 32 ASCII characters")
        if self.api_key == self.reviewer_key:
            raise ValueError("Operator and reviewer credentials must differ")
        url = urlsplit(self.base_url)
        if (url.scheme not in {"http", "https"} or not url.hostname or url.username
                or url.password or url.query or url.fragment or url.path not in {"", "/"}):
            raise ValueError("BASE_URL must be an HTTP(S) origin without credentials, path or query")
        try:
            port = url.port
        except ValueError as exc:
            raise ValueError("BASE_URL port is invalid") from exc
        if port is not None and not 1 <= port <= 65535:
            raise ValueError("BASE_URL port is invalid")
        if any(c.isspace() or ord(c) < 32 for c in self.base_url):
            raise ValueError("BASE_URL contains invalid characters")
        if not self.allowed_hosts or "*" in self.allowed_hosts:
            raise ValueError("Explicit ALLOWED_HOSTS are required; wildcard hosts are disabled")
        if not 1 <= self.rate_limit <= 100000 or not 1 <= self.redirect_limit <= 100000:
            raise ValueError("Rate limits must be in 1..100000 requests per minute")
        if not 1024 <= self.body_limit <= 1048576:
            raise ValueError("BODY_LIMIT must be in 1024..1048576 bytes")
        if not 1 <= self.database_attempts <= 5 or not 1 <= self.busy_timeout_ms <= 2000:
            raise ValueError("Database retry/timeout budget is out of bounds")

    @classmethod
    def from_env(cls, env_file: Path = Path(".env")) -> Settings:
        values: dict[str, str] = {}
        if env_file.exists():
            for line in env_file.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line and not line.startswith("#"):
                    name, separator, value = line.partition("=")
                    if not separator:
                        raise ValueError("Invalid .env entry; expected NAME=value")
                    values[name.strip()] = value.strip()
        values.update(os.environ)
        return cls(
            database_path=Path(values.get("DATABASE_PATH", "data/shortener.db")),
            api_key=values.get("API_KEY", ""),
            reviewer_key=values.get("REVIEWER_KEY", ""),
            audit_key=values.get("AUDIT_KEY", ""),
            base_url=values.get("BASE_URL", "http://127.0.0.1:8000"),
            allowed_hosts=tuple(x.strip() for x in values.get("ALLOWED_HOSTS", "127.0.0.1,localhost").split(",") if x.strip()),
            rate_limit=int(values.get("RATE_LIMIT", "120")),
            redirect_limit=int(values.get("REDIRECT_LIMIT", "300")),
        )
