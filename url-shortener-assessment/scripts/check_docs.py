"""Check documentation paths, local links and named test references; not a prose-quality proof."""
from __future__ import annotations

import ast
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REQUIRED = (
    "README.md", "SECURITY.md", "CONTRIBUTING.md", "CLAUDE.md",
    ".specify/memory/constitution.md", "specs/001-url-shortener/spec.md",
    "specs/001-url-shortener/plan.md", "specs/001-url-shortener/tasks.md",
    "docs/architecture/overview.md", "docs/architecture/api.md", "docs/architecture/data-model.md",
    "docs/operations/setup.md", "docs/operations/workflows.md", "docs/operations/backup-and-release.md",
    "docs/scenarios/index.md", "docs/scenarios/greenfield.md", "docs/scenarios/brownfield.md",
    "docs/scenarios/ambiguous.md", "docs/governance/source-reconciliation.md",
    "docs/governance/approvals.md", "docs/governance/ai-execution-record.md",
    "docs/assessment/testing-and-limitations.md", "docs/assessment/final-engineering-summary.md",
    "docs/assessment/reviewer-guide.md", "docs/assessment/self-review.md",
    "docs/traceability/matrix.json", "specs/001-url-shortener/checklists/readiness.md",
)


def check(root: Path = ROOT) -> dict:
    findings: list[str] = []
    for name in REQUIRED:
        path = root / name
        if not path.is_file() or path.stat().st_size < 150:
            findings.append(f"Missing or insufficient artifact: {name}")
    for path in [root / "README.md", *(root / "docs").rglob("*.md")]:
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        for target in re.findall(r"\]\(([^)\s]+)\)", text):
            if target.startswith(("https://", "http://", "mailto:", "#")):
                continue
            relative = target.split("#", 1)[0]
            resolved = (path.parent / relative).resolve()
            if not resolved.is_relative_to(root.resolve()) or not resolved.exists():
                findings.append(f"Broken local link in {path.relative_to(root)}: {target}")
    matrix_path = root / "docs/traceability/matrix.json"
    rows = json.loads(matrix_path.read_text()) if matrix_path.exists() else []
    expected = {f"AS-{n:02}" for n in range(1, 7)} | {f"FR-{n:02}" for n in range(1, 11)}
    expected |= {f"WF-{n:02}" for n in range(1, 10)} | {f"NFR-{n:02}" for n in range(1, 6)}
    if {row["requirement"] for row in rows} != expected:
        findings.append("Traceability must cover exactly the defined AS/FR/WF/NFR identifiers")
    for row in rows:
        for name in row["code"]:
            if not (root / name).exists():
                findings.append(f"{row['requirement']}: missing path {name}")
        for reference in row["tests"]:
            name, function = reference.split("::", 1)
            path = root / name
            if not path.exists():
                findings.append(f"Missing test file {name}")
                continue
            tree = ast.parse(path.read_text(), filename=name)
            names = {n.name for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
            if function not in names:
                findings.append(f"Unknown test reference {reference}")
        for number in row["adrs"]:
            if not (root / f"docs/adr/{number}.md").is_file():
                findings.append(f"Unknown ADR {number}")
    return {"outcome": "PASS" if not findings else "FAIL", "required_artifacts": len(REQUIRED),
            "requirement_rows": len(rows), "findings": findings,
            "limitation": "Checks presence, local links and named tests; does not replace human prose or requirements review"}


if __name__ == "__main__":
    result = check()
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result["outcome"] == "PASS" else 1)
