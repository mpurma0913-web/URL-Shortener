"""Framework-free domain types, interfaces and URL policy."""
from __future__ import annotations

import ipaddress
import re
import secrets
import string
import time
import unicodedata
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import quote, urlsplit, urlunsplit

import idna


class DomainError(Exception):
    """A safe public error. Never include submitted secrets or raw target URLs."""

    def __init__(self, status: int, code: str, message: str) -> None:
        self.status = status
        self.code = code
        self.message = message
        super().__init__(message)


class Clock(Protocol):
    def now(self) -> float: ...


class SystemClock:
    def now(self) -> float:
        return time.time()


class CodeGenerator(Protocol):
    def generate(self) -> str: ...


class RandomCodeGenerator:
    """Eight random base62 characters; the database is the uniqueness authority."""

    def generate(self) -> str:
        return "".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(8))


@dataclass(frozen=True)
class Link:
    code: str
    url: str
    created_at: float
    expires_at: float | None
    revoked: bool = False
    total_redirects: int = 0
    last_redirect_at: float | None = None

    def require_active(self, now: float) -> None:
        if self.revoked:
            raise DomainError(410, "link_revoked", "This short link has been revoked")
        if self.expires_at is not None and now >= self.expires_at:
            raise DomainError(410, "link_expired", "This short link has expired")


@dataclass(frozen=True)
class CreateResult:
    link: Link
    replayed: bool = False


def normalize_url(value: str) -> str:
    """Normalize only syntax, not query order or target semantics. Do not resolve DNS."""
    def invalid() -> DomainError:
        return DomainError(422, "invalid_url", "Use a public HTTP(S) URL without credentials or unsafe syntax")

    if not 1 <= len(value) <= 2048 or any(c.isspace() or unicodedata.category(c).startswith("C") for c in value):
        raise invalid()
    if "\\" in value or re.search(r"%(?:0[0-9a-f]|1[0-9a-f]|7f|5c)", value, re.I):
        raise invalid()
    if re.search(r"%(?![0-9a-fA-F]{2})", value):
        raise invalid()
    try:
        parsed = urlsplit(value)
        if (parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname
                or parsed.username is not None or parsed.password is not None):
            raise invalid()
        port = parsed.port
        if port is not None and port not in {80, 443}:
            raise invalid()
        host = parsed.hostname.lower().rstrip(".")
        if not host or "%" in host:
            raise invalid()
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            # Reject legacy/ambiguous numeric IPv4 and private-use names without DNS lookups.
            if ("." not in host or host.rsplit(".", 1)[-1].isdigit()
                    or host.startswith("0x")
                    or host.endswith((".localhost", ".local", ".internal", ".test", ".invalid"))):
                raise invalid() from None
            host = idna.encode(host, uts46=False, std3_rules=True).decode("ascii")
            if len(host) > 253:
                raise invalid()
        else:
            if not address.is_global or address.is_multicast or address.is_reserved:
                raise invalid()
            host = f"[{address.compressed}]" if address.version == 6 else address.compressed
        scheme = parsed.scheme.lower()
        default_port = 80 if scheme == "http" else 443
        authority = host + (f":{port}" if port is not None and port != default_port else "")
        path = quote(parsed.path or "/", safe="/%:@!$&'()*+,;=-._~")
        query = quote(parsed.query, safe="%/?@:!$&'()*+,;=-._~")
        fragment = quote(parsed.fragment, safe="%/?@:!$&'()*+,;=-._~")
        result = urlunsplit((scheme, authority, path, query, fragment))
        if len(result) > 2048:
            raise invalid()
        return result
    except (ValueError, idna.IDNAError) as exc:
        raise invalid() from exc
