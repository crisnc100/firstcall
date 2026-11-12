"""The HTTP app: health, intake routes, the board's JSON API and the built web pages."""

from __future__ import annotations

import ipaddress
import logging
import time
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import sessionmaker
from starlette.concurrency import run_in_threadpool

from firstcall.board.routes import board_router
from firstcall.core.clock import Clock
from firstcall.db import Source, make_engine, make_sessions
from firstcall.intake import auth
from firstcall.intake.limits import BodyTooLargeError, RateLimiter, ip_key, read_limited
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
        peer = request.client.host if request.client else "unknown"
        forwarded = request.headers.get("x-forwarded-for")
        if not (settings.trust_proxy and forwarded):
            return ip_key(peer)
        # Each trusted proxy appends the address it saw, so the client is that many entries
        # from the right. Anything further left was written by the client itself.
        entries = [entry.strip() for entry in forwarded.split(",")]
        if len(entries) < settings.trusted_hops:
            return ip_key(peer)
        candidate = entries[-settings.trusted_hops]
        try:
            ipaddress.ip_address(candidate)
        except ValueError:
            return ip_key(peer)
        return ip_key(candidate)

    def load_source(slug: str) -> Source | None:
        with sessions() as session:
            return session.scalar(select(Source).where(Source.slug == slug))

    def cors(source: Source | None, request: Request) -> dict[str, str]:
        origin = request.headers.get("origin")
        if source is None or not origin or origin not in source.allowed_origins:
            return {}
        return {"Access-Control-Allow-Origin": origin, "Vary": "Origin"}

    # Allowed origins per source, refreshed at most once a minute with one query. A per-IP
    # 429 reads this instead of the database, so a flood never costs a query per request,
    # yet the browser can still show the form's "try again".
    origins_cache: dict[str, Any] = {"at": None, "by_slug": {}}

    def origins_for(slug: str) -> list[str]:
        now = monotonic()
        if origins_cache["at"] is None or now - origins_cache["at"] >= 60:
            with sessions() as session:
                rows = session.execute(select(Source.slug, Source.allowed_origins)).all()
            origins_cache["by_slug"] = {s: list(o) for s, o in rows}
            origins_cache["at"] = now
        return origins_cache["by_slug"].get(slug, [])

    async def limited(request: Request, slug: str) -> JSONResponse:
        response = _error(429, "too many requests from this address")
        origin = request.headers.get("origin")
        if origin and origin in await run_in_threadpool(origins_for, slug):
            response.headers.update({"Access-Control-Allow-Origin": origin, "Vary": "Origin"})
        return response

    async def intake(request: Request, slug: str, token: str | None) -> JSONResponse:
        if not per_ip.allow(client_ip(request)):
            return await limited(request, slug)
        source = await run_in_threadpool(load_source, slug)
        if source is None:
            return _error(404, "unknown source")
        response = await checked_intake(request, source, token)
        response.headers.update(cors(source, request))
        return response

    async def checked_intake(request: Request, source: Source, token: str | None) -> JSONResponse:
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
        # The source's budget is spent only by requests that passed auth, so a stranger with a
        # wrong credential cannot use up the real sender's allowance.
        if not per_source.allow(source.slug):
            return _error(429, "too many requests for this source")
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

    async def preflight(request: Request, slug: str) -> Response:
        """The browser's CORS check before a cross-origin form post. It carries no credential,
        so it reveals only whether this origin may post to the source."""
        if not per_ip.allow(client_ip(request)):
            return await limited(request, slug)
        source = await run_in_threadpool(load_source, slug)
        headers = cors(source, request)
        if headers:
            headers |= {
                "Access-Control-Allow-Methods": "POST",
                "Access-Control-Allow-Headers": "Content-Type",
                "Access-Control-Max-Age": "600",
            }
        return Response(status_code=204, headers=headers)

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/intake/{slug}")
    async def intake_plain(request: Request, slug: str) -> JSONResponse:
        return await intake(request, slug, None)

    @app.post("/intake/{slug}/{token}")
    async def intake_with_token(request: Request, slug: str, token: str) -> JSONResponse:
        return await intake(request, slug, token)

    @app.options("/intake/{slug}")
    async def preflight_plain(request: Request, slug: str) -> Response:
        return await preflight(request, slug)

    @app.options("/intake/{slug}/{token}")
    async def preflight_with_token(request: Request, slug: str, token: str) -> Response:
        return await preflight(request, slug)

    app.include_router(board_router(settings, sessions, clock, monotonic, client_ip))

    if WEB_DIST.is_dir():
        app.mount("/", StaticFiles(directory=WEB_DIST, html=True), name="web")
    return app
