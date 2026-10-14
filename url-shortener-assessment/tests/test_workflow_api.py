"""API authorization, persistent audit, optimistic concurrency and compensation."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy

import pytest
from fastapi.testclient import TestClient

from shortener.api import create_app
from shortener.domain import DomainError
from shortener.workflows import Actor
from test_core import KEY, REVIEWER, client, settings
from test_workflows import WORKER, result

HEADERS = {"X-API-Key": KEY}
REVIEW_HEADERS = {"X-API-Key": REVIEWER}


def create_run(client):
    response = client.post("/api/v1/workflows", headers=HEADERS, json={"scenario": "greenfield",
                           "requirement": "Create a short URL for HTTP(S).", "acceptance_criteria": ["POST returns 201"]})
    assert response.status_code == 201
    return response.json()


def approve(client, run, gate, **extra):
    return client.post(f"/api/v1/workflows/{run['id']}/decisions", headers=REVIEW_HEADERS,
                       json={"expected_version": run["version"], "gate": gate, "approve": True,
                             "reason": "Explicit test-fixture API decision, not candidate approval", **extra})


def test_unauthorized_and_wrong_role_cannot_approve(client):
    run = create_run(client)
    payload = {"expected_version": run["version"], "gate": "requirements", "approve": True,
               "reason": "Operator cannot approve itself"}
    path = f"/api/v1/workflows/{run['id']}/decisions"
    assert client.post(path, json=payload).status_code == 401
    assert client.post(path, headers=HEADERS, json=payload).status_code == 403
    assert approve(client, run, "requirements").status_code == 200
    assert approve(client, run, "requirements").status_code == 409


def test_audit_pagination_and_state_survive_restart(client, settings):
    run = create_run(client)
    run = approve(client, run, "requirements").json()
    response = client.get(f"/api/v1/workflows/{run['id']}/audit?limit=1", headers=HEADERS)
    audit = response.json()
    assert audit["verified"] is True
    assert len(audit["events"]) == 1
    assert audit["next_after"] == 1
    with TestClient(create_app(settings)) as reopened:
        state = reopened.get(f"/api/v1/workflows/{run['id']}", headers=HEADERS).json()
        assert state["status"] == "WAITING_ARCHITECTURE"
        assert state["audit_head"] == run["audit_head"]


def test_concurrent_approvals_allow_exactly_one_version(client):
    run = create_run(client)
    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses = list(pool.map(lambda _: approve(client, run, "requirements").status_code, range(2)))
    assert sorted(statuses) == [200, 409]


def test_audit_tampering_detected_and_mutations_blocked(client):
    run = create_run(client)
    db = client.app.state.database
    db.execute(lambda c: c.execute("UPDATE workflow_audit SET event_json='{}' WHERE run_id=? AND sequence=1", (run["id"],)), write=True)
    audit = client.get(f"/api/v1/workflows/{run['id']}/audit", headers=HEADERS).json()
    assert audit["verified"] is False
    assert approve(client, run, "requirements").status_code == 503


def test_state_tampering_detected(client):
    run = create_run(client)
    client.app.state.database.execute(lambda c: c.execute("UPDATE workflows SET state_json='{}' WHERE id=?", (run["id"],)), write=True)
    assert client.get(f"/api/v1/workflows/{run['id']}", headers=HEADERS).status_code == 503


def test_compensation_actually_withdraws_manifest(client):
    run = create_run(client)
    run = approve(client, run, "requirements").json()
    run = approve(client, run, "architecture").json()
    store = client.app.state.workflow_store
    engine = client.app.state.workflow_engine
    state = store.apply(run["id"], None, lambda r: engine.claim(r, "implementation", WORKER)[1])
    token = state.nodes["implementation"].lease_token
    store.apply(run["id"], None, lambda r: engine.complete(r, "implementation", token, result("implementation"), WORKER))
    path = f"/api/v1/workflows/{run['id']}/candidate"
    assert client.get(path, headers=HEADERS).status_code == 200
    state = store.apply(run["id"], None, lambda r: engine.claim(r, "tests", WORKER)[1])
    token = state.nodes["tests"].lease_token
    store.apply(run["id"], None, lambda r: engine.fail(r, "tests", token, "permanent", "Fixture invalid source", WORKER))
    assert client.get(path, headers=HEADERS).status_code == 410
    assert client.get(f"/api/v1/workflows/{run['id']}/audit", headers=HEADERS).json()["verified"] is True


def test_no_remote_execution_or_result_upload_endpoint(client):
    run = create_run(client)
    assert client.post(f"/api/v1/workflows/{run['id']}/execute", headers=HEADERS, json={"command": "anything"}).status_code == 404
    assert client.post(f"/api/v1/workflows/{run['id']}/complete", headers=HEADERS, json={"passed": True}).status_code == 404


def test_unknown_workflow_and_malformed_id(client):
    assert client.get("/api/v1/workflows/not-a-uuid", headers=HEADERS).status_code == 422
    assert client.get("/api/v1/workflows/00000000-0000-0000-0000-000000000000", headers=HEADERS).status_code == 404
    assert client.get("/api/v1/workflows?limit=1000", headers=HEADERS).status_code == 422
