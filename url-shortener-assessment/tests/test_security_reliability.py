"""Negative security, transaction, configuration and failure-path validation."""
from dataclasses import replace
from datetime import UTC, datetime
import json
import logging
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from shortener.api import create_app
from shortener.config import Settings
from shortener.domain import DomainError, Link, RandomCodeGenerator, normalize_url
from shortener.storage import Database
from test_core import KEY, REVIEWER, client, create, settings
from test_workflows import FakeClock


@pytest.mark.parametrize("changes", [
    {"api_key": ""}, {"reviewer_key": KEY}, {"api_key": "REPLACE_" + "x" * 40},
    {"audit_key": "short"}, {"allowed_hosts": ("*",)}, {"allowed_hosts": ()},
    {"rate_limit": 0}, {"redirect_limit": 100001}, {"body_limit": 1},
    {"database_attempts": 0}, {"busy_timeout_ms": 3000},
    {"base_url": "ftp://example.com"}, {"base_url": "https://x.test/path"},
    {"base_url": "https://x.test/?query=1"}, {"base_url": "http://x.test:bad"},
    {"base_url": "http://x.test:0"}, {"base_url": "http://exam ple.com"},
])
def test_invalid_settings_fail_at_startup(settings, changes):
    with pytest.raises(ValueError):
        replace(settings, **changes)


def test_env_is_loaded_without_overwriting_real_environment(tmp_path, monkeypatch):
    path = tmp_path / "settings.env"
    path.write_text(f"# comment\nAPI_KEY={KEY}\nREVIEWER_KEY={REVIEWER}\nRATE_LIMIT=10\n")
    monkeypatch.setenv("RATE_LIMIT", "20")
    assert Settings.from_env(path).rate_limit == 20
    path.write_text("malformed entry")
    with pytest.raises(ValueError):
        Settings.from_env(path)


@pytest.mark.parametrize("value", ["", "x" * 2049, "https://example.com/\x00", "http://example.com:99999",
    "http://", "https://[broken", "http://%31%32%37.0.0.1", "https://example.com/\u202e",
    "http://foo..com", "https://" + "a" * 254 + ".com", "https://user@example.com/", "http://192.168.1.1",
    "http://[fe80::1]", "http://[fc00::1]", "http://0.0.0.0", "http://224.0.0.1"])
def test_additional_url_policy_cases(value):
    with pytest.raises(DomainError):
        normalize_url(value)


@pytest.mark.parametrize("value,expected", [
    ("HTTPS://EXAMPLE.COM:443", "https://example.com/"),
    ("http://example.com:80/", "http://example.com/"),
    ("https://8.8.8.8/", "https://8.8.8.8/"),
    ("https://[2606:4700:4700::1111]/", "https://[2606:4700:4700::1111]/"),
    ("https://example.com./a?x=1&x=2", "https://example.com/a?x=1&x=2"),
    ("https://example.com:80/", "https://example.com:80/"),
    ("https://example.com/caf\u00e9", "https://example.com/caf%C3%A9"),
])
def test_url_normalization_preserves_meaning(value, expected):
    assert normalize_url(value) == expected


def test_expiration_exact_boundary_and_expired_replay(client):
    clock = FakeClock()
    client.app.state.repository.clock = clock
    expiry = datetime.fromtimestamp(clock.now() + 10, UTC).isoformat()
    headers = {"X-API-Key": KEY, "Idempotency-Key": "expire-replay-0001"}
    body = {"url": "https://example.com", "expires_at": expiry}
    first = client.post("/api/v1/links", headers=headers, json=body)
    assert first.status_code == 201
    clock.advance(10)
    assert client.get(f"/r/{first.json()['code']}", follow_redirects=False).status_code == 410
    replay = client.post("/api/v1/links", headers=headers, json=body)
    assert replay.status_code == 200
    assert replay.json()["code"] == first.json()["code"]


def test_expiry_more_than_one_year_rejected(client):
    clock = FakeClock()
    client.app.state.repository.clock = clock
    assert create(client, expires_at=datetime.fromtimestamp(clock.now() + 366 * 86400, UTC).isoformat()).status_code == 422


def test_collision_budget_is_bounded(client):
    class Fixed:
        calls = 0
        def generate(self):
            self.calls += 1
            return "AAAABBBB"
    generator = Fixed()
    client.app.state.repository.generator = generator
    assert create(client).status_code == 201
    assert create(client).status_code == 503
    assert generator.calls == 6


def test_analytics_failure_rolls_back_counter_before_any_redirect(client):
    code = create(client).json()["code"]
    db = client.app.state.database
    db.execute(lambda c: c.execute("CREATE TRIGGER fail_daily BEFORE INSERT ON daily_clicks BEGIN SELECT RAISE(ABORT,'fixture_failure'); END;"), write=True)
    response = client.get(f"/r/{code}", follow_redirects=False)
    assert response.status_code == 503
    assert "location" not in response.headers
    assert client.app.state.repository.get(code).total_redirects == 0


def test_locked_database_returns_bounded_503(settings):
    settings = replace(settings, busy_timeout_ms=5, database_attempts=2)
    with TestClient(create_app(settings)) as c:
        connection = c.app.state.database.connect()
        connection.execute("BEGIN IMMEDIATE")
        try:
            response = create(c)
            assert response.status_code == 503
            assert response.headers["retry-after"] == "1"
        finally:
            connection.rollback()
            connection.close()


def test_rate_limit_survives_restart_and_ignores_forwarded_ip(settings):
    settings = replace(settings, rate_limit=2)
    with TestClient(create_app(settings)) as c:
        assert create(c).status_code == 201
        assert create(c).status_code == 201
        assert c.post("/api/v1/links", headers={"X-API-Key": KEY, "X-Forwarded-For": "8.8.8.8"}, json={"url": "https://example.com"}).status_code == 429
    with TestClient(create_app(settings)) as c:
        response = create(c)
        assert response.status_code == 429
        assert response.headers["retry-after"] == "60"


def test_rate_window_rollover_and_cleanup(client):
    repo = client.app.state.repository
    clock = FakeClock()
    repo.clock = clock
    repo.consume_rate_limit("fixture-subject", 1)
    with pytest.raises(DomainError):
        repo.consume_rate_limit("fixture-subject", 1)
    clock.advance(181)
    repo.consume_rate_limit("fixture-subject", 1)
    assert repo.database.execute(lambda c: c.execute("SELECT COUNT(*) FROM rate_limits").fetchone()[0]) == 1


def test_oversized_body_chunked_body_and_untrusted_host(client):
    assert client.post("/api/v1/links", content=b"x" * 20000).status_code == 413
    assert client.post("/api/v1/links", content=iter([b"x" * 10000, b"x" * 10000])).status_code == 413
    assert client.get("/health/live", headers={"Host": "attacker.invalid"}).status_code == 400
    assert client.post("/api/v1/links", content=b"", headers={"Content-Length": "bad"}).status_code == 400


def test_request_ids_do_not_echo_untrusted_header(client):
    response = client.get("/health/live", headers={"X-Request-ID": "not-a-uuid-secret"})
    assert response.headers["x-request-id"] != "not-a-uuid-secret"
    valid = "7efcd054-16b0-4024-a90d-d2a52e41f7ce"
    assert client.get("/health/live", headers={"X-Request-ID": valid}).headers["x-request-id"] == valid


def test_logs_do_not_contain_target_query_credentials_or_raw_code(client, caplog):
    caplog.set_level(logging.INFO, logger="shortener.requests")
    link = create(client, url="https://example.com/secretpath?private=do-not-log").json()
    client.get(f"/r/{link['code']}", follow_redirects=False)
    text = "\n".join(record.getMessage() for record in caplog.records)
    assert "do-not-log" not in text and "secretpath" not in text and KEY not in text
    assert link["code"] not in text
    assert '"route":"/r/{code}"' in text


def test_validation_errors_do_not_echo_submitted_secret(client):
    response = create(client, url="credential-secret-invalid")
    assert "credential-secret-invalid" not in response.text


def test_metrics_are_private_and_low_cardinality(client):
    assert client.get("/metrics").status_code == 401
    for name in ["a", "b", "c"]:
        client.get("/unknown-" + name)
    response = client.get("/metrics", headers={"X-API-Key": KEY})
    assert response.status_code == 200
    assert 'route="unmatched"' in response.text
    assert "unknown-a" not in response.text


def test_schema_drift_and_readiness_fail_closed(client):
    db = client.app.state.database
    db.execute(lambda c: c.execute("UPDATE schema_migrations SET checksum='corrupted' WHERE version='001_initial.sql'"), write=True)
    with pytest.raises(DomainError, match="checksum"):
        db.initialize()
    db.execute(lambda c: c.execute("DELETE FROM schema_migrations WHERE version='003_workflows.sql'"), write=True)
    assert client.get("/health/ready").status_code == 503
    assert client.get("/health/live").status_code == 200


def test_database_failure_no_sql_details_leak(client):
    client.app.state.database.execute(lambda c: c.execute("DROP TABLE links"), write=True)
    response = create(client)
    assert response.status_code == 503
    assert "no such table" not in response.text


def test_random_generator_properties():
    codes = {RandomCodeGenerator().generate() for _ in range(1000)}
    assert len(codes) == 1000
    assert all(len(code) == 8 and code.isascii() and code.isalnum() for code in codes)
