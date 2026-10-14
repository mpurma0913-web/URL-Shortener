"""SC-B / FR-06 regression: written and run against working v1 before the fix."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from test_core import KEY, client, create, settings


def post(client, key="retry-request-0001", **overrides):
    return client.post("/api/v1/links", headers={"X-API-Key": KEY, "Idempotency-Key": key},
                       json={"url": "https://example.com/payment", **overrides})


def test_same_key_and_payload_replays_original(client):
    first, second = post(client), post(client)
    assert first.status_code == 201
    assert second.status_code == 200
    assert first.json() == second.json()
    assert first.headers["idempotency-replayed"] == "false"
    assert second.headers["idempotency-replayed"] == "true"


def test_same_key_different_payload_conflicts(client):
    assert post(client).status_code == 201
    assert post(client, url="https://example.com/different").status_code == 409


def test_omitted_key_keeps_old_behavior(client):
    a, b = create(client), create(client)
    assert a.status_code == b.status_code == 201
    assert a.json()["code"] != b.json()["code"]


@pytest.mark.parametrize("key", ["", "tiny", "x" * 129, "contains space"])
def test_bad_idempotency_key_rejected(client, key):
    assert post(client, key=key).status_code == 422


@pytest.mark.concurrency
def test_parallel_retries_create_exactly_one_link(client):
    with ThreadPoolExecutor(max_workers=8) as pool:
        responses = list(pool.map(lambda _: post(client, key="parallel-request-0001"), range(20)))
    assert [r.status_code for r in responses].count(201) == 1
    assert [r.status_code for r in responses].count(200) == 19
    assert len({r.json()["code"] for r in responses}) == 1


def test_revoked_replay_does_not_reactivate(client):
    first = post(client)
    code = first.json()["code"]
    client.delete(f"/api/v1/links/{code}", headers={"X-API-Key": KEY})
    replay = post(client)
    assert replay.status_code == 200
    assert replay.json()["code"] == code
    assert replay.json()["revoked"] is True
    assert client.get(f"/r/{code}", follow_redirects=False).status_code == 410


def test_normalization_and_query_order(client):
    first = post(client, key="normalize-request-0001", url="HTTPS://EXAMPLE.COM:443")
    second = post(client, key="normalize-request-0001", url="https://example.com/")
    assert second.status_code == 200
    assert first.json()["code"] == second.json()["code"]
    post(client, key="query-order-00001", url="https://example.com/?x=1&y=2")
    assert post(client, key="query-order-00001", url="https://example.com/?y=2&x=1").status_code == 409
