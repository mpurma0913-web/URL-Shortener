"""Atomic workflow state and tamper-evident audit persistence.

HMAC detects edits without the key; it is not external immutable/WORM storage. A host
administrator with both the database and the key can rewrite evidence. See the threat model.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import sqlite3
from collections.abc import Callable
from typing import Any

from shortener.domain import DomainError
from shortener.storage import Database
from shortener.workflows import Actor, Event, RequirementInput, Workflow, WorkflowEngine


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)


class WorkflowStore:
    def __init__(self, database: Database, engine: WorkflowEngine, audit_secret: bytes) -> None:
        self.database = database
        self.engine = engine
        self.audit_secret = audit_secret

    def _mac(self, text: str) -> str:
        return hmac.new(self.audit_secret, text.encode(), hashlib.sha256).hexdigest()

    def _load(self, c: sqlite3.Connection, run_id: str) -> Workflow:
        row = c.execute("SELECT * FROM workflows WHERE id=?", (run_id,)).fetchone()
        if row is None:
            raise DomainError(404, "workflow_not_found", "Workflow was not found")
        if not hmac.compare_digest(row["state_mac"], self._mac(row["state_json"])):
            raise DomainError(503, "state_integrity_failed", "Workflow state integrity verification failed")
        run = Workflow.model_validate_json(row["state_json"])
        if run.id != run_id or run.version != row["version"]:
            raise DomainError(503, "state_integrity_failed", "Workflow identity or version does not match")
        return run

    def _chain_valid(self, c: sqlite3.Connection, run: Workflow) -> bool:
        previous = "0" * 64
        expected_sequence = 1
        for row in c.execute("SELECT * FROM workflow_audit WHERE run_id=? ORDER BY sequence", (run.id,)):
            expected_hash = self._mac(run.id + ":" + str(row["sequence"]) + ":" + previous + ":" + row["event_json"])
            if (row["sequence"] != expected_sequence or row["previous_hash"] != previous
                    or not hmac.compare_digest(row["event_hash"], expected_hash)):
                return False
            expected_sequence += 1
            previous = row["event_hash"]
        return previous == run.audit_head and expected_sequence - 1 == run.audit_sequence

    def _save(self, c: sqlite3.Connection, run: Workflow, events: list[Event], *, initial: bool = False) -> None:
        old_version = run.version
        if not initial:
            run.version += 1
        for event in events:
            run.audit_sequence += 1
            text = canonical({**event.model_dump(), "run_id": run.id, "sequence": run.audit_sequence, "version": run.version})
            event_hash = self._mac(run.id + ":" + str(run.audit_sequence) + ":" + run.audit_head + ":" + text)
            c.execute("INSERT INTO workflow_audit(run_id,sequence,event_json,previous_hash,event_hash) VALUES(?,?,?,?,?)",
                      (run.id, run.audit_sequence, text, run.audit_head, event_hash))
            run.audit_head = event_hash
        run.updated_at = self.engine.clock.now()
        text = canonical(run.model_dump())
        cursor = c.execute("UPDATE workflows SET version=?,state_json=?,state_mac=? WHERE id=? AND version=?",
                           (run.version, text, self._mac(text), run.id, old_version))
        if cursor.rowcount != 1:
            raise DomainError(409, "workflow_version_conflict", "Workflow changed; inspect it again before retrying")

    def create(self, requirement: RequirementInput, actor: Actor) -> Workflow:
        run, events = self.engine.create(requirement, actor)
        def create_run(c: sqlite3.Connection) -> Workflow:
            candidate = run.model_copy(deep=True)
            text = canonical(candidate.model_dump())
            c.execute("INSERT INTO workflows(id,version,created_at,state_json,state_mac) VALUES(?,?,?,?,?)",
                      (candidate.id, candidate.version, candidate.created_at, text, self._mac(text)))
            self._save(c, candidate, events, initial=True)
            return candidate
        return self.database.execute(create_run, write=True)

    def get(self, run_id: str) -> Workflow:
        def read(c: sqlite3.Connection) -> Workflow:
            run = self._load(c, run_id)
            self.engine.refresh(run)
            return run
        return self.database.execute(read)

    def list(self, limit: int = 20) -> list[dict[str, Any]]:
        def read(c: sqlite3.Connection) -> list[dict[str, Any]]:
            c.execute("BEGIN")
            ids = [row["id"] for row in c.execute("SELECT id FROM workflows ORDER BY created_at DESC,id DESC LIMIT ?", (limit,))]
            result = []
            for run_id in ids:
                run = self._load(c, run_id)
                self.engine.refresh(run)
                result.append({"id": run.id, "status": run.status, "scenario": run.scenario,
                               "version": run.version, "revision": run.revision, "created_at": run.created_at})
            return result
        return self.database.execute(read)

    def apply(self, run_id: str, expected_version: int | None,
              operation: Callable[[Workflow], list[Event]]) -> Workflow:
        def mutate(c: sqlite3.Connection) -> Workflow:
            run = self._load(c, run_id)
            if not self._chain_valid(c, run):
                raise DomainError(503, "audit_integrity_failed", "Audit chain verification failed; mutation is blocked")
            if expected_version is not None and expected_version != run.version:
                raise DomainError(409, "workflow_version_conflict", "Workflow changed; inspect it again before retrying")
            events = operation(run)
            self._save(c, run, events)
            return run
        return self.database.execute(mutate, write=True)

    def audit(self, run_id: str, after: int = 0, limit: int = 200) -> dict[str, Any]:
        def read(c: sqlite3.Connection) -> dict[str, Any]:
            c.execute("BEGIN")
            run = self._load(c, run_id)
            rows = c.execute("SELECT * FROM workflow_audit WHERE run_id=? AND sequence>? ORDER BY sequence LIMIT ?",
                             (run_id, after, limit)).fetchall()
            events = [{**json.loads(row["event_json"]), "event_hash": row["event_hash"], "previous_hash": row["previous_hash"]} for row in rows]
            next_after = rows[-1]["sequence"] if rows and rows[-1]["sequence"] < run.audit_sequence else None
            return {"run_id": run_id, "verified": self._chain_valid(c, run), "total_events": run.audit_sequence,
                    "events": events, "next_after": next_after,
                    "integrity_model": "HMAC chain plus signed state head; not external immutable storage"}
        return self.database.execute(read)
