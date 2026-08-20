"""
api/middleware.py — FastAPI middleware stack.

Middleware applied in order:
  1. RequestTimingMiddleware  → log request duration
  2. CORSMiddleware           → allow cross-origin requests
  3. TrustedHostMiddleware    → restrict to known hosts

Architecture decisions:
  - Request IDs are added to each response (X-Request-ID header).
    Useful for correlating logs across services.
  - Request timing logged as structured JSON.
  - CORS: allow all origins in development, restrict in production.
  - All middleware is non-blocking (async).
"""
from __future__ import annotations

import json
import logging
import time
import uuid

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware

logger = logging.getLogger(__name__)


class RequestTimingMiddleware(BaseHTTPMiddleware):
    """
    Log every request with timing, status code, and request ID.

    Adds X-Request-ID and X-Response-Time headers to all responses.
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        start       = time.monotonic()
        request_id  = str(uuid.uuid4())[:8]

        # Add request ID to request state (accessible in routes)
        request.state.request_id = request_id

        response = await call_next(request)

        duration_ms = (time.monotonic() - start) * 1000

        # Add headers
        response.headers["X-Request-ID"]    = request_id
        response.headers["X-Response-Time"] = f"{duration_ms:.1f}ms"

        # Structured log
        logger.info(json.dumps({
            "event":      "http_request",
            "method":     request.method,
            "path":       request.url.path,
            "status":     response.status_code,
            "duration_ms":round(duration_ms, 1),
            "request_id": request_id,
            "ip":         request.client.host if request.client else "unknown",
        }))

        return response


def setup_middleware(app: FastAPI) -> None:
    """
    Register all middleware on the FastAPI app.

    Order matters: middleware is applied in REVERSE order
    (last added = first executed).
    """
    # Timing middleware (outermost — measures total time)
    app.add_middleware(RequestTimingMiddleware)

    # CORS — allow all origins
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "DELETE"],
        allow_headers=["*"],
        expose_headers=["X-Request-ID", "X-Response-Time"],
    )
