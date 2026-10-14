"""Create a consistent SQLite backup. Never overwrite an existing destination."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import time
from pathlib import Path
from urllib.parse import quote


def backup(database: Path, destination: Path, timeout: float = 10) -> dict:
    database = database.resolve()
    destination = destination.resolve()
    if not database.is_file() or database == destination:
        raise ValueError("Source must exist and differ from the destination")
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(descriptor)
    started = time.monotonic()
    try:
        source = sqlite3.connect("file:" + quote(str(database), safe="/:") + "?mode=ro", uri=True, timeout=1)
        target = sqlite3.connect(destination)
        try:
            def progress(status: int, remaining: int, total: int) -> None:
                if time.monotonic() - started >= timeout:
                    raise TimeoutError("Backup deadline exceeded")
            source.backup(target, pages=128, progress=progress, sleep=0.05)
            if target.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise RuntimeError("Backup integrity check failed")
        finally:
            target.close()
            source.close()
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    return {"completed": True, "destination": str(destination),
            "sha256": hashlib.sha256(destination.read_bytes()).hexdigest(),
            "duration_seconds": time.monotonic() - started, "integrity_check": "ok"}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, default=Path("data/shortener.db"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(backup(args.database, args.output), indent=2))
