"""The HTTP app: health, intake routes and the built web pages."""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import sessionmaker
from starlette.concurrency import run_in_threadpool

from firstcall.core.clock import Clock
from firstcall.db import Source, make_engine, make_sessions
from firstcall.intake import auth
from firstcall.intake.limits import BodyTooLargeError, RateLimiter, read_limited
from firstcall.intake.store import accept
from firstcall.runtime import SystemClock
from firstcall.settings import Settings

WEB_DIST = Path(__file__).resolve().parents[1] / "web" / "dist"
CONTENT_TYPES = {"email": "message/rfc822", "form": "application/json", "call": "application/json"}

log = logging.getLogger("firstcall.intake")


def _error(status: int, detail: str) -> JSONResponse:
    return JSONResponse({"detail": detail}, status_code=status)


def create_app(
    settings: Settings | None = None,
    sessions: sessionmaker[Any] | None = None,
    clock: Clock | None = None,
    monotonic: Any = time.monotonic,
) -> FastAPI:
    settings = settings or Settings()
    sessions = sessions or make_sessions(make_engine(settings.database_url))
    clock = clock or SystemClock()
    app = FastAPI(title="Firstcall", docs_url=None, redoc_url=None, openapi_url=None)
    per_source = RateLimiter(settings.source_rate_per_minute, monotonic)
    per_ip = RateLimiter(settings.ip_rate_per_minute, monotonic)

    def client_ip(request: Request) -> str:
        forwarded = request.headers.get("x-forwarded-for")
        if settings.trust_proxy and forwarded:
            return forwarded.split(",")[0].strip()
        return request.client.host if request.client else "unknown"

    def load_source(slug: str) -> Source | None:
        with sessions() as session:
            return session.scalar(select(Source).where(Source.slug == slug))

    async def intake(request: Request, slug: str, token: str | None) -> JSONResponse:
        if not per_ip.allow(client_ip(request)):
            return _error(429, "too many requests from this address")
        source = await run_in_threadpool(load_source, slug)
        if source is None:
            return _error(404, "unknown source")
        if not per_source.allow(source.slug):
            return _error(429, "too many requests for this source")
        content_type = request.headers.get("content-type", "").split(";")[0].strip().lower()
        if content_type != CONTENT_TYPES[source.kind]:
            return _error(415, f"this source takes {CONTENT_TYPES[source.kind]}")
        limit = settings.email_max_bytes if source.kind == "email" else settings.form_max_bytes
        declared = request.headers.get("content-length")
        if declared and declared.isdigit() and int(declared) > limit:
            return _error(413, f"the body is over {limit} bytes")
        if source.auth_method == "token" and not auth.check_token(source, token):
            return _error(401, "bad token")
        if source.auth_method == "basic":
            ok = token is None and await run_in_threadpool(
                auth.check_basic, source, request.headers.get("authorization")
            )
            if not ok:
                return _error(401, "bad credentials")
        if source.auth_method == "hmac" and token is not None:
            return _error(401, "bad signature")
        try:
            body = await read_limited(request.stream(), limit)
        except BodyTooLargeError:
            return _error(413, f"the body is over {limit} bytes")
        if source.auth_method == "hmac" and not auth.check_hmac(
            source,
            request.headers.get(auth.TIMESTAMP_HEADER),
            request.headers.get(auth.SIGNATURE_HEADER),
            body,
            clock.now(),
        ):
            return _error(401, "bad signature")
        try:
            accepted = await run_in_threadpool(
                accept, sessions, source, body, content_type, request.headers
            )
        except SQLAlchemyError:
            log.exception("intake commit failed for source %s", source.slug)
            return _error(503, "not stored, try again")
        status = 202 if accepted.status == "received" else 200
        return JSONResponse(
            {"status": accepted.status, "message_id": str(accepted.message_id)},
            status_code=status,
        )

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/intake/{slug}")
    async def intake_plain(request: Request, slug: str) -> JSONResponse:
        return await intake(request, slug, None)

    @app.post("/intake/{slug}/{token}")
    async def intake_with_token(request: Request, slug: str, token: str) -> JSONResponse:
        return await intake(request, slug, token)

    if WEB_DIST.is_dir():
        app.mount("/", StaticFiles(directory=WEB_DIST, html=True), name="web")
    return app
