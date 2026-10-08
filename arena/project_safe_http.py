"""Fail-closed HTTP ingress policy for Arena project-safe mode."""
from __future__ import annotations

import logging
from typing import Final

from aiohttp import web

from arena.project_safe import project_safe_enabled
from arena.web_utils import CORS_HEADERS

_LOG = logging.getLogger(__name__)

# Exact method/path pairs only.  A newly added Arena route is denied in
# project-safe mode until it is deliberately reviewed and added here.
PROJECT_SAFE_HTTP_ALLOWLIST: Final[frozenset[tuple[str, str]]] = frozenset({
    ("GET", "/health"),
    ("GET", "/v1/version"),
    ("GET", "/v1/status"),
    ("GET", "/v1/control/status"),
    ("POST", "/v1/control/halt"),

    # MCP transports. Tool-level capability checks still apply downstream.
    ("POST", "/mcp"),
    ("DELETE", "/mcp"),
    ("GET", "/sse"),
    ("POST", "/messages"),
    ("GET", "/ws"),

    # Browser extension: status/read-only metadata plus preview/execute.
    # execute is allowed only because the extension runtime delegates tool
    # calls to the canonical MCP dispatcher, where project-safe tool policy
    # is enforced again.
    ("GET", "/v1/extension/status"),
    ("GET", "/v1/extension/policies"),
    ("GET", "/v1/extension/instructions"),
    ("POST", "/v1/extension/preview"),
    ("POST", "/v1/extension/execute"),
})

_ALLOWED_PATHS: Final[frozenset[str]] = frozenset(
    path for _method, path in PROJECT_SAFE_HTTP_ALLOWLIST
)


def project_safe_http_allowed(method: str, path: str) -> bool:
    """Return True only for explicitly reviewed project-safe HTTP ingress."""
    clean_method = str(method or "").upper()
    clean_path = str(path or "")

    # Preflight is non-mutating, but only for paths that are themselves in the
    # reviewed surface.  Do not let OPTIONS become a wildcard route probe.
    if clean_method == "OPTIONS":
        return clean_path in _ALLOWED_PATHS

    return (clean_method, clean_path) in PROJECT_SAFE_HTTP_ALLOWLIST


@web.middleware
async def project_safe_http_middleware(
    request: web.Request,
    handler,
) -> web.StreamResponse:
    """Deny every unreviewed HTTP route while ARENA_PROJECT_SAFE is enabled."""
    if not project_safe_enabled():
        return await handler(request)

    if project_safe_http_allowed(request.method, request.path):
        if request.method == "OPTIONS":
            return web.Response(status=204, headers=dict(CORS_HEADERS))
        return await handler(request)

    _LOG.warning(
        "SECURITY_INGRESS_DENIED method=%s path=%s remote=%s",
        request.method,
        request.path,
        request.remote or "",
    )
    return web.json_response(
        {
            "ok": False,
            "error": "project_safe_ingress_denied",
            "message": "Endpoint is disabled in ARENA_PROJECT_SAFE mode",
        },
        status=403,
        headers=dict(CORS_HEADERS),
    )


__all__ = [
    "PROJECT_SAFE_HTTP_ALLOWLIST",
    "project_safe_http_allowed",
    "project_safe_http_middleware",
]
