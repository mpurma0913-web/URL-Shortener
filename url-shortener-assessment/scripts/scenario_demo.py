"""Reproduce three governed scenarios with real worker commands and explicitly labeled test actors.

Fixture approvals are NOT human sign-off. Unavailable security tools remain UNEXECUTED.
"""
from __future__ import annotations

import json
import secrets
import tempfile
from pathlib import Path

from shortener.config import Settings
from shortener.domain import SystemClock
from shortener.storage import Database
from shortener.validation import CommandRunner, source_digest
from shortener.worker import StageExecutor, Worker
from shortener.workflow_store import WorkflowStore
from shortener.workflows import Actor, CheckResult, RequirementInput, StageResult, WorkflowEngine

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "evidence/scenarios"
OPERATOR = Actor(role="operator", kind="test_fixture", name="scenario-fixture-operator")
REVIEWER = Actor(role="reviewer", kind="test_fixture", name="scenario-fixture-reviewer-not-human")
WORKER = Actor(role="worker", kind="test_fixture", name="recovery-fixture-worker")
CRITERIA = ["Valid HTTP(S) creation returns 201.", "Invalid schemes return 422.",
            "GET returns 302 and increments a UTC aggregate counter.", "HEAD never increments analytics."]


class FixtureClock:
    def __init__(self) -> None:
        self.value = 1700000000.0

    def now(self) -> float:
        return self.value

    def advance(self, amount: float) -> None:
        self.value += amount


def database(directory: Path) -> Database:
    config = Settings(database_path=directory / "scenario.db", api_key=secrets.token_urlsafe(40),
                      reviewer_key=secrets.token_urlsafe(40), audit_key=secrets.token_urlsafe(40))
    db = Database(config)
    db.initialize()
    return db


def decide(store: WorkflowStore, run_id: str, gate: str, *, criteria: list[str] | None = None) -> None:
    current = store.get(run_id)
    store.apply(run_id, current.version, lambda r: store.engine.decide(
        r, REVIEWER, gate, True, "Automated fixture decision to exercise mechanism; not a human approval",
        acceptance_criteria=criteria, evidence_refs=["docs/scenarios/brownfield.md"] if gate == "impact" else []))


def scenario(name: str) -> dict:
    with tempfile.TemporaryDirectory(prefix="scenario-") as temporary:
        engine = WorkflowEngine(SystemClock())
        store = WorkflowStore(database(Path(temporary)), engine, secrets.token_bytes(40))
        requirement = RequirementInput(scenario=name,
            requirement="Make links expire soon." if name == "ambiguous" else
                        "Add optional keyed creation replay without changing no-key behavior." if name == "brownfield" else
                        "Create a public HTTP(S) short link with counted redirects.",
            acceptance_criteria=[] if name == "ambiguous" else CRITERIA + (["Matching key replays the original resource; changed payload returns 409."] if name == "brownfield" else []),
            ambiguities=["Define default TTL, expiry boundary and existing-link treatment."] if name == "ambiguous" else [])
        run = store.create(requirement, OPERATOR)
        worker = Worker(store, StageExecutor(ROOT, CommandRunner()))
        snapshots = {"before_any_fixture_approval": run.model_dump()}
        blocked_attempt = worker.run(run.id, until_blocked=True)
        if blocked_attempt["executions"]:
            raise RuntimeError("Worker crossed an approval gate")
        if name == "ambiguous":
            resolved = ["Omitted expiry means no automatic expiry.", "Explicit aware expiry must be future and within 365 days.",
                        "At or after explicit expiry return 410.", "Do not change existing links retroactively."]
            decide(store, run.id, "clarification", criteria=resolved)
            snapshots["after_fixture_clarification"] = store.get(run.id).model_dump()
            proposed = RequirementInput(scenario="ambiguous", requirement="Require explicit UTC-aware expiry; omission keeps a link active until revocation.",
                                        acceptance_criteria=resolved)
            current = store.get(run.id)
            store.apply(run.id, current.version, lambda r: engine.propose_change(
                r, proposed, OPERATOR, "Fixture formalizes the ambiguous request as explicit acceptance criteria",
                ["specs/001-url-shortener/spec.md", "src/shortener/domain.py", "tests/test_security_reliability.py", "docs/architecture/api.md"]))
            snapshots["waiting_change_approval"] = store.get(run.id).model_dump()
            decide(store, run.id, "change")
            snapshots["after_reviewed_fixture_replan"] = store.get(run.id).model_dump()
        if name == "brownfield":
            decide(store, run.id, "impact")
            snapshots["after_fixture_impact_review"] = store.get(run.id).model_dump()
        decide(store, run.id, "requirements")
        decide(store, run.id, "architecture")
        outcome = worker.run(run.id, until_blocked=True)
        final = store.get(run.id)
        audit = store.audit(run.id, limit=500)
        if not audit["verified"]:
            raise RuntimeError("Scenario audit failed verification")
        report = {"scenario": name, "provenance": "test_fixture", "human_approval_claimed": False,
                  "execution_note": "Approvals are test actors; worker test/contract/security/documentation commands are actual executions.",
                  "initial_input": requirement.model_dump(), "snapshots": snapshots,
                  "blocked_worker_attempt": blocked_attempt, "actual_worker_result": outcome,
                  "final_workflow": final.model_dump(), "audit": audit, "metrics": engine.metrics(final),
                  "limitation": "A policy block is a truthful terminal observation, not a failed claim of human-approved release."}
        (OUTPUT / f"{name}.json").write_text(json.dumps(report, indent=2) + "\n")
        return {"scenario": name, "run_id": run.id, "state": final.status, "audit_verified": audit["verified"],
                "human_approval": "NOT_CLAIMED", "actual_worker_tasks": outcome["executions"]}


def recovery_fixture() -> dict:
    with tempfile.TemporaryDirectory(prefix="recovery-fixture-") as temporary:
        clock = FixtureClock()
        engine = WorkflowEngine(clock, lease_seconds=10)
        store = WorkflowStore(database(Path(temporary)), engine, secrets.token_bytes(40))
        run = store.create(RequirementInput(scenario="greenfield", requirement="Demonstrate bounded recovery using injected fixture failures.",
                                          acceptance_criteria=["Three transient failures safe-stop and withdraw the candidate.",
                                                               "A reviewer fixture resumes; recovered failures alone contribute to MTTR."]), OPERATOR)
        decide(store, run.id, "requirements")
        decide(store, run.id, "architecture")
        digest = source_digest(ROOT)
        def claim(stage: str) -> str:
            current = store.apply(run.id, None, lambda r: engine.claim(r, stage, WORKER)[1])
            token = current.nodes[stage].lease_token
            if token is None:
                raise RuntimeError("Missing fixture lease")
            return token
        token = claim("implementation")
        store.apply(run.id, None, lambda r: engine.complete(r, "implementation", token,
                    StageResult(source_digest=digest, evidence_refs=["tests/test_workflows.py"], detail="Fixture source result; not an external execution"), WORKER))
        for _ in range(3):
            token = claim("tests")
            store.apply(run.id, None, lambda r: engine.fail(r, "tests", token, "transient", "Injected fixture dependency failure", WORKER))
            clock.advance(5)
        stopped = store.get(run.id)
        stopped_metrics = engine.metrics(stopped)
        store.apply(run.id, stopped.version, lambda r: engine.resume(r, REVIEWER, "Fixture dependency recovery explicitly authorizes bounded resumption"))
        token = claim("tests")
        result = StageResult(source_digest=digest, evidence_refs=["tests/test_workflows.py"],
                             checks=[CheckResult(id=name, outcome="PASS", evidence="tests/test_workflows.py", detail="Fixture state transition only; not tool execution") for name in ("tests", "contracts")])
        store.apply(run.id, None, lambda r: engine.complete(r, "tests", token, result, WORKER))
        final = store.get(run.id)
        report = {"provenance": "test_fixture", "clock": "deterministic simulated clock", "failure_inputs": "injected transient errors",
                  "external_test_results": "FIXTURES in this recovery record only; use the three scenario worker logs for actual test commands",
                  "human_approval_claimed": False, "before_resume": stopped.model_dump(), "before_metrics": stopped_metrics,
                  "after_resume": final.model_dump(), "after_metrics": engine.metrics(final), "audit": store.audit(run.id),
                  "production_statistics": False}
        (OUTPUT / "recovery.json").write_text(json.dumps(report, indent=2) + "\n")
        return report["after_metrics"]


if __name__ == "__main__":
    OUTPUT.mkdir(parents=True, exist_ok=True)
    results = [scenario(name) for name in ("greenfield", "brownfield", "ambiguous")]
    recovery = recovery_fixture()
    report = {"scenarios": results, "recovery_fixture": recovery, "formal_release_decision": "NOT READY", "human_signoff": "PENDING"}
    (OUTPUT / "index.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
