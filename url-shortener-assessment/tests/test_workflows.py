"""WF-01..08: deterministic test-fixture approvals are not real human sign-off."""
from copy import deepcopy

import pytest

from shortener.domain import DomainError
from shortener.workflows import Actor, CheckResult, RequirementInput, StageResult, WorkflowEngine


class FakeClock:
    def __init__(self):
        self.value = 1700000000.0
    def now(self):
        return self.value
    def advance(self, seconds):
        self.value += seconds


OPERATOR = Actor(role="operator", kind="test_fixture", name="fixture-operator")
REVIEWER = Actor(role="reviewer", kind="test_fixture", name="fixture-reviewer")
WORKER = Actor(role="worker", kind="test_fixture", name="fixture-worker")
DIGEST = "a" * 64


@pytest.fixture
def engine():
    return WorkflowEngine(FakeClock(), lease_seconds=10)


def spec(scenario="greenfield", **kwargs):
    return RequirementInput(scenario=scenario, requirement="Create a public HTTP(S) short link.",
                            acceptance_criteria=["POST returns 201.", "GET returns 302."], **kwargs)


def prepared(engine):
    run, _ = engine.create(spec(), OPERATOR)
    engine.decide(run, REVIEWER, "requirements", True, "Fixture requirements are testable")
    engine.decide(run, REVIEWER, "architecture", True, "Fixture architecture is explicitly approved")
    return run


def result(stage, **changes):
    ids = {"implementation": [], "tests": ["tests", "contracts"],
           "security": ["secret_scan", "lint", "types", "dependency_audit", "license_inventory"],
           "documentation": ["documentation"]}[stage]
    return StageResult(source_digest=DIGEST, checks=[CheckResult(id=x, outcome="PASS", evidence="tests/fixture") for x in ids],
                       evidence_refs=["tests/fixture"], **changes)


def finish(engine, run, stage):
    token, _ = engine.claim(run, stage, WORKER)
    engine.complete(run, stage, token, result(stage), WORKER)


def ready_for_release(engine):
    run = prepared(engine)
    for stage in ["implementation", "tests", "security", "documentation"]:
        finish(engine, run, stage)
    return run


def test_complete_greenfield_skips_only_clarification(engine):
    run, events = engine.create(spec(), OPERATOR)
    assert run.nodes["clarification"].status == "SKIPPED"
    assert run.status == "WAITING_REQUIREMENTS"
    assert any(e.action == "quality_checked" for e in events)
    with pytest.raises(DomainError):
        engine.claim(run, "implementation", WORKER)


def test_ambiguous_input_blocks_until_explicit_reviewer_clarification(engine):
    run, _ = engine.create(RequirementInput(scenario="ambiguous", requirement="Make links expire soon.",
                           acceptance_criteria=[], ambiguities=["Define a default TTL."]), OPERATOR)
    assert run.status == "WAITING_CLARIFICATION"
    with pytest.raises(DomainError):
        engine.decide(run, OPERATOR, "clarification", True, "I approve myself", acceptance_criteria=["Expire after 24 hours"])
    engine.decide(run, REVIEWER, "clarification", True, "Use no implicit TTL; require explicit UTC expiry",
                  acceptance_criteria=["Omitted expiry means no expiry.", "At explicit expires_at return 410."])
    assert run.status == "WAITING_REQUIREMENTS"
    assert run.ambiguities == []
    assert run.original_requirement == "Make links expire soon."
    assert run.acceptance_criteria[0] == "Omitted expiry means no expiry."


def test_brownfield_requires_impact_review(engine):
    run, _ = engine.create(spec("brownfield"), OPERATOR)
    assert run.status == "WAITING_IMPACT"
    with pytest.raises(DomainError):
        engine.decide(run, REVIEWER, "impact", True, "Review without evidence")
    engine.decide(run, REVIEWER, "impact", True, "Reviewed service, schema and regression impact",
                  evidence_refs=["docs/scenarios/brownfield.md"])
    assert run.status == "WAITING_REQUIREMENTS"


def test_parallel_checks_synchronize_before_release(engine):
    run = prepared(engine)
    finish(engine, run, "implementation")
    claims = {stage: engine.claim(run, stage, WORKER)[0] for stage in ["tests", "security", "documentation"]}
    assert all(run.nodes[s].status == "RUNNING" for s in claims)
    engine.complete(run, "tests", claims["tests"], result("tests"), WORKER)
    with pytest.raises(DomainError):
        engine.decide(run, REVIEWER, "release", True, "Too early release request")
    for stage in ["security", "documentation"]:
        engine.complete(run, stage, claims[stage], result(stage), WORKER)
    assert run.status == "WAITING_RELEASE"
    engine.decide(run, REVIEWER, "release", True, "Fixture release explicitly approved")
    assert run.status == "READY"
    assert run.candidate_state == "RELEASED"


def test_stale_or_wrong_lease_is_rejected(engine):
    run = prepared(engine)
    token, _ = engine.claim(run, "implementation", WORKER)
    with pytest.raises(DomainError):
        engine.complete(run, "implementation", "incorrect-token", result("implementation"), WORKER)
    engine.clock.advance(11)
    with pytest.raises(DomainError):
        engine.complete(run, "implementation", token, result("implementation"), WORKER)
    engine.recover(run, OPERATOR)
    assert run.nodes["implementation"].status == "RETRY_WAIT"


def test_retry_budget_safe_stop_compensation_and_human_resume(engine):
    run = prepared(engine)
    finish(engine, run, "implementation")
    for _ in range(3):
        token, _ = engine.claim(run, "tests", WORKER)
        engine.fail(run, "tests", token, "transient", "Fixture dependency unavailable", WORKER)
        engine.clock.advance(5)
    assert run.status == "SAFE_STOP"
    assert run.candidate_state == "WITHDRAWN"
    with pytest.raises(DomainError):
        engine.resume(run, OPERATOR, "Operator cannot self approve recovery")
    engine.resume(run, REVIEWER, "Fixture dependency recovered; bounded retry authorized")
    finish(engine, run, "tests")
    metrics = engine.metrics(run)
    assert metrics["recovered_failures"] == 3
    assert metrics["unrecovered_failures"] == 0
    assert metrics["mttr_seconds"] > 0


def test_permanent_failure_requires_replan_not_blind_resume(engine):
    run = prepared(engine)
    token, _ = engine.claim(run, "implementation", WORKER)
    engine.fail(run, "implementation", token, "permanent", "Fixture invalid source manifest", WORKER)
    with pytest.raises(DomainError):
        engine.resume(run, REVIEWER, "Try again without changing input")
    assert engine.metrics(run)["mttr_seconds"] is None
    assert engine.metrics(run)["unrecovered_failures"] == 1


def test_replan_invalidates_results_and_preserves_decision_lineage(engine):
    run = ready_for_release(engine)
    old_revision = run.revision
    engine.propose_change(run, spec(), OPERATOR, "Add explicit analytics acceptance", ["analytics", "API", "tests"])
    assert run.status == "WAITING_CHANGE"
    events = engine.decide(run, REVIEWER, "change", True, "Fixture change impact reviewed")
    assert run.revision == old_revision + 1
    assert run.status == "WAITING_REQUIREMENTS"
    assert run.nodes["tests"].result is None
    assert run.candidate_state == "WITHDRAWN"
    assert run.history
    assert any(e.action == "replanned" for e in events)


def test_failed_policy_blocks_and_expiring_exception_is_enforced(engine):
    run = prepared(engine)
    finish(engine, run, "implementation")
    finish(engine, run, "tests")
    token, _ = engine.claim(run, "security", WORKER)
    outcome = result("security")
    next(c for c in outcome.checks if c.id == "dependency_audit").outcome = "UNEXECUTED"
    engine.complete(run, "security", token, outcome, WORKER)
    finish(engine, run, "documentation")
    assert run.status == "BLOCKED_POLICY"
    with pytest.raises(DomainError):
        engine.decide(run, REVIEWER, "release", True, "Cannot ignore failed mandatory policy")
    engine.request_exception(run, "dependency_audit", "Fixture offline audit limitation", "Keep service on isolated loopback until audit completes",
                             engine.clock.now() + 100, OPERATOR)
    with pytest.raises(DomainError):
        engine.decide_exception(run, "dependency_audit", True, "Self approval is forbidden", OPERATOR)
    engine.decide_exception(run, "dependency_audit", True, "Fixture risk acceptance for local demonstration only", REVIEWER)
    assert run.status == "WAITING_RELEASE"
    engine.clock.advance(101)
    engine.refresh(run)
    assert run.status == "BLOCKED_POLICY"


def test_mandatory_test_failure_cannot_be_waived(engine):
    run = prepared(engine)
    with pytest.raises(DomainError):
        engine.request_exception(run, "tests", "Skip tests for speed", "No meaningful control is possible",
                                 engine.clock.now() + 100, OPERATOR)


def test_explicit_fallback_is_audited(engine):
    run = prepared(engine)
    token, _ = engine.claim(run, "implementation", WORKER)
    events = engine.complete(run, "implementation", token, result("implementation", mode="fallback",
                             detail="Git absent; filesystem SHA-256 inventory used"), WORKER)
    assert any(e.action == "fallback_used" for e in events)


def test_source_change_invalidates_validation(engine):
    run = prepared(engine)
    finish(engine, run, "implementation")
    token, _ = engine.claim(run, "tests", WORKER)
    changed = result("tests")
    changed.source_digest = "b" * 64
    engine.complete(run, "tests", token, changed, WORKER)
    assert run.status == "SAFE_STOP"
    assert run.candidate_state == "WITHDRAWN"


def test_rejection_is_terminal_and_not_resumeable(engine):
    run = prepared(engine)
    finish(engine, run, "implementation")
    # A release cannot be rejected before its dependencies; reject a new architecture gate instead.
    engine.propose_change(run, spec(), OPERATOR, "Architecture needs a security change", ["security"])
    engine.decide(run, REVIEWER, "change", True, "Change request explicitly accepted")
    engine.decide(run, REVIEWER, "requirements", False, "Requirement rejected by fixture reviewer")
    assert run.status == "REJECTED"
    with pytest.raises(DomainError):
        engine.resume(run, REVIEWER, "Rejected runs cannot silently reopen")


def test_mutating_result_checks_cannot_override_other_stage_policies(engine):
    run = prepared(engine)
    token, _ = engine.claim(run, "implementation", WORKER)
    forged = result("implementation")
    forged.checks = [CheckResult(id="tests", outcome="PASS", evidence="forged")]
    with pytest.raises(DomainError):
        engine.complete(run, "implementation", token, forged, WORKER)
