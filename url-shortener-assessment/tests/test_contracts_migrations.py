"""Versioned API/schema contracts, additive migration and recovery via verified backup."""
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3

import jsonschema
import pytest
from fastapi.testclient import TestClient

from scripts.backup import backup
from scripts.export_contracts import contracts
from scripts.init_env import initialize
from shortener.api import create_app
from test_core import KEY, client, create, settings
from test_workflow_api import create_run

ROOT = Path(__file__).resolve().parents[1]


def test_contract_snapshots_match_generated_schema():
    for name, expected in contracts().items():
        actual = json.loads((ROOT / "specs/001-url-shortener/contracts" / name).read_text())
        assert actual == expected, name


def test_openapi_has_unique_operations_auth_and_responses(client):
    schema = client.get("/openapi.json").json()
    operations = [op for path in schema["paths"].values() for method, op in path.items() if method in {"get", "post", "head", "delete"}]
    ids = [op["operationId"] for op in operations]
    assert len(ids) == len(set(ids))
    assert schema["paths"]["/api/v1/links"]["post"]["security"] == [{"APIKeyHeader": []}]
    assert "200" in schema["paths"]["/api/v1/links"]["post"]["responses"]
    assert "security" not in schema["paths"]["/r/{code}"]["get"]


def test_real_responses_validate_against_json_schemas(client):
    definitions = contracts()
    response = create(client).json()
    jsonschema.validate(response, definitions["LinkResponse.schema.json"])
    stats = client.get(f"/api/v1/links/{response['code']}/stats", headers={"X-API-Key": KEY}).json()
    jsonschema.validate(stats, definitions["StatsResponse.schema.json"])
    error = client.post("/api/v1/links", json={"url": "https://example.com"}).json()
    jsonschema.validate(error, definitions["ErrorResponse.schema.json"])
    jsonschema.validate(create_run(client), definitions["WorkflowResponse.schema.json"])


def test_additive_migrations_preserve_real_v1_data(settings):
    settings.database_path.parent.mkdir(parents=True, exist_ok=True)
    sql = (ROOT / "src/shortener/migrations/001_initial.sql").read_text()
    with closing(sqlite3.connect(settings.database_path)) as connection:
        connection.executescript(sql)
        connection.execute("CREATE TABLE schema_migrations(version TEXT PRIMARY KEY, checksum TEXT NOT NULL)")
        connection.execute("INSERT INTO schema_migrations VALUES(?,?)", ("001_initial.sql", hashlib.sha256(sql.encode()).hexdigest()))
        connection.execute("INSERT INTO links(code,url,created_at,total_redirects) VALUES('BASE1234','https://example.com/',1000,7)")
        connection.commit()
    with TestClient(create_app(settings)) as c:
        stats = c.get("/api/v1/links/BASE1234/stats", headers={"X-API-Key": KEY}).json()
        assert stats["total_redirects"] == 7
        headers = {"X-API-Key": KEY, "Idempotency-Key": "migration-request-0001"}
        first = c.post("/api/v1/links", headers=headers, json={"url": "https://example.com"})
        second = c.post("/api/v1/links", headers=headers, json={"url": "https://example.com"})
        assert first.status_code == 201 and second.status_code == 200
        assert first.json() == second.json()


def test_consistent_backup_and_restore(client, tmp_path):
    code = create(client).json()["code"]
    client.get(f"/r/{code}", follow_redirects=False)
    target = tmp_path / "backup" / "verified.db"
    report = backup(client.app.state.database.path, target)
    assert report["completed"] and report["integrity_check"] == "ok"
    with closing(sqlite3.connect(target)) as restored:
        assert restored.execute("SELECT total_redirects FROM links WHERE code=?", (code,)).fetchone()[0] == 1
    with pytest.raises(FileExistsError):
        backup(client.app.state.database.path, target)
    with pytest.raises(ValueError):
        backup(tmp_path / "missing.db", tmp_path / "nothing.db")


def test_env_initializer_never_overwrites_existing_secrets(tmp_path):
    path = tmp_path / ".env"
    initialize(path)
    old = path.read_text()
    assert "API_KEY=" in old and "AUDIT_KEY=" in old
    with pytest.raises(FileExistsError):
        initialize(path)
    assert path.read_text() == old
