"""Reviewable local evidence primitives. This is not a sandbox for untrusted code."""
from __future__ import annotations

import ast
import hashlib
import importlib.metadata
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

FINGERPRINT_ROOTS = ("src", "tests", "scripts", "specs", ".specify/memory", "docs/adr")
FINGERPRINT_FILES = ("pyproject.toml", "requirements.lock", "requirements-dev.txt", "SECURITY.md")
IGNORED_PARTS = {"__pycache__", ".pytest_cache", ".git", ".venv", ".mypy_cache", ".ruff_cache"}


def source_inventory(root: Path) -> dict[str, str]:
    files: set[Path] = set()
    for name in FINGERPRINT_ROOTS:
        directory = root / name
        if directory.is_dir():
            files.update(p for p in directory.rglob("*") if p.is_file() and not p.is_symlink()
                         and not IGNORED_PARTS.intersection(p.parts) and p.suffix not in {".pyc", ".pyo"})
    files.update(root / name for name in FINGERPRINT_FILES if (root / name).is_file())
    return {str(p.relative_to(root)).replace(os.sep, "/"): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(files)}


def source_digest(root: Path) -> str:
    data = json.dumps(source_inventory(root), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(data.encode()).hexdigest()


def hygiene(root: Path) -> dict[str, Any]:
    """Narrow, transparent static checks; not a replacement for SAST or dependency audit."""
    findings: list[dict[str, Any]] = []
    scanned = 0
    patterns = [re.compile(r"AKIA[0-9A-Z]{16}"), re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
                re.compile(r"ghp_[A-Za-z0-9]{36}"), re.compile(r"sk-proj-[A-Za-z0-9_-]{30,}")]
    for prefix in ("src", "scripts", "tests"):
        directory = root / prefix
        for path in sorted(directory.rglob("*.py")):
            if IGNORED_PARTS.intersection(path.parts):
                continue
            scanned += 1
            relative = str(path.relative_to(root))
            text = path.read_text(encoding="utf-8")
            for line_number, line in enumerate(text.splitlines(), 1):
                if "\t" in line or line.rstrip() != line:
                    findings.append({"path": relative, "line": line_number, "rule": "whitespace"})
                if any(pattern.search(line) for pattern in patterns):
                    findings.append({"path": relative, "line": line_number, "rule": "possible_secret"})
            try:
                tree = ast.parse(text, filename=relative)
            except SyntaxError as exc:
                findings.append({"path": relative, "line": exc.lineno, "rule": "syntax"})
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    name = node.func.id if isinstance(node.func, ast.Name) else node.func.attr if isinstance(node.func, ast.Attribute) else ""
                    if name in {"eval", "exec"}:
                        findings.append({"path": relative, "line": node.lineno, "rule": "dynamic_execution"})
                    if any(k.arg == "shell" and isinstance(k.value, ast.Constant) and k.value.value is True for k in node.keywords):
                        findings.append({"path": relative, "line": node.lineno, "rule": "shell_true"})
                if isinstance(node, ast.ExceptHandler) and len(node.body) == 1 and isinstance(node.body[0], ast.Pass):
                    findings.append({"path": relative, "line": node.lineno, "rule": "silenced_exception"})
    return {"check": "bounded_static_hygiene", "outcome": "PASS" if not findings else "FAIL",
            "files_scanned": scanned, "findings": findings,
            "limitations": ["Pattern-based secrets only", "No whole-program taint analysis", "No vulnerability database queried"]}


@dataclass(frozen=True)
class CommandResult:
    command: list[str]
    outcome: str
    exit_code: int | None
    duration_seconds: float
    evidence: str
    detail: str = ""

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class CommandRunner:
    """Timeout/output-bounded subprocess adapter; callers supply fixed reviewed commands."""

    def __init__(self, sensitive_values: tuple[str, ...] = (), output_limit: int = 1024 * 1024) -> None:
        self.sensitive_values = tuple(s for s in sensitive_values if s)
        self.output_limit = output_limit

    def run(self, command: list[str], root: Path, evidence: str, timeout: float = 90) -> CommandResult:
        if not evidence or Path(evidence).is_absolute() or ".." in Path(evidence).parts:
            raise ValueError("Evidence path must be relative to the trusted repository")
        output = (root / evidence).resolve()
        if not output.is_relative_to(root.resolve()):
            raise ValueError("Evidence must remain inside the trusted repository")
        output.parent.mkdir(parents=True, exist_ok=True)
        # No API keys, cloud credentials or provider tokens are copied to child environments.
        env = {name: os.environ[name] for name in ("PATH", "SYSTEMROOT", "WINDIR", "TMPDIR", "TEMP", "TMP") if name in os.environ}
        env.update({"PYTHONPATH": str(root / "src"), "PYTHONDONTWRITEBYTECODE": "1", "PYTHONUNBUFFERED": "1",
                    "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"})
        started = time.monotonic()
        outcome, detail, exit_code = "PASS", "", None
        with tempfile.TemporaryFile() as capture:
            try:
                process = subprocess.Popen(command, cwd=root, env=env, stdin=subprocess.DEVNULL,
                                           stdout=capture, stderr=subprocess.STDOUT, start_new_session=os.name == "posix")
            except OSError as exc:
                outcome, detail = "UNEXECUTED", type(exc).__name__
            else:
                while process.poll() is None:
                    if time.monotonic() - started >= timeout:
                        outcome, detail = "TIMEOUT", "Command exceeded its wall-clock budget"
                    elif capture.tell() > self.output_limit:
                        outcome, detail = "OUTPUT_LIMIT", "Command exceeded its output budget"
                    if outcome != "PASS":
                        self._terminate(process)
                        break
                    time.sleep(0.02)
                exit_code = process.wait()
                if outcome == "PASS" and capture.tell() > self.output_limit:
                    outcome, detail = "OUTPUT_LIMIT", "Command exceeded its output budget"
                if outcome == "PASS" and exit_code != 0:
                    outcome = "FAIL"
            capture.seek(0)
            text = capture.read(self.output_limit).decode("utf-8", errors="replace")
        for secret in self.sensitive_values:
            text = text.replace(secret, "[REDACTED]")
        text += f"\nOutcome: {outcome}; exit code: {exit_code}; {detail}\n"
        output.write_text(text, encoding="utf-8")
        return CommandResult(command, outcome, exit_code, round(time.monotonic() - started, 6), evidence, detail)

    @staticmethod
    def _terminate(process: subprocess.Popen[bytes]) -> None:
        if process.poll() is not None:
            return
        try:
            if os.name == "posix":
                os.killpg(process.pid, signal.SIGTERM)
            else:
                process.terminate()
            process.wait(timeout=0.5)
        except ProcessLookupError:
            return
        except subprocess.TimeoutExpired:
            if os.name == "posix":
                os.killpg(process.pid, signal.SIGKILL)
            else:
                process.kill()
            process.wait()


def installed_versions(names: list[str]) -> dict[str, str | None]:
    result: dict[str, str | None] = {}
    for name in names:
        try:
            result[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            result[name] = None
    return result


def license_inventory(names: list[str]) -> list[dict[str, Any]]:
    inventory: list[dict[str, Any]] = []
    for name in names:
        try:
            metadata = importlib.metadata.metadata(name)
        except importlib.metadata.PackageNotFoundError:
            inventory.append({"name": name, "installed": False})
            continue
        inventory.append({"name": name, "version": metadata["Version"], "installed": True,
                          "license": metadata.get("License-Expression") or metadata.get("License") or "REVIEW_REQUIRED"})
    return inventory
