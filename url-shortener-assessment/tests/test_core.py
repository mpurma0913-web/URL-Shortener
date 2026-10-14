"""FR-01..05,07..10: application acceptance tests, written before implementation."""
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from shortener.api import create_app
from shortener.config import Settings

KEY = "test-operator-credential-00000000000000000"
REVIEWER = "test-reviewer-credential-00000000000000000"


@pytest.fixture
def settings(tmp_path):
    return Settings(database_path=tmp_path / "test.db", api_key=KEY, reviewer_key=REVIEWER,
                    rate_limit=10000, allowed_hosts=("testserver", "localhost", "127.0.0.1"))


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings)) as c:
        yield c


def create(client, **overrides):
    body = {"url": "https://example.com/path?q=one#section", **overrides}
    return client.post("/api/v1/links", headers={"X-API-Key": KEY}, json=body)


def test_create_redirect_and_private_analytics(client):
    response = create(client)
    assert response.status_code == 201
    link = response.json()
    assert len(link["code"]) == 8
    code = link["code"]
    response = client.get(f"/r/{code}", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["location"] == "https://example.com/path?q=one#section"
    assert response.headers["cache-control"] == "no-store"
    assert client.get(f"/api/v1/links/{code}/stats").status_code == 401
    stats = client.get(f"/api/v1/links/{code}/stats", headers={"X-API-Key": KEY}).json()
    assert stats["total_redirects"] == 1
    assert sum(day["redirects"] for day in stats["daily"]) == 1
    assert stats["measurement"] == "committed_get_redirects"


def test_auth_and_role_separation(client):
    assert client.post("/api/v1/links", json={"url": "https://example.com"}).status_code == 401
    assert client.post("/api/v1/links", headers={"X-API-Key": REVIEWER},
                       json={"url": "https://example.com"}).status_code == 403


def test_head_does_not_count(client):
    code = create(client).json()["code"]
    assert client.head(f"/r/{code}", follow_redirects=False).status_code == 302
    stats = client.get(f"/api/v1/links/{code}/stats", headers={"X-API-Key": KEY}).json()
    assert stats["total_redirects"] == 0


@pytest.mark.parametrize("url", [
    "javascript:alert(1)", "file:///etc/passwd", "ftp://example.com", "https://user:pass@example.com",
    "http://127.0.0.1", "http://10.0.0.1", "http://[::1]", "http://169.254.169.254",
    "http://localhost", "http://service.internal", "http://2130706433", "http://127.1",
    "http://0x7f000001", "http://0177.0.0.1", "https://example.com\\@evil.com",
    "https://example.com/%0d%0aLocation:bad", "https://example.com:8080", "https://-bad.com",
    "https://example.com/has space", "https://example.com/%zz", "https://[::ffff:127.0.0.1]",
])
def test_unsafe_url_rejected(client, url):
    assert create(client, url=url).status_code == 422


def test_expiry_and_revocation(client):
    past = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
    assert create(client, expires_at=past).status_code == 422
    assert create(client, expires_at="2030-01-01T00:00:00").status_code == 422
    code = create(client).json()["code"]
    headers = {"X-API-Key": KEY}
    assert client.delete(f"/api/v1/links/{code}", headers=headers).status_code == 204
    assert client.delete(f"/api/v1/links/{code}", headers=headers).status_code == 204
    assert client.get(f"/r/{code}", follow_redirects=False).status_code == 410
    assert client.get(f"/api/v1/links/{code}", headers=headers).json()["revoked"] is True


def test_missing_invalid_and_unknown_fields(client):
    assert client.get("/r/AAAAAAAA", follow_redirects=False).status_code == 404
    assert create(client, extra="not allowed").status_code == 422
    assert client.get("/api/v1/links/AAAAAAAA/stats?days=1000", headers={"X-API-Key": KEY}).status_code == 422


def test_health_static_and_security_headers(client):
    assert client.get("/health/live").json()["status"] == "alive"
    assert client.get("/health/ready").status_code == 200
    response = client.get("/")
    assert response.status_code == 200
    assert "URL Shortener" in response.text
    assert "default-src 'self'" in response.headers["content-security-policy"]
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-request-id"]
    assert client.get("/openapi.json").json()["openapi"].startswith("3.")


@pytest.mark.concurrency
def test_parallel_redirects_have_no_lost_updates(client):
    code = create(client).json()["code"]
    with ThreadPoolExecutor(max_workers=8) as pool:
        statuses = list(pool.map(lambda _: client.get(f"/r/{code}", follow_redirects=False).status_code,
                                 range(32)))
    assert statuses == [302] * 32
    stats = client.get(f"/api/v1/links/{code}/stats", headers={"X-API-Key": KEY}).json()
    assert stats["total_redirects"] == 32


def test_database_persists_between_lifespans(settings):
    with TestClient(create_app(settings)) as first:
        code = create(first).json()["code"]
    with TestClient(create_app(settings)) as second:
        assert second.get(f"/r/{code}", follow_redirects=False).status_code == 302
