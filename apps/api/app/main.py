"""InsightOS API entrypoint."""
from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.v1 import (alerts, analysis, auth, datasources, documents,
                        investigations, metrics, models, query, system)
from app.core.config import settings
from app.core.ratelimit import get_limiter, limit_for

logging.basicConfig(
    level=logging.DEBUG if settings.DEBUG else logging.INFO,
    format='{"ts":"%(asctime)s","level":"%(levelname)s","logger":"%(name)s","msg":"%(message)s"}',
)
log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("startup env=%s llm_enabled=%s", settings.ENV, settings.llm_enabled)
    try:
        from app.db.session import create_all
        await create_all()
        log.info("schema ensured (startup bootstrap)")
    except Exception as exc:
        log.warning("schema bootstrap note: %s", exc)
    log.info("rate limiter backend=%s", get_limiter().backend)
    if not settings.llm_enabled:
        log.warning(
            "No LLM provider configured. Investigations will run in deterministic "
            "mode: all figures are still computed, narratives are templated."
        )
    yield
    from app.datasources.registry import get_registry
    from app.db.session import dispose
    await get_registry().dispose_all()
    await dispose()
    log.info("shutdown")


app = FastAPI(
    title="InsightOS API",
    description="Autonomous business intelligence and decision intelligence platform.",
    version="0.1.0",
    lifespan=lifespan,
    docs_url="/docs" if settings.ENV != "production" else None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if "*" in settings.CORS_ORIGINS else settings.CORS_ORIGINS,
    allow_origin_regex=r"https://.*\.vercel\.app|https://.*\.onrender\.com|http://localhost:.*",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def add_timing_and_request_id(request: Request, call_next):
    import uuid
    request_id = request.headers.get("x-request-id", uuid.uuid4().hex[:16])
    started = time.perf_counter()
    response = await call_next(request)
    elapsed = (time.perf_counter() - started) * 1000
    response.headers["x-request-id"] = request_id
    response.headers["x-response-time-ms"] = f"{elapsed:.1f}"
    log.info("http %s %s %s %.1fms id=%s", request.method, request.url.path,
             response.status_code, elapsed, request_id)
    return response


@app.middleware("http")
async def rate_limit(request: Request, call_next):
    """Keyed on the authenticated user when there is one, on the client address
    otherwise. Using the token subject means one user cannot exhaust a shared
    office IP's budget for everyone behind it."""
    if request.url.path in {"/api/v1/health", "/docs", "/openapi.json"}:
        return await call_next(request)

    identity = request.headers.get("authorization", "")
    key = identity[-32:] if identity else (request.client.host if request.client else "anon")
    limit, window = limit_for(request.method, request.url.path)
    decision = await get_limiter().check(f"{request.method}:{request.url.path}:{key}",
                                         limit=limit, window_seconds=window)
    if not decision.allowed:
        return JSONResponse(
            status_code=429,
            headers={**decision.headers(), "retry-after": str(decision.reset_after)},
            content={"error": "rate_limited",
                     "message": f"Too many requests. Limit is {limit} per "
                                f"{window}s on this endpoint.",
                     "reason": "Rate limit exceeded.",
                     "suggested_fix": f"Retry in {decision.reset_after}s.",
                     "retryable": True})
    response = await call_next(request)
    for header, value in decision.headers().items():
        response.headers[header] = value
    return response


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    """Never leak a stack trace to a user (spec section 69)."""
    log.exception("unhandled path=%s", request.url.path)
    return JSONResponse(
        status_code=500,
        content={
            "error": "internal_error",
            "message": "Something went wrong on our side.",
            "reason": "The request could not be completed.",
            "suggested_fix": "Retry in a moment. If it persists, contact your workspace admin.",
            "retryable": True,
        },
    )


app.include_router(system.router, prefix="/api/v1", tags=["system"])
app.include_router(auth.router, prefix="/api/v1/auth", tags=["auth"])
app.include_router(metrics.router, prefix="/api/v1/metrics", tags=["semantic-layer"])
app.include_router(investigations.router, prefix="/api/v1/investigations",
                   tags=["investigations"])
app.include_router(alerts.router, prefix="/api/v1/alerts", tags=["alerts"])
app.include_router(analysis.router, prefix="/api/v1/analysis", tags=["analysis"])
app.include_router(datasources.router, prefix="/api/v1/datasources",
                   tags=["data sources"])
app.include_router(query.router, prefix="/api/v1/query", tags=["query"])
app.include_router(models.router, prefix="/api/v1/models", tags=["models"])
app.include_router(documents.router, prefix="/api/v1/documents", tags=["documents"])
