"""Pure governed workflow state machine; no networking, subprocesses or database access.

The workbench coordinates operator-owned evidence. It does not generate application code
or replace the repository's SpecKit lifecycle. Test-fixture actors remain labeled as such.
"""
from __future__ import annotations

import hashlib
import json
import re
import uuid
from typing import Any, Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field

from shortener.domain import Clock, DomainError

POLICY_VERSION = "assessment-policy/v1"
MAX_ATTEMPTS = 3
MAX_RESUMES = 2
TASK_CHECKS: dict[str, set[str]] = {
    "implementation": set(), "tests": {"tests", "contracts"},
    "security": {"secret_scan", "lint", "types", "dependency_audit", "license_inventory"},
    "documentation": {"documentation"},
}
MANDATORY = {"tests", "contracts", "secret_scan", "lint", "types", "dependency_audit", "documentation"}
EXCEPTION_ELIGIBLE = {"dependency_audit"}
DEPENDENCIES: dict[str, list[str]] = {
    "clarification": [], "impact": ["clarification"],
    "requirements": ["clarification", "impact"], "architecture": ["requirements"],
    "implementation": ["architecture"], "tests": ["implementation"],
    "security": ["implementation"], "documentation": ["implementation"],
    "release": ["tests", "security", "documentation"],
}
GOOD = {"SUCCEEDED", "SKIPPED"}
WorkflowStatus: TypeAlias = Literal["CREATED", "WAITING_CLARIFICATION", "WAITING_IMPACT", "WAITING_REQUIREMENTS", "WAITING_ARCHITECTURE", "READY_TO_EXECUTE", "RUNNING", "WAITING_RELEASE", "BLOCKED_POLICY", "WAITING_CHANGE", "SAFE_STOP", "REJECTED", "READY"]


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Actor(Model):
    role: Literal["operator", "reviewer", "worker"]
    kind: Literal["credential_action", "local_worker", "test_fixture"]
    name: str = Field(min_length=1, max_length=100)


class RequirementInput(Model):
    scenario: Literal["greenfield", "brownfield", "ambiguous"]
    requirement: str = Field(min_length=10, max_length=4000)
    acceptance_criteria: list[str] = Field(default_factory=list, max_length=20)
    ambiguities: list[str] = Field(default_factory=list, max_length=20)


class CheckResult(Model):
    id: str
    outcome: Literal["PASS", "FAIL", "UNEXECUTED", "NOT_APPLICABLE"]
    evidence: str = Field(max_length=500)
    detail: str = Field(default="", max_length=2000)


class StageResult(Model):
    source_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    checks: list[CheckResult] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    mode: Literal["primary", "fallback"] = "primary"
    detail: str = Field(default="", max_length=2000)


class Stage(Model):
    name: str
    kind: Literal["approval", "task"]
    dependencies: list[str]
    status: Literal["PENDING", "READY", "WAITING_APPROVAL", "RUNNING", "RETRY_WAIT", "SUCCEEDED", "SKIPPED", "FAILED"] = "PENDING"
    attempts: int = 0
    total_attempts: int = 0
    lease_token: str | None = None
    started_at: float | None = None
    deadline_at: float | None = None
    retry_at: float | None = None
    completed_at: float | None = None
    result: StageResult | None = None


class Failure(Model):
    id: str
    stage: str
    revision: int
    classification: Literal["transient", "permanent", "timeout"]
    reason: str
    detected_at: float
    recovery_started_at: float | None = None
    recovered_at: float | None = None
    mechanism: str | None = None


class PolicyException(Model):
    check_id: str
    reason: str
    compensating_control: str
    expires_at: float
    requested_by: Actor
    requested_at: float
    revision: int
    status: Literal["EXCEPTION-REQUESTED", "APPROVED", "REJECTED"] = "EXCEPTION-REQUESTED"
    reviewed_by: Actor | None = None
    reviewed_at: float | None = None
    review_reason: str | None = None


class ChangeRequest(Model):
    proposed: RequirementInput
    reason: str
    impact: list[str]
    requested_at: float
    requested_by: Actor


class Event(Model):
    action: str
    timestamp: float
    actor: Actor
    revision: int
    result: str
    reason: str
    stage: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class Workflow(Model):
    id: str
    version: int = 0
    revision: int = 1
    policy_version: str = POLICY_VERSION
    scenario: Literal["greenfield", "brownfield", "ambiguous"]
    provenance: Literal["credential_action", "test_fixture"]
    original_requirement: str
    requirement: str
    acceptance_criteria: list[str]
    ambiguities: list[str]
    nodes: dict[str, Stage]
    created_at: float
    updated_at: float
    status: WorkflowStatus = "CREATED"
    candidate_state: Literal["NONE", "CANDIDATE", "WITHDRAWN", "RELEASED"] = "NONE"
    source_digest: str | None = None
    release_digest: str | None = None
    failures: list[Failure] = Field(default_factory=list)
    exceptions: dict[str, PolicyException] = Field(default_factory=dict)
    pending_change: ChangeRequest | None = None
    history: list[dict[str, Any]] = Field(default_factory=list)
    stop_classification: str | None = None
    resume_count: int = 0
    audit_sequence: int = 0
    audit_head: str = "0" * 64


def role_required(actor: Actor, role: str) -> None:
    if actor.role != role:
        raise DomainError(403, "workflow_forbidden", f"This operation requires the {role} role")


def reference_valid(value: str) -> bool:
    return bool(value and len(value) <= 300 and not value.startswith(("/", "\\"))
                and ".." not in value.split("/") and "\\" not in value and ":" not in value)


class WorkflowEngine:
    def __init__(self, clock: Clock, lease_seconds: int = 120) -> None:
        if not 1 <= lease_seconds <= 900:
            raise ValueError("Lease duration must be in 1..900 seconds")
        self.clock = clock
        self.lease_seconds = lease_seconds

    def event(self, run: Workflow, actor: Actor, action: str, result: str, reason: str,
              stage: str | None = None, **details: Any) -> Event:
        return Event(action=action, timestamp=self.clock.now(), actor=actor, revision=run.revision,
                     result=result, reason=reason, stage=stage, details=details)

    def quality_questions(self, spec: RequirementInput) -> list[str]:
        questions = [s.strip() for s in spec.ambiguities if s.strip()]
        if not spec.acceptance_criteria or not all(s.strip() for s in spec.acceptance_criteria):
            questions.append("Provide observable acceptance criteria, including negative behavior.")
        if any(len(s) > 500 for s in [*spec.acceptance_criteria, *spec.ambiguities]):
            raise DomainError(422, "requirement_too_large", "Each criterion or ambiguity is limited to 500 characters")
        text = " ".join([spec.requirement, *spec.acceptance_criteria])
        terms = sorted(set(re.findall(r"\b(?:soon|quickly|better|user-friendly|asap)\b", text, re.I)))
        if terms:
            questions.append("Replace subjective terms with measurable decisions: " + ", ".join(terms))
        return list(dict.fromkeys(questions))

    def create(self, spec: RequirementInput, actor: Actor) -> tuple[Workflow, list[Event]]:
        role_required(actor, "operator")
        questions = self.quality_questions(spec)
        now = self.clock.now()
        nodes = {name: Stage(name=name, kind="task" if name in TASK_CHECKS else "approval", dependencies=deps.copy())
                 for name, deps in DEPENDENCIES.items()}
        if not questions:
            nodes["clarification"].status = "SKIPPED"
        if spec.scenario != "brownfield":
            nodes["impact"].status = "SKIPPED"
        run = Workflow(id=str(uuid.uuid4()), scenario=spec.scenario,
                       provenance="test_fixture" if actor.kind == "test_fixture" else "credential_action",
                       original_requirement=spec.requirement, requirement=spec.requirement,
                       acceptance_criteria=spec.acceptance_criteria, ambiguities=questions,
                       nodes=nodes, created_at=now, updated_at=now)
        self.refresh(run)
        return run, [self.event(run, actor, "created", "PASS", "Structured requirement ingested", policy_version=POLICY_VERSION),
                     self.event(run, actor, "quality_checked", "CLARIFICATION_REQUIRED" if questions else "PASS",
                                "Material questions require explicit clarification" if questions else "Complete structured input: no artificial clarification gate",
                                questions=questions, checks=["acceptance criteria present", "bounded fields", "subjective-term scan", "scenario branch"])]

    def policy_outcomes(self, run: Workflow) -> dict[str, dict[str, Any]]:
        now = self.clock.now()
        observed = {check.id: check for node in run.nodes.values() if node.result for check in node.result.checks}
        output: dict[str, dict[str, Any]] = {}
        for name in sorted(MANDATORY | {"license_inventory"}):
            check = observed.get(name)
            outcome = check.outcome if check else "UNEXECUTED"
            exception = run.exceptions.get(name)
            waived = bool(exception and exception.status == "APPROVED" and exception.expires_at > now and exception.revision == run.revision)
            output[name] = {"mandatory": name in MANDATORY, "observed": outcome,
                            "effective": "PASS" if outcome == "PASS" or waived else "FAIL",
                            "exception_applied": waived, "policy_version": run.policy_version}
        return output

    def refresh(self, run: Workflow) -> None:
        if run.status in {"SAFE_STOP", "REJECTED"}:
            return
        if run.pending_change is not None:
            run.status = "WAITING_CHANGE"
            return
        now = self.clock.now()
        for stage in run.nodes.values():
            if stage.status == "RETRY_WAIT" and stage.retry_at is not None and now >= stage.retry_at:
                stage.status = "READY"
            if stage.status == "PENDING" and all(run.nodes[d].status in GOOD for d in stage.dependencies):
                stage.status = "READY" if stage.kind == "task" else "WAITING_APPROVAL"
        release = run.nodes["release"]
        if release.status in {"WAITING_APPROVAL", "SUCCEEDED"}:
            failed = any(p["mandatory"] and p["effective"] != "PASS" for p in self.policy_outcomes(run).values())
            if failed:
                run.status = "BLOCKED_POLICY"
                if run.candidate_state == "RELEASED":
                    run.candidate_state = "WITHDRAWN"
                return
        if release.status == "SUCCEEDED":
            run.status = "READY"
            return
        gates: dict[str, WorkflowStatus] = {"clarification": "WAITING_CLARIFICATION", "impact": "WAITING_IMPACT",
                 "requirements": "WAITING_REQUIREMENTS", "architecture": "WAITING_ARCHITECTURE", "release": "WAITING_RELEASE"}
        for name, status in gates.items():
            if run.nodes[name].status == "WAITING_APPROVAL":
                run.status = status
                return
        run.status = "RUNNING" if any(n.status in {"RUNNING", "RETRY_WAIT"} for n in run.nodes.values()) else "READY_TO_EXECUTE"

    def decide(self, run: Workflow, actor: Actor, gate: str, approve: bool, reason: str,
               acceptance_criteria: list[str] | None = None, evidence_refs: list[str] | None = None) -> list[Event]:
        role_required(actor, "reviewer")
        if len(reason.strip()) < 10:
            raise DomainError(422, "decision_reason_required", "Provide a meaningful decision rationale")
        if gate == "change":
            return self._decide_change(run, actor, approve, reason)
        self.refresh(run)
        stage = run.nodes.get(gate)
        if (run.status in {"SAFE_STOP", "REJECTED", "WAITING_CHANGE", "BLOCKED_POLICY", "READY"}
                or stage is None or stage.kind != "approval" or stage.status != "WAITING_APPROVAL"):
            raise DomainError(409, "gate_not_ready", "This approval gate is not ready")
        events: list[Event] = []
        if not approve:
            stage.status = "FAILED"
            run.status = "REJECTED"
            self._withdraw(run, actor, events, "Reviewer rejected the candidate")
            events.append(self.event(run, actor, "rejected", "REJECTED", reason, gate))
            return events
        if gate == "clarification":
            criteria = acceptance_criteria or []
            if not criteria or any(not x.strip() or len(x) > 500 for x in criteria):
                raise DomainError(422, "clarification_required", "Supply explicit replacement acceptance criteria")
            if re.search(r"\b(?:soon|quickly|better|asap)\b", " ".join(criteria), re.I):
                raise DomainError(422, "clarification_incomplete", "Replacement criteria remain subjective")
            run.history.append({"revision": run.revision, "event": "clarification", "questions": run.ambiguities.copy(),
                                "previous_criteria": run.acceptance_criteria.copy(), "decision": reason})
            run.acceptance_criteria = criteria.copy()
            run.ambiguities = []
            events.append(self.event(run, actor, "clarified", "PASS", reason, gate, replacement_criteria=criteria))
        if gate == "impact" and (not evidence_refs or not all(reference_valid(r) for r in evidence_refs)):
            raise DomainError(422, "impact_evidence_required", "Brownfield approval requires relative impact-analysis evidence paths")
        if gate == "release":
            if run.candidate_state != "CANDIDATE" or run.source_digest is None:
                raise DomainError(409, "candidate_not_ready", "A current, validated candidate is required")
            run.release_digest = hashlib.sha256(json.dumps({"source": run.source_digest, "revision": run.revision,
                                                           "policies": self.policy_outcomes(run)}, sort_keys=True).encode()).hexdigest()
            run.candidate_state = "RELEASED"
        stage.status = "SUCCEEDED"
        stage.completed_at = self.clock.now()
        events.append(self.event(run, actor, "approved", "PASS", reason, gate, evidence_refs=evidence_refs or []))
        run.updated_at = self.clock.now()
        self.refresh(run)
        return events

    def claim(self, run: Workflow, stage_name: str, actor: Actor) -> tuple[str, list[Event]]:
        role_required(actor, "worker")
        self.refresh(run)
        stage = run.nodes.get(stage_name)
        if (run.status in {"SAFE_STOP", "REJECTED", "WAITING_CHANGE", "READY", "BLOCKED_POLICY"}
                or stage is None or stage.kind != "task" or stage.status != "READY"):
            raise DomainError(409, "stage_not_ready", "This stage cannot be claimed")
        if stage.attempts >= MAX_ATTEMPTS:
            raise DomainError(409, "retry_budget_exhausted", "Retry budget is exhausted")
        now = self.clock.now()
        token = str(uuid.uuid4())
        stage.status = "RUNNING"
        stage.attempts += 1
        stage.total_attempts += 1
        stage.lease_token = token
        stage.started_at = now
        stage.deadline_at = now + self.lease_seconds
        for failure in run.failures:
            if failure.stage == stage_name and failure.revision == run.revision and failure.recovered_at is None:
                if failure.recovery_started_at is None:
                    failure.recovery_started_at = now
        run.updated_at = now
        self.refresh(run)
        return token, [self.event(run, actor, "stage_claimed", "RUNNING", "Bounded worker lease acquired", stage_name,
                                  attempt=stage.attempts, deadline_at=stage.deadline_at)]

    def _active_lease(self, run: Workflow, stage_name: str, token: str, *, allow_expired: bool = False) -> Stage:
        stage = run.nodes.get(stage_name)
        if (run.status in {"SAFE_STOP", "REJECTED", "WAITING_CHANGE", "READY"} or stage is None
                or stage.status != "RUNNING" or stage.lease_token != token):
            raise DomainError(409, "stale_lease", "This worker result is stale or not owned by the active lease")
        if not allow_expired and (stage.deadline_at is None or self.clock.now() >= stage.deadline_at):
            raise DomainError(409, "lease_expired", "Worker lease expired; run controlled recovery")
        return stage

    def complete(self, run: Workflow, stage_name: str, token: str, result: StageResult, actor: Actor) -> list[Event]:
        role_required(actor, "worker")
        stage = self._active_lease(run, stage_name, token)
        ids = [c.id for c in result.checks]
        if set(ids) != TASK_CHECKS[stage_name] or len(ids) != len(set(ids)):
            raise DomainError(422, "invalid_stage_checks", "Stage result must contain exactly its assigned policy checks")
        if not result.evidence_refs or not all(reference_valid(p) for p in result.evidence_refs):
            raise DomainError(422, "invalid_evidence_reference", "Evidence must use bounded repository-relative paths")
        if stage_name != "implementation" and result.source_digest != run.source_digest:
            return self.fail(run, stage_name, token, "permanent", "Source changed after the candidate snapshot", actor)
        now = self.clock.now()
        stage.result = result
        stage.status = "SUCCEEDED"
        stage.completed_at = now
        stage.lease_token = None
        stage.deadline_at = None
        if stage_name == "implementation":
            run.source_digest = result.source_digest
            run.candidate_state = "CANDIDATE"
        events = [self.event(run, actor, "stage_completed", "PASS", "Evidence collection completed; policy outcomes are evaluated separately", stage_name,
                             source_digest=result.source_digest, checks=[c.model_dump() for c in result.checks])]
        if result.mode == "fallback":
            events.append(self.event(run, actor, "fallback_used", "PASS", result.detail, stage_name))
        for failure in run.failures:
            if failure.stage == stage_name and failure.revision == run.revision and failure.recovered_at is None:
                failure.recovered_at = now
                failure.mechanism = "reviewer_resume" if run.resume_count else "bounded_retry"
                events.append(self.event(run, actor, "recovered", "PASS", "Failure recovered by successful stage completion", stage_name,
                                         failure_id=failure.id, duration_seconds=max(0.0, now - failure.detected_at)))
        run.updated_at = now
        self.refresh(run)
        if run.status == "BLOCKED_POLICY":
            events.append(self.event(run, actor, "policy_blocked", "FAIL", "Mandatory validation is failed or unexecuted", policies=self.policy_outcomes(run)))
        return events

    def _withdraw(self, run: Workflow, actor: Actor, events: list[Event], reason: str) -> None:
        if run.candidate_state in {"CANDIDATE", "RELEASED"}:
            run.candidate_state = "WITHDRAWN"
            run.release_digest = None
            events.append(self.event(run, actor, "compensated", "PASS", reason,
                                     compensation="withdraw_candidate_manifest", source_digest=run.source_digest))

    def fail(self, run: Workflow, stage_name: str, token: str, classification: str, reason: str, actor: Actor,
             *, allow_expired: bool = False) -> list[Event]:
        if actor.role not in {"worker", "operator"}:
            raise DomainError(403, "workflow_forbidden", "Only a worker or recovery operator may record task failure")
        if classification not in {"transient", "permanent", "timeout"}:
            raise DomainError(422, "invalid_failure_class", "Failure classification is not recognized")
        stage = self._active_lease(run, stage_name, token, allow_expired=allow_expired)
        now = self.clock.now()
        failure = Failure.model_validate({"id": str(uuid.uuid4()), "stage": stage_name, "revision": run.revision,
                                         "classification": classification, "reason": reason, "detected_at": now})
        run.failures.append(failure)
        stage.lease_token = None
        stage.deadline_at = None
        events = [self.event(run, actor, "stage_failed", "FAIL", reason, stage_name,
                             classification=classification, failure_id=failure.id)]
        if classification != "permanent" and stage.attempts < MAX_ATTEMPTS:
            stage.status = "RETRY_WAIT"
            stage.retry_at = now + 0.25 * (2 ** (stage.attempts - 1))
            events.append(self.event(run, actor, "retry_scheduled", "RETRY_WAIT", "Retry remains inside the configured budget", stage_name,
                                     retry_at=stage.retry_at, max_attempts=MAX_ATTEMPTS))
        else:
            stage.status = "FAILED"
            run.status = "SAFE_STOP"
            run.stop_classification = classification
            # Other validation tasks are read-only; invalidate their leases and reject late results.
            for other in run.nodes.values():
                if other.status in {"RUNNING", "READY", "RETRY_WAIT"}:
                    other.status = "FAILED"
                    other.lease_token = None
                    other.deadline_at = None
            self._withdraw(run, actor, events, "Candidate withdrawn after unsafe or exhausted execution")
            events.append(self.event(run, actor, "safe_stopped", "SAFE_STOP", "Automatic continuation is prohibited", stage_name))
        run.updated_at = now
        self.refresh(run)
        return events

    def recover(self, run: Workflow, actor: Actor) -> list[Event]:
        role_required(actor, "operator")
        events: list[Event] = []
        for stage in run.nodes.values():
            if (stage.status == "RUNNING" and stage.deadline_at is not None
                    and self.clock.now() >= stage.deadline_at and stage.lease_token is not None):
                events.extend(self.fail(run, stage.name, stage.lease_token, "timeout", "Worker lease expired after interruption",
                                        actor, allow_expired=True))
                if run.status == "SAFE_STOP":
                    break
        if not events:
            events.append(self.event(run, actor, "recovery_checked", "NOT_APPLICABLE", "No expired leases found"))
        self.refresh(run)
        return events

    def resume(self, run: Workflow, actor: Actor, reason: str) -> list[Event]:
        role_required(actor, "reviewer")
        if (run.status != "SAFE_STOP" or run.stop_classification not in {"transient", "timeout"}
                or run.resume_count >= MAX_RESUMES):
            raise DomainError(409, "resume_not_allowed", "Only bounded recovery of retryable safe-stops is allowed; otherwise propose a reviewed change")
        if len(reason.strip()) < 10:
            raise DomainError(422, "decision_reason_required", "Document the recovery rationale")
        run.resume_count += 1
        for node in run.nodes.values():
            if node.kind == "task" and node.status == "FAILED":
                node.status = "PENDING"
                node.attempts = 0
                node.retry_at = None
        run.status = "CREATED"
        run.stop_classification = None
        if run.source_digest:
            run.candidate_state = "CANDIDATE"
        self.refresh(run)
        return [self.event(run, actor, "resumed", "PASS", reason, resume_count=run.resume_count, attempts_per_epoch=MAX_ATTEMPTS)]

    def propose_change(self, run: Workflow, proposed: RequirementInput, actor: Actor, reason: str, impact: list[str]) -> list[Event]:
        role_required(actor, "operator")
        if run.status == "REJECTED" or run.pending_change is not None or run.revision >= 10:
            raise DomainError(409, "change_not_allowed", "This run cannot accept another change request")
        if len(reason.strip()) < 10 or not impact or any(not item.strip() or len(item) > 300 for item in impact):
            raise DomainError(422, "change_impact_required", "Provide a rationale and explicit affected components")
        self.quality_questions(proposed)
        run.pending_change = ChangeRequest(proposed=proposed, reason=reason, impact=impact,
                                           requested_at=self.clock.now(), requested_by=actor)
        run.status = "WAITING_CHANGE"
        return [self.event(run, actor, "change_requested", "WAITING_CHANGE", reason, impact=impact)]

    def _decide_change(self, run: Workflow, actor: Actor, approve: bool, reason: str) -> list[Event]:
        change = run.pending_change
        if change is None:
            raise DomainError(409, "change_not_pending", "No change request awaits approval")
        if not approve:
            run.pending_change = None
            run.status = "SAFE_STOP" if run.stop_classification else "CREATED"
            self.refresh(run)
            return [self.event(run, actor, "change_rejected", "REJECTED", reason)]
        events: list[Event] = []
        old_revision = run.revision
        run.history.append({"revision": old_revision, "requirement": run.requirement,
                            "criteria": run.acceptance_criteria.copy(), "nodes": {n: s.model_dump() for n, s in run.nodes.items()},
                            "exceptions": {n: e.model_dump() for n, e in run.exceptions.items()},
                            "impact": change.impact, "decision": reason})
        self._withdraw(run, actor, events, "Approved upstream change invalidates the previous candidate")
        new, _ = self.create(change.proposed, Actor(role="operator", kind=actor.kind, name=actor.name))
        run.revision += 1
        run.scenario = new.scenario
        run.requirement = new.requirement
        run.acceptance_criteria = new.acceptance_criteria
        run.ambiguities = new.ambiguities
        run.nodes = new.nodes
        run.source_digest = None
        run.release_digest = None
        run.exceptions = {}
        run.pending_change = None
        run.stop_classification = None
        run.resume_count = 0
        run.status = "CREATED"
        self.refresh(run)
        events.append(self.event(run, actor, "replanned", "PASS", reason, previous_revision=old_revision,
                                 invalidated=list(DEPENDENCIES), impact=change.impact))
        return events

    def request_exception(self, run: Workflow, check_id: str, reason: str, compensating_control: str,
                          expires_at: float, actor: Actor) -> list[Event]:
        role_required(actor, "operator")
        now = self.clock.now()
        if check_id not in EXCEPTION_ELIGIBLE:
            raise DomainError(409, "policy_not_waivable", "This policy cannot be waived")
        if not now < expires_at <= now + 7 * 86400 or len(reason.strip()) < 10 or len(compensating_control.strip()) < 20:
            raise DomainError(422, "invalid_exception", "Exception needs a reason, compensating control and expiry within seven days")
        if run.status in {"REJECTED", "READY", "WAITING_CHANGE"}:
            raise DomainError(409, "exception_not_allowed", "Cannot change policy exceptions in this state")
        run.exceptions[check_id] = PolicyException(check_id=check_id, reason=reason,
                                                 compensating_control=compensating_control, expires_at=expires_at,
                                                 requested_by=actor, requested_at=now, revision=run.revision)
        return [self.event(run, actor, "exception_requested", "EXCEPTION-REQUESTED", reason,
                           check_id=check_id, compensating_control=compensating_control, expires_at=expires_at)]

    def decide_exception(self, run: Workflow, check_id: str, approve: bool, reason: str, actor: Actor) -> list[Event]:
        role_required(actor, "reviewer")
        exception = run.exceptions.get(check_id)
        if exception is None or exception.status != "EXCEPTION-REQUESTED" or exception.expires_at <= self.clock.now():
            raise DomainError(409, "exception_not_pending", "No unexpired exception awaits review")
        if len(reason.strip()) < 10:
            raise DomainError(422, "decision_reason_required", "Document the policy decision rationale")
        exception.status = "APPROVED" if approve else "REJECTED"
        exception.reviewed_by = actor
        exception.reviewed_at = self.clock.now()
        exception.review_reason = reason
        self.refresh(run)
        return [self.event(run, actor, "exception_decided", exception.status, reason, check_id=check_id)]

    def metrics(self, run: Workflow) -> dict[str, Any]:
        recovered = [f for f in run.failures if f.recovered_at is not None]
        durations = [max(0.0, float(f.recovered_at) - f.detected_at) for f in recovered if f.recovered_at is not None]
        total = sum(durations)
        return {"population": "this_workflow_all_revisions", "provenance": run.provenance,
                "production_statistics": False, "failures": len(run.failures),
                "recovered_failures": len(recovered), "unrecovered_failures": len(run.failures) - len(recovered),
                "total_recovery_seconds": total, "mttr_seconds": total / len(recovered) if recovered else None,
                "individual_recovery_seconds": durations, "resume_count": run.resume_count,
                "stage_attempts_current_revision": sum(s.total_attempts for s in run.nodes.values()),
                "elapsed_seconds": max(0.0, self.clock.now() - run.created_at),
                "exclusions": ["Unrecovered failures excluded from MTTR denominator", "No production workload", "Wall-clock timestamps may be affected by clock correction"]}
