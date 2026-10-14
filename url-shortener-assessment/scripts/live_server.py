"""Isolated loopback server helper for trusted local smoke/browser checks."""
from __future__ import annotations

import os
import secrets
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import IO

import httpx

ROOT = Path(__file__).resolve().parents[1]


class LiveServer:
    def __init__(self, log: Path) -> None:
        self.log = log
        self.key = secrets.token_urlsafe(40)
        self.reviewer = secrets.token_urlsafe(40)
        self.audit_key = secrets.token_urlsafe(40)
        self.temp: tempfile.TemporaryDirectory[str] | None = None
        self.process: subprocess.Popen | None = None
        self.output: IO[bytes] | None = None
        self.url = ""

    def __enter__(self) -> LiveServer:
        self.temp = tempfile.TemporaryDirectory(prefix="shortener-smoke-")
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        self.url = f"http://127.0.0.1:{port}"
        env = {key: os.environ[key] for key in ("PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP") if key in os.environ}
        env.update({"PYTHONPATH": str(ROOT / "src"), "PYTHONDONTWRITEBYTECODE": "1", "API_KEY": self.key,
                    "REVIEWER_KEY": self.reviewer, "AUDIT_KEY": self.audit_key,
                    "DATABASE_PATH": str(Path(self.temp.name) / "smoke.db"), "BASE_URL": self.url,
                    "ALLOWED_HOSTS": "127.0.0.1,localhost", "RATE_LIMIT": "10000", "REDIRECT_LIMIT": "10000"})
        self.log.parent.mkdir(parents=True, exist_ok=True)
        self.output = self.log.open("wb")
        command = [sys.executable, "-m", "uvicorn", "shortener.api:create_app", "--factory",
                   "--host", "127.0.0.1", "--port", str(port), "--no-access-log", "--no-server-header", "--no-proxy-headers"]
        try:
            self.process = subprocess.Popen(command, cwd=ROOT, env=env, stdin=subprocess.DEVNULL,
                                            stdout=self.output, stderr=subprocess.STDOUT)
            for _ in range(100):
                if self.process.poll() is not None:
                    raise RuntimeError("Isolated server exited before readiness; inspect its log")
                try:
                    response = httpx.get(self.url + "/health/ready", timeout=0.3, trust_env=False)
                    if response.status_code == 200:
                        return self
                except httpx.HTTPError:
                    time.sleep(0.05)
            raise TimeoutError("Isolated server did not become ready")
        except Exception:
            self.__exit__(None, None, None)
            raise

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        if self.process is not None and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
        if self.output is not None:
            self.output.close()
        if self.temp is not None:
            self.temp.cleanup()
