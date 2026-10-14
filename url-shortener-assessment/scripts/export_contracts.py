"""Export/check deterministic OpenAPI, JSON schemas and the versioned policy contract."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from shortener.api import CreateLinkRequest, ErrorResponse, LinkResponse, StatsResponse, create_app
from shortener.config import Settings
from shortener.workflow_api import DecisionRequest, ExceptionDecision, ExceptionRequest, WorkflowResponse
from shortener.workflows import Event, Failure, MANDATORY, EXCEPTION_ELIGIBLE, POLICY_VERSION, PolicyException, RequirementInput, StageResult, Workflow


def contracts() -> dict[str, dict]:
    # Synthetic credentials for schema construction only. No server or database is started.
    settings = Settings(api_key="schema-fixture-operator-0000000000000000", reviewer_key="schema-fixture-reviewer-0000000000000000")
    app = create_app(settings)
    models = [CreateLinkRequest, LinkResponse, StatsResponse, ErrorResponse, RequirementInput, Workflow,
              WorkflowResponse, DecisionRequest, ExceptionRequest, ExceptionDecision, PolicyException, Event, Failure, StageResult]
    output = {"openapi.json": app.openapi(), "policy.json": {"version": POLICY_VERSION,
              "mandatory_checks": sorted(MANDATORY), "exception_eligible": sorted(EXCEPTION_ELIGIBLE),
              "max_exception_seconds": 7 * 86400}}
    for model in models:
        output[model.__name__ + ".schema.json"] = {"$schema": "https://json-schema.org/draft/2020-12/schema", **model.model_json_schema()}
    return output


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    directory = Path(__file__).resolve().parents[1] / "specs/001-url-shortener/contracts"
    directory.mkdir(parents=True, exist_ok=True)
    mismatches = []
    for name, value in contracts().items():
        text = json.dumps(value, indent=2, sort_keys=True) + "\n"
        path = directory / name
        if args.check:
            if not path.exists() or path.read_text() != text:
                mismatches.append(name)
        else:
            path.write_text(text, encoding="utf-8")
    if mismatches:
        print("Contract drift: " + ", ".join(mismatches))
        raise SystemExit(1)
    print("Contract snapshots match implementation." if args.check else "Versioned contract snapshots exported.")
