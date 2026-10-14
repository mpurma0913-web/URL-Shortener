"""Application service depends on a small repository interface, not SQLite or HTTP."""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Protocol

from shortener.domain import CreateResult, DomainError, Link, normalize_url


class LinkRepository(Protocol):
    def create(self, url: str, expires_at: float | None, idempotency_key: str | None = None) -> CreateResult: ...
    def get(self, code: str) -> Link: ...
    def resolve(self, code: str, *, count: bool = True) -> Link: ...
    def revoke(self, code: str) -> None: ...
    def stats(self, code: str, days: int) -> dict[str, Any]: ...


class LinkService:
    def __init__(self, repository: LinkRepository) -> None:
        self.repository = repository

    def create(self, url: str, expires_at: datetime | None = None, idempotency_key: str | None = None) -> CreateResult:
        if idempotency_key is not None and not re.fullmatch(r"[A-Za-z0-9._:-]{8,128}", idempotency_key):
            raise DomainError(422, "invalid_idempotency_key", "Use 8-128 ASCII letters, digits, dots, underscores, colons or hyphens")
        if expires_at is not None and expires_at.utcoffset() is None:
            raise DomainError(422, "invalid_expiration", "Expiry must include a timezone")
        return self.repository.create(normalize_url(url), expires_at.timestamp() if expires_at else None, idempotency_key)

    def get(self, code: str) -> Link:
        return self.repository.get(code)

    def resolve(self, code: str, *, count: bool = True) -> Link:
        return self.repository.resolve(code, count=count)

    def revoke(self, code: str) -> None:
        self.repository.revoke(code)

    def stats(self, code: str, days: int) -> dict[str, Any]:
        return self.repository.stats(code, days)
