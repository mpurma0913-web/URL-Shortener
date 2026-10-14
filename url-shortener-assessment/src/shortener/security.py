"""Bounded request bodies, request IDs, security headers and low-cardinality telemetry."""
from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
import uuid
from collections import Counter

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

logger = logging.getLogger("shortener.requests")


class Metrics:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.requests: Counter[tuple[str, str, int]] = Counter()
        self.duration = 0.0

    def record(self, method: str, route: str, status: int, seconds: float) -> None:
        with self.lock:
            self.requests[(method, route, status)] += 1
            self.duration += seconds

    def render(self) -> str:
        with self.lock:
            lines = ["# TYPE shortener_http_requests_total counter"]
            for (method, route, status), count in sorted(self.requests.items()):
                # Labels are framework route templates, never user URLs or arbitrary path segments.
                lines.append(f'shortener_http_requests_total{{method="{method}",route="{route}",status="{status}"}} {count}')
            lines += ["# TYPE shortener_http_duration_seconds_sum counter",
                      f"shortener_http_duration_seconds_sum {self.duration:.6f}"]
            return "\n".join(lines) + "\n"


class SecurityMiddleware:
    def __init__(self, app: ASGIApp, metrics: Metrics, body_limit: int = 16384) -> None:
        self.app = app
        self.metrics = metrics
        self.body_limit = body_limit

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        started = time.perf_counter()
        headers = dict(scope.get("headers", []))
        try:
            request_id = str(uuid.UUID(headers.get(b"x-request-id", b"").decode("ascii")))
        except (ValueError, UnicodeDecodeError):
            request_id = str(uuid.uuid4())
        scope.setdefault("state", {})["request_id"] = request_id
        status = 500

        async def wrapped_send(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                pairs = list(message.get("headers", []))
                replaced = {b"cache-control", b"x-request-id", b"x-content-type-options", b"referrer-policy", b"content-security-policy"}
                pairs = [(k, v) for k, v in pairs if k.lower() not in replaced]
                pairs.extend([
                    (b"cache-control", b"no-store"), (b"x-request-id", request_id.encode()),
                    (b"x-content-type-options", b"nosniff"), (b"referrer-policy", b"no-referrer"),
                    (b"content-security-policy", b"default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"),
                ])
                message["headers"] = pairs
            await send(message)

        async def reject(code: int, name: str, message: str) -> None:
            response = JSONResponse({"error": {"code": name, "message": message}, "request_id": request_id}, status_code=code)
            await response(scope, receive, wrapped_send)

        try:
            try:
                length = int(headers.get(b"content-length", b"0"))
            except ValueError:
                await reject(400, "invalid_content_length", "Invalid Content-Length")
                return
            if length < 0 or length > self.body_limit:
                await reject(413, "body_too_large", "Request body exceeds configured limit")
                return
            chunks: list[bytes] = []
            size = 0
            deadline = asyncio.get_running_loop().time() + 5.0
            while True:
                remaining = deadline - asyncio.get_running_loop().time()
                try:
                    message = await asyncio.wait_for(receive(), timeout=max(0.001, remaining))
                except TimeoutError:
                    await reject(408, "request_timeout", "Request body deadline exceeded")
                    return
                if message["type"] == "http.disconnect":
                    return
                chunk = message.get("body", b"")
                size += len(chunk)
                if size > self.body_limit:
                    await reject(413, "body_too_large", "Request body exceeds configured limit")
                    return
                chunks.append(chunk)
                if not message.get("more_body", False):
                    break
            body = b"".join(chunks)
            sent = False

            async def replay_receive() -> Message:
                nonlocal sent
                if not sent:
                    sent = True
                    return {"type": "http.request", "body": body, "more_body": False}
                return await receive()

            await self.app(scope, replay_receive, wrapped_send)
        finally:
            duration = time.perf_counter() - started
            route = getattr(scope.get("route"), "path", "unmatched")
            method = scope.get("method", "UNKNOWN")
            if method not in {"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"}:
                method = "OTHER"
            self.metrics.record(method, route, status, duration)
            logger.info(json.dumps({"event": "http_request", "request_id": request_id,
                                    "method": method, "route": route, "status": status,
                                    "duration_ms": round(duration * 1000, 3)}, separators=(",", ":")))
