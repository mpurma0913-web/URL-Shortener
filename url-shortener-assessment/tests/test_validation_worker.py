"""Bounded subprocesses, source fingerprints, real fallback and parallel worker behavior."""
from dataclasses import replace
import json
import os
from pathlib import Path
import sys
import threading

import pytest

from shortener.domain import DomainError
from shortener.storage import Database
from shortener.validation import CommandResult, CommandRunner, hygiene, installed_versions, license_inventory, source_digest, source_inventory
from shortener.worker import StageExecutor, Worker
from shortener.workflow_store import WorkflowStore
from shortener.workflows import CheckResult, StageResult, WorkflowEngine
from test_core import settings
from test_workflows import FakeClock, OPERATOR, REVIEWER, WORKER, spec


@pytest.fixture
def root(tmp_path):
    root = tmp_path / "repo"
    (root / "src").mkdir(parents=True)
    (root / "src" / "sample.py").write_text('VALUE = 1\n')
    (root / "pyproject.toml").write_text('[project]\nname="fixture"\n')
    return root


def test_fingerprint_ignores_evidence_and_changes_with_source(root):
    before = source_digest(root)
    (root / "evidence").mkdir()
    (root / "evidence" / "file.log").write_text("new result")
    assert source_digest(root) == before
    (root / "src" / "sample.py").write_text("VALUE = 2\n")
    assert source_digest(root) != before
    assert "src/sample.py" in source_inventory(root)


def test_hygiene_reports_precise_rule_without_source_secret(root):
    assert hygiene(root)["outcome"] == "PASS"
    (root / "src" / "bad.py").write_text("eval('1')\ntry:\n    value = 1\nexcept Exception:\n    pass\n")
    report = hygiene(root)
    assert {f["rule"] for f in report["findings"]} == {"dynamic_execution", "silenced_exception"}
    (root / "src" / "bad.py").write_text("\tdef bad(:  \n")
    assert {f["rule"] for f in hygiene(root)["findings"]} == {"whitespace", "syntax"}


def test_command_output_is_redacted_and_secrets_not_inherited(root, monkeypatch):
    monkeypatch.setenv("MY_PRIVATE_TOKEN", "fixture-secret-never-inherit")
    runner = CommandRunner(("fixture-redact-me",))
    result = runner.run([sys.executable, "-c", "import os; print('fixture-redact-me'); print(os.getenv('MY_PRIVATE_TOKEN'))"], root, "evidence/command.log")
    assert result.outcome == "PASS"
    text = (root / result.evidence).read_text()
    assert "[REDACTED]" in text and "fixture-redact-me" not in text
    assert "fixture-secret-never-inherit" not in text
    assert "None" in text


def test_command_failure_missing_binary_timeout_and_output_cap(root):
    runner = CommandRunner(output_limit=1000)
    assert runner.run([sys.executable, "-c", "raise SystemExit(3)"], root, "evidence/fail.log").outcome == "FAIL"
    assert runner.run(["/definitely/nonexistent/binary"], root, "evidence/missing.log").outcome == "UNEXECUTED"
    assert runner.run([sys.executable, "-c", "import time; time.sleep(5)"], root, "evidence/timeout.log", timeout=0.05).outcome == "TIMEOUT"
    assert runner.run([sys.executable, "-c", "print('x'*10000)"], root, "evidence/large.log").outcome == "OUTPUT_LIMIT"
    assert (root / "evidence/large.log").stat().st_size < 1300


@pytest.mark.parametrize("path", ["", "../outside.log", "/tmp/outside.log"])
def test_evidence_cannot_escape_repository(root, path):
    with pytest.raises(ValueError):
        CommandRunner().run([sys.executable, "-c", "print(1)"], root, path)


def test_package_metadata_inventory():
    assert installed_versions(["fastapi", "no-such-package-fixture"])["no-such-package-fixture"] is None
    assert license_inventory(["fastapi"])[0]["installed"] is True
    assert license_inventory(["no-such-package-fixture"])[0]["installed"] is False


def test_real_filesystem_fallback_without_git(root):
    result = StageExecutor(root, CommandRunner()).execute("fixture-run", 1, "implementation")
    assert result.mode == "fallback"
    assert result.source_digest == source_digest(root)
    assert (root / result.evidence_refs[0]).is_file()


class FakeRunner:
    def __init__(self, outcome="PASS"):
        self.outcome = outcome
    def run(self, command, root, evidence, timeout=90):
        path = root / evidence
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("synthetic adapter result, not actual external-tool output")
        if "scripts/security_checks.py" in command:
            checks = [{"id": x, "outcome": "PASS", "evidence": evidence} for x in ["secret_scan", "lint", "types", "dependency_audit", "license_inventory"]]
            (path.parent / "summary.json").write_text(json.dumps({"checks": checks}))
        return CommandResult(command, self.outcome, 0 if self.outcome == "PASS" else 1, 0, evidence)


@pytest.mark.parametrize("stage", ["tests", "security", "documentation"])
def test_stage_executor_parses_evidence_and_checks(root, stage):
    result = StageExecutor(root, FakeRunner()).execute("fixture", 1, stage)
    assert result.checks and all(c.outcome == "PASS" for c in result.checks)


@pytest.mark.parametrize("stage", ["tests", "security", "documentation"])
def test_stage_executor_timeout_is_not_success(root, stage):
    with pytest.raises(DomainError):
        StageExecutor(root, FakeRunner("TIMEOUT")).execute("fixture", 1, stage)


def test_stage_executor_rejects_unknown_stage(root):
    with pytest.raises(ValueError):
        StageExecutor(root, FakeRunner()).execute("fixture", 1, "arbitrary-command")


@pytest.fixture
def store(settings):
    db = Database(settings)
    db.initialize()
    return WorkflowStore(db, WorkflowEngine(FakeClock()), b"fixture-audit-key-00000000000000000")


def prepared_store(store):
    run = store.create(spec(), OPERATOR)
    run = store.apply(run.id, run.version, lambda r: store.engine.decide(r, REVIEWER, "requirements", True, "Fixture requirements approved"))
    return store.apply(run.id, run.version, lambda r: store.engine.decide(r, REVIEWER, "architecture", True, "Fixture architecture approved"))


def test_real_worker_parallel_fanout_and_synchronization(root, store):
    run = prepared_store(store)
    barrier = threading.Barrier(3, timeout=5)
    class Executor:
        def __init__(self):
            self.root = root
        def execute(self, run_id, revision, stage):
            from test_workflows import result
            if stage != "implementation":
                barrier.wait()  # All three real worker threads must enter before any can finish.
            data = result(stage)
            data.source_digest = source_digest(root)
            return data
    worker = Worker(store, Executor())
    output = worker.run(run.id, until_blocked=True)
    assert output["status"] == "WAITING_RELEASE"
    assert set(output["executions"]) == {"implementation", "tests", "security", "documentation"}
    assert store.audit(run.id)["verified"] is True


def test_worker_unknown_adapter_failure_safe_stops(root, store):
    run = prepared_store(store)
    class Executor:
        def __init__(self):
            self.root = root
        def execute(self, *args):
            raise RuntimeError("Do not leak fixture sensitive error details")
    output = Worker(store, Executor()).run(run.id, until_blocked=True)
    assert output["status"] == "SAFE_STOP"
    assert "sensitive error" not in json.dumps(store.audit(run.id))


def test_worker_does_not_cross_human_gate(root, store):
    run = store.create(spec(), OPERATOR)
    output = Worker(store, StageExecutor(root, FakeRunner())).run(run.id, until_blocked=True)
    assert output["status"] == "WAITING_REQUIREMENTS"
    assert output["executions"] == {}


def test_duplicate_worker_claim_is_harmless(root, store):
    run = prepared_store(store)
    store.apply(run.id, None, lambda r: store.engine.claim(r, "implementation", WORKER)[1])
    assert Worker(store, StageExecutor(root, FakeRunner())).execute_stage(run.id, "implementation") == "NOT_CLAIMED"
