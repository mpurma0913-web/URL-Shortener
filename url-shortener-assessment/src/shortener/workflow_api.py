"""Authenticated governance API. No HTTP endpoint executes commands or supplies test results."""
from __future__ import annotations

import hashlib
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import Depends, FastAPI, Path, Query
from pydantic import Field

from shortener.domain import DomainError, SystemClock
from shortener.workflow_store import WorkflowStore
from shortener.workflows import Actor, Model, RequirementInput, Workflow, WorkflowEngine

RunId = Annotated[UUID, Path(description="Persistent workflow UUID")]


class PolicyOutcome(Model):
    mandatory: bool
    observed: Literal["PASS", "FAIL", "UNEXECUTED", "NOT_APPLICABLE"]
    effective: Literal["PASS", "FAIL"]
    exception_applied: bool
    policy_version: str


class WorkflowResponse(Workflow):
    policy_results: dict[str, PolicyOutcome]


class Versioned(Model):
    expected_version: int = Field(ge=0)


class DecisionRequest(Versioned):
    gate: Literal["clarification", "impact", "requirements", "architecture", "change", "release"]
    approve: bool
    reason: str = Field(min_length=10, max_length=1000)
    acceptance_criteria: list[str] = Field(default_factory=list, max_length=20)
    evidence_refs: list[str] = Field(default_factory=list, max_length=20)


class ChangeRequestBody(Versioned):
    proposed: RequirementInput
    reason: str = Field(min_length=10, max_length=1000)
    impact: list[str] = Field(min_length=1, max_length=20)


class ResumeRequest(Versioned):
    reason: str = Field(min_length=10, max_length=1000)


class ExceptionRequest(Versioned):
    check_id: str = Field(max_length=100)
    reason: str = Field(min_length=10, max_length=1000)
    compensating_control: str = Field(min_length=20, max_length=1000)
    expires_at: float


class ExceptionDecision(Versioned):
    check_id: str = Field(max_length=100)
    approve: bool
    reason: str = Field(min_length=10, max_length=1000)


def public_run(run: Workflow, engine: WorkflowEngine) -> dict[str, Any]:
    data = run.model_dump()
    for node in data["nodes"].values():
        node["lease_token"] = None  # Worker fencing tokens are not exposed through the web console.
    data["policy_results"] = engine.policy_outcomes(run)
    return data


def install_workflow_routes(app: FastAPI) -> None:
    config = app.state.settings
    engine = WorkflowEngine(SystemClock())
    audit_key = getattr(config, "audit_key", "")
    secret = audit_key.encode() if audit_key else hashlib.sha256(("audit/v1:" + config.api_key).encode()).digest()
    store = WorkflowStore(app.state.database, engine, secret)
    app.state.workflow_store = store
    app.state.workflow_engine = engine
    principal = app.state.principal_dependency
    operator = app.state.operator_dependency
    reviewer = app.state.reviewer_dependency
    operator_actor = Actor(role="operator", kind="credential_action", name="operator-key")
    reviewer_actor = Actor(role="reviewer", kind="credential_action", name="reviewer-key")

    @app.post("/api/v1/workflows", response_model=WorkflowResponse, status_code=201, tags=["workflows"], dependencies=[Depends(operator)])
    def create_workflow(body: RequirementInput) -> dict[str, Any]:
        return public_run(store.create(body, operator_actor), engine)

    @app.get("/api/v1/workflows", tags=["workflows"], dependencies=[Depends(principal)])
    def list_workflows(limit: int = Query(default=20, ge=1, le=100)) -> list[dict[str, Any]]:
        return store.list(limit)

    @app.get("/api/v1/workflows/{run_id}", response_model=WorkflowResponse, tags=["workflows"], dependencies=[Depends(principal)])
    def read_workflow(run_id: RunId) -> dict[str, Any]:
        return public_run(store.get(str(run_id)), engine)

    @app.post("/api/v1/workflows/{run_id}/decisions", response_model=WorkflowResponse, tags=["governance"], dependencies=[Depends(reviewer)])
    def decide(run_id: RunId, body: DecisionRequest) -> dict[str, Any]:
        run = store.apply(str(run_id), body.expected_version,
                          lambda r: engine.decide(r, reviewer_actor, body.gate, body.approve, body.reason,
                                                  body.acceptance_criteria, body.evidence_refs))
        return public_run(run, engine)

    @app.post("/api/v1/workflows/{run_id}/changes", response_model=WorkflowResponse, tags=["governance"], dependencies=[Depends(operator)])
    def propose_change(run_id: RunId, body: ChangeRequestBody) -> dict[str, Any]:
        run = store.apply(str(run_id), body.expected_version,
                          lambda r: engine.propose_change(r, body.proposed, operator_actor, body.reason, body.impact))
        return public_run(run, engine)

    @app.post("/api/v1/workflows/{run_id}/recover", response_model=WorkflowResponse, tags=["recovery"], dependencies=[Depends(operator)])
    def recover(run_id: RunId, body: Versioned) -> dict[str, Any]:
        return public_run(store.apply(str(run_id), body.expected_version, lambda r: engine.recover(r, operator_actor)), engine)

    @app.post("/api/v1/workflows/{run_id}/resume", response_model=WorkflowResponse, tags=["recovery"], dependencies=[Depends(reviewer)])
    def resume(run_id: RunId, body: ResumeRequest) -> dict[str, Any]:
        return public_run(store.apply(str(run_id), body.expected_version, lambda r: engine.resume(r, reviewer_actor, body.reason)), engine)

    @app.post("/api/v1/workflows/{run_id}/exceptions", response_model=WorkflowResponse, tags=["governance"], dependencies=[Depends(operator)])
    def request_exception(run_id: RunId, body: ExceptionRequest) -> dict[str, Any]:
        return public_run(store.apply(str(run_id), body.expected_version,
                                     lambda r: engine.request_exception(r, body.check_id, body.reason, body.compensating_control,
                                                                        body.expires_at, operator_actor)), engine)

    @app.post("/api/v1/workflows/{run_id}/exception-decisions", response_model=WorkflowResponse, tags=["governance"], dependencies=[Depends(reviewer)])
    def decide_exception(run_id: RunId, body: ExceptionDecision) -> dict[str, Any]:
        return public_run(store.apply(str(run_id), body.expected_version,
                                     lambda r: engine.decide_exception(r, body.check_id, body.approve, body.reason, reviewer_actor)), engine)

    @app.get("/api/v1/workflows/{run_id}/audit", tags=["evidence"], dependencies=[Depends(principal)])
    def audit(run_id: RunId, after: int = Query(default=0, ge=0), limit: int = Query(default=200, ge=1, le=500)) -> dict[str, Any]:
        return store.audit(str(run_id), after, limit)

    @app.get("/api/v1/workflows/{run_id}/metrics", tags=["evidence"], dependencies=[Depends(principal)])
    def workflow_metrics(run_id: RunId) -> dict[str, Any]:
        return engine.metrics(store.get(str(run_id)))

    @app.get("/api/v1/workflows/{run_id}/candidate", tags=["evidence"], dependencies=[Depends(principal)])
    def candidate(run_id: RunId) -> dict[str, Any]:
        run = store.get(str(run_id))
        if run.candidate_state not in {"CANDIDATE", "RELEASED"} or run.pending_change is not None:
            raise DomainError(410, "candidate_unavailable", "No current candidate manifest is available")
        return {"run_id": run.id, "revision": run.revision, "candidate_state": run.candidate_state,
                "source_digest": run.source_digest, "release_digest": run.release_digest,
                "release_authorized": run.status == "READY", "policy_version": run.policy_version}
