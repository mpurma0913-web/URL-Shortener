"""Execute bounded quality checks. Local success never implies full release readiness."""
from __future__ import annotations

import argparse
import json
import platform
import sqlite3
import sys
import time
from pathlib import Path

from shortener.validation import CommandRunner, hygiene, installed_versions, source_digest

ROOT = Path(__file__).resolve().parents[1]


def run(local: bool, output: str) -> dict:
    directory = (ROOT / output).resolve()
    if not directory.is_relative_to(ROOT):
        raise ValueError("Output must remain in the repository")
    directory.mkdir(parents=True, exist_ok=True)
    runner = CommandRunner()
    started = time.time()
    digest = source_digest(ROOT)
    checks = {}
    # Coverage is explicitly loaded because the bounded runner disables automatic pytest plugins.
    commands = {
        "tests": [sys.executable, "-m", "pytest", "tests", "-q", "-W", "error", "-p", "pytest_cov",
                  "--cov=shortener", "--cov-branch", "--cov-report=term-missing",
                  f"--cov-report=json:{output}/coverage.json", f"--junitxml={output}/junit.xml"],
        "contracts": [sys.executable, "scripts/export_contracts.py", "--check"],
        "documentation": [sys.executable, "scripts/check_docs.py"],
        "security": [sys.executable, "scripts/security_checks.py", "--output-dir", f"{output}/security"],
    }
    if local:
        commands["security"].append("--local")
    for name, command in commands.items():
        checks[name] = runner.run(command, ROOT, f"{output}/{name}.log", timeout=120).as_dict()
    static = hygiene(ROOT)
    local_pass = all(checks[name]["outcome"] == "PASS" for name in ("tests", "contracts", "documentation")) and static["outcome"] == "PASS"
    full_pass = local_pass and checks["security"]["outcome"] == "PASS"
    report = {
        "mode": "local" if local else "full", "started_at_utc_epoch": started,
        "finished_at_utc_epoch": time.time(), "python": platform.python_version(), "sqlite": sqlite3.sqlite_version,
        "source_digest_before": digest, "source_digest_after": source_digest(ROOT),
        "source_unchanged": digest == source_digest(ROOT), "checks": checks, "bounded_static_hygiene": static,
        "local_checks_pass": local_pass, "all_external_checks_pass": full_pass,
        "release_decision": "NOT READY", "human_approval": "PENDING",
        "tools": installed_versions(["fastapi", "pydantic", "uvicorn", "pytest", "pytest-cov", "httpx", "jsonschema", "ruff", "mypy", "pip-audit"]),
        "note": "This command does not approve requirements, architecture, exceptions or release. Unexecuted checks remain blockers.",
    }
    (directory / "summary.json").write_text(json.dumps(report, indent=2) + "\n")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--local", action="store_true")
    parser.add_argument("--output-dir", default="evidence/validation/final")
    args = parser.parse_args()
    outcome = run(args.local, args.output_dir)
    print(json.dumps({key: outcome[key] for key in ("mode", "local_checks_pass", "all_external_checks_pass", "source_unchanged", "release_decision")}, indent=2))
    succeeded = outcome["local_checks_pass"] if args.local else outcome["all_external_checks_pass"]
    raise SystemExit(0 if succeeded and outcome["source_unchanged"] else 2)
