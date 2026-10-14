"""Collect security/static-tool outcomes; uninstalled tools remain UNEXECUTED."""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path

from shortener.validation import CommandRunner, hygiene, installed_versions, license_inventory, source_digest


def collect(root: Path, output_dir: str, *, local_only: bool = False) -> dict:
    destination = root / output_dir
    destination.mkdir(parents=True, exist_ok=True)
    checks = []
    local = hygiene(root)
    hygiene_path = f"{output_dir}/hygiene.json"
    (root / hygiene_path).write_text(json.dumps(local, indent=2) + "\n", encoding="utf-8")
    checks.append({"id": "secret_scan", "outcome": local["outcome"], "evidence": hygiene_path,
                   "detail": "Bounded AST/secret-pattern check; not a professional penetration test"})
    commands = {
        "lint": ("ruff", [sys.executable, "-m", "ruff", "check", "src", "tests", "scripts"]),
        "types": ("mypy", [sys.executable, "-m", "mypy", "src/shortener"]),
        "dependency_audit": ("pip_audit", [sys.executable, "-m", "pip_audit", "--disable-pip", "--no-deps", "-r", "requirements.lock", "--format", "json"]),
    }
    runner = CommandRunner()
    for name, (module, command) in commands.items():
        evidence = f"{output_dir}/{name}.log"
        if local_only or importlib.util.find_spec(module) is None:
            outcome = "UNEXECUTED"
            detail = "Local-only mode" if local_only else f"Required tool {module} is not installed"
            (root / evidence).write_text(detail + "\nNo successful result is claimed.\n", encoding="utf-8")
        else:
            result = runner.run(command, root, evidence, timeout=35 if name == "dependency_audit" else 25)
            outcome = "PASS" if result.outcome == "PASS" else "UNEXECUTED" if result.outcome == "UNEXECUTED" else "FAIL"
            detail = result.detail or f"Executed command; exit code {result.exit_code}"
        checks.append({"id": name, "outcome": outcome, "evidence": evidence, "detail": detail})
    names = [line.split("==", 1)[0] for line in (root / "requirements.lock").read_text().splitlines() if "==" in line and not line.startswith("#")]
    inventory = license_inventory(names)
    license_path = f"{output_dir}/licenses.json"
    (root / license_path).write_text(json.dumps({"packages": inventory, "legal_approval": "NOT_PERFORMED"}, indent=2) + "\n")
    checks.append({"id": "license_inventory", "outcome": "PASS" if all(i["installed"] for i in inventory) else "FAIL",
                   "evidence": license_path, "detail": "Installed package license metadata inventoried; not a legal approval"})
    return {"scope": "local-only" if local_only else "full", "source_digest": source_digest(root),
            "tools": installed_versions(["ruff", "mypy", "pip-audit"]), "checks": checks,
            "all_mandatory_pass": all(c["outcome"] == "PASS" for c in checks if c["id"] != "license_inventory")}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", default="evidence/validation/security")
    parser.add_argument("--local", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    if Path(args.output_dir).is_absolute() or ".." in Path(args.output_dir).parts:
        parser.error("Output directory must be repository-relative")
    report = collect(root, args.output_dir, local_only=args.local)
    (root / args.output_dir / "summary.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    # Collection succeeds even when a gate fails; the report and workbench enforce the gate.
    raise SystemExit(0 if report["all_mandatory_pass"] else 2)
