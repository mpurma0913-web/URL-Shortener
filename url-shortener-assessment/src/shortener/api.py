"""HTTP delivery adapter; domain errors are safe, stable API responses."""
from __future__ import annotations

import hashlib
import hmac
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import Depends, FastAPI, Header, Path as ApiPath, Query, Request, Response, Security
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, RedirectResponse
from fastapi.security import APIKeyHeader
from pydantic import BaseModel, ConfigDict, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.staticfiles import StaticFiles

from shortener.config import Settings
from shortener.domain import DomainError, Link, RandomCodeGenerator, SystemClock
from shortener.security import Metrics, SecurityMiddleware
from shortener.services import LinkService
from shortener.storage import Database, SqliteLinkRepository
from shortener.workflow_api import install_workflow_routes

Code = Annotated[str, ApiPath(pattern=r"^[A-Za-z0-9]{8}$")]
key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


class CreateLinkRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    url: str = Field(min_length=1, max_length=2048)
    expires_at: datetime | None = None


class LinkResponse(BaseModel):
    code: str = Field(pattern=r"^[A-Za-z0-9]{8}$")
    short_url: str
    url: str
    created_at: datetime
    expires_at: datetime | None
    revoked: bool


class ErrorIssue(BaseModel):
    field: str
    type: str


class ErrorDetail(BaseModel):
    code: str
    message: str
    issues: list[ErrorIssue] | None = None


class ErrorResponse(BaseModel):
    error: ErrorDetail
    request_id: str


class DailyCount(BaseModel):
    day: date
    redirects: int = Field(ge=0)


class StatsResponse(BaseModel):
    code: str
    measurement: Literal["committed_get_redirects"]
    timezone: Literal["UTC"]
    total_redirects: int = Field(ge=0)
    last_redirect_at: datetime | None
    daily: list[DailyCount]


def link_response(link: Link, base_url: str) -> LinkResponse:
    return LinkResponse(code=link.code, short_url=f"{base_url.rstrip('/')}/r/{link.code}", url=link.url,
                        created_at=datetime.fromtimestamp(link.created_at, UTC),
                        expires_at=datetime.fromtimestamp(link.expires_at, UTC) if link.expires_at is not None else None,
                        revoked=link.revoked)


def create_app(settings: Settings | None = None) -> FastAPI:
    config = settings or Settings.from_env()
    database = Database(config)
    repository = SqliteLinkRepository(database, SystemClock(), RandomCodeGenerator())
    service = LinkService(repository)
    metrics = Metrics()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        database.initialize()
        yield

    app = FastAPI(title="Reviewable URL Shortener", version="1.0.0", lifespan=lifespan,
                  docs_url=None, redoc_url=None, responses={status: {"model": ErrorResponse} for status in (401, 403, 404, 409, 410, 422, 429, 503)}, description="Operator-authenticated creation and analytics; public redirects. Offline console at /.")
    app.state.database = database
    app.state.repository = repository
    app.state.service = service
    app.state.settings = config
    app.state.metrics = metrics
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=list(config.allowed_hosts))
    app.add_middleware(SecurityMiddleware, metrics=metrics, body_limit=config.body_limit)

    @app.exception_handler(DomainError)
    async def domain_error(request: Request, exc: DomainError) -> JSONResponse:
        headers = {"Retry-After": "60"} if exc.status == 429 else {"Retry-After": "1"} if exc.status == 503 else {}
        return JSONResponse({"error": {"code": exc.code, "message": exc.message},
                             "request_id": request.state.request_id}, status_code=exc.status, headers=headers)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        issues = [{"field": ".".join(str(p) for p in e["loc"]), "type": e["type"]} for e in exc.errors()]
        return JSONResponse({"error": {"code": "validation_error", "message": "Invalid request fields", "issues": issues},
                             "request_id": request.state.request_id}, status_code=422)

    @app.exception_handler(Exception)
    async def unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        return JSONResponse({"error": {"code": "internal_error", "message": "Unexpected server error"},
                             "request_id": request.state.request_id}, status_code=500,
                            headers={"Cache-Control": "no-store", "X-Request-ID": request.state.request_id,
                                     "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer"})

    def rate_limit(request: Request, category: str, limit: int) -> None:
        host = request.client.host if request.client else "unknown"
        digest = hmac.new(config.api_key.encode(), f"{category}:{host}".encode(), hashlib.sha256).hexdigest()
        repository.consume_rate_limit(digest, limit)

    def principal(request: Request, key: str | None = Security(key_header)) -> str:
        rate_limit(request, "management", config.rate_limit)
        if key is not None and hmac.compare_digest(key.encode(), config.api_key.encode()):
            return "operator"
        if key is not None and hmac.compare_digest(key.encode(), config.reviewer_key.encode()):
            return "reviewer"
        raise DomainError(401, "unauthorized", "A valid X-API-Key is required")

    def operator(role: str = Depends(principal)) -> str:
        if role != "operator":
            raise DomainError(403, "forbidden", "This action requires the operator credential")
        return role

    def reviewer(role: str = Depends(principal)) -> str:
        if role != "reviewer":
            raise DomainError(403, "forbidden", "This action requires the reviewer credential")
        return role

    app.state.principal_dependency = principal
    app.state.operator_dependency = operator
    app.state.reviewer_dependency = reviewer

    @app.get("/health/live", tags=["operations"])
    def live() -> dict[str, str]:
        return {"status": "alive"}

    @app.get("/health/ready", tags=["operations"])
    def ready() -> dict[str, str]:
        database.healthy()
        return {"status": "ready"}

    @app.get("/metrics", response_class=PlainTextResponse, dependencies=[Depends(principal)], tags=["operations"])
    def get_metrics() -> str:
        return metrics.render()

    @app.post("/api/v1/links", response_model=LinkResponse, status_code=201,
              responses={200: {"model": LinkResponse, "description": "Matching idempotent replay"}},
              dependencies=[Depends(operator)], tags=["links"])
    def create_link(body: CreateLinkRequest, response: Response,
                    idempotency_key: Annotated[str | None, Header(min_length=8, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")] = None) -> LinkResponse:
        result = service.create(body.url, body.expires_at, idempotency_key)
        response.status_code = 200 if result.replayed else 201
        response.headers["Idempotency-Replayed"] = str(result.replayed).lower()
        response.headers["Location"] = f"/api/v1/links/{result.link.code}"
        return link_response(result.link, config.base_url)

    @app.get("/api/v1/links/{code}", response_model=LinkResponse,
             dependencies=[Depends(principal)], tags=["links"])
    def get_link(code: Code) -> LinkResponse:
        return link_response(service.get(code), config.base_url)

    @app.delete("/api/v1/links/{code}", status_code=204,
                dependencies=[Depends(operator)], tags=["links"])
    def revoke_link(code: Code) -> Response:
        service.revoke(code)
        return Response(status_code=204)

    @app.get("/api/v1/links/{code}/stats", response_model=StatsResponse, dependencies=[Depends(principal)], tags=["analytics"])
    def statistics(code: Code, days: int = Query(default=30, ge=1, le=90)) -> dict[str, Any]:
        stats = service.stats(code, days)
        if stats["last_redirect_at"] is not None:
            stats["last_redirect_at"] = datetime.fromtimestamp(stats["last_redirect_at"], UTC).isoformat()
        return stats

    @app.get("/r/{code}", response_class=RedirectResponse, status_code=302, tags=["redirects"])
    @app.head("/r/{code}", response_class=RedirectResponse, status_code=302, tags=["redirects"])
    def redirect(code: Code, request: Request) -> RedirectResponse:
        rate_limit(request, "redirect", config.redirect_limit)
        link = service.resolve(code, count=request.method == "GET")
        return RedirectResponse(link.url, status_code=302)

    static = Path(__file__).parent / "static"
    app.mount("/static", StaticFiles(directory=static), name="static")

    @app.get("/", include_in_schema=False)
    def console() -> FileResponse:
        return FileResponse(static / "index.html")

    install_workflow_routes(app)
    return app
