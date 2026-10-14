"""Explicit local worker for a trusted repository; fixed tasks, leases and parallel checks.

Run after reviewer gates: python -m shortener.worker --run-id UUID --until-blocked
This is read-only validation of candidate source, not an autonomous coding agent.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Protocol

from shortener.config import Settings
from shortener.domain import DomainError, SystemClock
from shortener.storage import Database
from shortener.validation import CommandResult, CommandRunner, source_digest, source_inventory
from shortener.workflow_store import WorkflowStore
from shortener.workflows import Actor, CheckResult, StageResult, WorkflowEngine

WORKER = Actor(role="worker", kind="local_worker", name="bounded-local-worker")
OPERATOR = Actor(role="operator", kind="local_worker", name="local-recovery-operator")


class CommandExecution(Protocol):
    def run(self, command: list[str], root: Path, evidence: str, timeout: float = 90) -> CommandResult: ...


class StageExecution(Protocol):
    root: Path
    def execute(self, run_id: str, revision: int, stage: str) -> StageResult: ...


class StageExecutor:
    def __init__(self, root: Path, runner: CommandExecution) -> None:
        self.root = root.resolve()
        self.runner = runner

    def execute(self, run_id: str, revision: int, stage: str) -> StageResult:
        directory = f"evidence/runtime/{run_id}/revision-{revision}/{stage}"
        (self.root / directory).mkdir(parents=True, exist_ok=True)
        before = source_digest(self.root)
        if stage == "implementation":
            git = shutil.which("git")
            git_result = None
            if git and (self.root / ".git").exists():
                git_result = self.runner.run([git, "rev-parse", "HEAD"], self.root, f"{directory}/git.log", timeout=10)
            mode = "primary" if git_result and git_result.outcome == "PASS" else "fallback"
            ref = f"{directory}/artifact.json"
            artifact = {"source_digest": before, "files": source_inventory(self.root),
                        "operation": "capture_candidate_source_not_generate_code", "git_available": mode == "primary"}
            (self.root / ref).write_text(json.dumps(artifact, indent=2) + "\n")
            return StageResult(source_digest=before, evidence_refs=[ref], mode=mode,
                               detail="Filesystem SHA-256 inventory; Git revision additionally captured when available")
        checks: list[CheckResult] = []
        if stage == "tests":
            commands = [("tests", [sys.executable, "-m", "pytest", "tests", "-q", "-W", "error", f"--junitxml={directory}/junit.xml"]),
                        ("contracts", [sys.executable, "scripts/export_contracts.py", "--check"])]
            for name, command in commands:
                result = self.runner.run(command, self.root, f"{directory}/{name}.log", timeout=70 if name == "tests" else 15)
                if result.outcome in {"TIMEOUT", "OUTPUT_LIMIT", "UNEXECUTED"}:
                    raise DomainError(503 if result.outcome == "TIMEOUT" else 422, "worker_" + result.outcome.lower(), result.detail or result.outcome)
                checks.append(CheckResult(id=name, outcome="PASS" if result.outcome == "PASS" else "FAIL", evidence=result.evidence,
                                          detail=f"Command exit code {result.exit_code}"))
        elif stage == "security":
            result = self.runner.run([sys.executable, "scripts/security_checks.py", "--output-dir", directory],
                                     self.root, f"{directory}/collection.log", timeout=100)
            summary = self.root / directory / "summary.json"
            if result.outcome in {"TIMEOUT", "OUTPUT_LIMIT", "UNEXECUTED"} or not summary.exists():
                raise DomainError(503 if result.outcome == "TIMEOUT" else 422, "security_collection_failed", "Security evidence collection did not complete")
            checks = [CheckResult.model_validate(c) for c in json.loads(summary.read_text())["checks"]]
        elif stage == "documentation":
            result = self.runner.run([sys.executable, "scripts/check_docs.py"], self.root, f"{directory}/documentation.log", timeout=30)
            if result.outcome in {"TIMEOUT", "OUTPUT_LIMIT", "UNEXECUTED"}:
                raise DomainError(503 if result.outcome == "TIMEOUT" else 422, "documentation_collection_failed", "Documentation evidence collection did not complete")
            checks = [CheckResult(id="documentation", outcome="PASS" if result.outcome == "PASS" else "FAIL", evidence=result.evidence)]
        else:
            raise ValueError("Stage is not in the fixed execution allowlist")
        after = source_digest(self.root)
        if before != after:
            raise DomainError(409, "source_changed", "Source changed during validation; propose a reviewed change")
        ref = f"{directory}/result.json"
        outcome = StageResult(source_digest=after, checks=checks, evidence_refs=[ref, *[c.evidence for c in checks]])
        (self.root / ref).write_text(outcome.model_dump_json(indent=2) + "\n")
        return outcome


class Worker:
    def __init__(self, store: WorkflowStore, executor: StageExecution) -> None:
        self.store = store
        self.engine = store.engine
        self.executor = executor

    def execute_stage(self, run_id: str, stage: str) -> str:
        try:
            run = self.store.apply(run_id, None, lambda r: self.engine.claim(r, stage, WORKER)[1])
        except DomainError as exc:
            if exc.status == 409:
                return "NOT_CLAIMED"
            raise
        token = run.nodes[stage].lease_token
        if token is None:
            raise RuntimeError("A successful claim must provide a fencing token")
        try:
            if stage != "implementation" and source_digest(self.executor.root) != run.source_digest:
                raise DomainError(409, "source_changed", "Source changed before validation; a reviewed replan is required")
            result = self.executor.execute(run_id, run.revision, stage)
            self.store.apply(run_id, None, lambda r: self.engine.complete(r, stage, token, result, WORKER))
            return "COMPLETED"
        except DomainError as exc:
            if exc.code in {"stale_lease", "lease_expired"}:
                return "STALE_RESULT_DISCARDED"
            classification = "transient" if exc.status == 503 else "permanent"
            try:
                self.store.apply(run_id, None, lambda r: self.engine.fail(r, stage, token, classification, exc.message, WORKER))
            except DomainError as conflict:
                if conflict.status != 409:
                    raise
            return "FAILED"
        except Exception as exc:
            # Unknown adapter failures are permanent, redacted and never treated as success.
            try:
                self.store.apply(run_id, None, lambda r: self.engine.fail(r, stage, token, "permanent", "Adapter error: " + type(exc).__name__, WORKER))
            except DomainError as conflict:
                if conflict.status != 409:
                    raise
            return "FAILED"

    def run(self, run_id: str, *, until_blocked: bool = False) -> dict[str, Any]:
        outcomes: dict[str, str] = {}
        for _ in range(12 if until_blocked else 1):
            run = self.store.get(run_id)
            ready = [name for name, node in run.nodes.items() if node.kind == "task" and node.status == "READY"]
            if not ready:
                retry_times = [n.retry_at for n in run.nodes.values() if n.status == "RETRY_WAIT" and n.retry_at is not None]
                if until_blocked and retry_times and run.status not in {"SAFE_STOP", "REJECTED", "WAITING_CHANGE"}:
                    time.sleep(min(1.0, max(0.0, min(retry_times) - self.engine.clock.now())))
                    continue
                break
            with ThreadPoolExecutor(max_workers=3, thread_name_prefix="validation") as pool:
                futures = {name: pool.submit(self.execute_stage, run_id, name) for name in ready}
                outcomes.update({name: future.result() for name, future in futures.items()})
            if not until_blocked:
                break
        run = self.store.get(run_id)
        return {"run_id": run_id, "status": run.status, "revision": run.revision,
                "executions": outcomes, "policies": self.engine.policy_outcomes(run),
                "next_action": "Inspect the run and address its current gate; the worker never approves it"}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--until-blocked", action="store_true")
    parser.add_argument("--recover", action="store_true", help="Reclaim expired leases without granting approvals")
    args = parser.parse_args()
    root = args.root.resolve()
    if not (root / "pyproject.toml").is_file():
        parser.error("Root must be the trusted assessment repository")
    settings = Settings.from_env(root / ".env")
    if not settings.database_path.is_absolute():
        from dataclasses import replace
        settings = replace(settings, database_path=root / settings.database_path)
    db = Database(settings)
    db.initialize()
    engine = WorkflowEngine(SystemClock())
    secret = settings.audit_key.encode() if settings.audit_key else hashlib.sha256(("audit/v1:" + settings.api_key).encode()).digest()
    store = WorkflowStore(db, engine, secret)
    if args.recover:
        store.apply(args.run_id, None, lambda r: engine.recover(r, OPERATOR))
    runner = CommandRunner((settings.api_key, settings.reviewer_key, settings.audit_key))
    output = Worker(store, StageExecutor(root, runner)).run(args.run_id, until_blocked=args.until_blocked)
    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
