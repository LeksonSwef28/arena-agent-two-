"""Regression tests for the fail-closed project-safe HTTP ingress gate."""
import asyncio

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from arena.project_safe_http import (
    PROJECT_SAFE_HTTP_ALLOWLIST,
    project_safe_http_allowed,
    project_safe_http_middleware,
)


def test_project_safe_http_allows_only_reviewed_surface():
    expected = {
        ("GET", "/health"),
        ("GET", "/v1/version"),
        ("GET", "/v1/status"),
        ("GET", "/v1/control/status"),
        ("POST", "/v1/control/halt"),
        ("POST", "/mcp"),
        ("DELETE", "/mcp"),
        ("GET", "/sse"),
        ("POST", "/messages"),
        ("GET", "/ws"),
        ("GET", "/v1/extension/status"),
        ("GET", "/v1/extension/policies"),
        ("GET", "/v1/extension/instructions"),
        ("POST", "/v1/extension/preview"),
        ("POST", "/v1/extension/execute"),
    }
    assert PROJECT_SAFE_HTTP_ALLOWLIST == expected


def test_project_safe_http_denies_known_bypasses_and_host_control():
    denied = [
        ("POST", "/v1/exec"),
        ("POST", "/v1/exec/script"),
        ("POST", "/v1/exec/stream"),
        ("POST", "/v2/exec"),
        ("POST", "/run"),
        ("POST", "/tool"),
        ("POST", "/v1/tasks"),
        ("POST", "/v1/batch"),
        ("POST", "/v1/mission/run"),
        ("POST", "/v1/mission/schedules/tick"),
        ("POST", "/v1/skills/install"),
        ("POST", "/v1/skills/reload"),
        ("POST", "/v1/admin/update/apply"),
        ("POST", "/v1/tunnels/start"),
        ("POST", "/v1/desktop/click"),
        ("POST", "/v1/mobile/connect"),
        ("POST", "/v1/browser/cdp/navigate"),
        ("POST", "/v1/cdp/eval"),
        ("POST", "/v1/control/unhalt"),
        ("POST", "/v1/control/resume"),
        ("POST", "/v1/control/yolo"),
    ]
    for method, path in denied:
        assert not project_safe_http_allowed(method, path), (method, path)


def test_project_safe_http_is_method_sensitive_and_unknown_is_denied():
    assert not project_safe_http_allowed("POST", "/health")
    assert not project_safe_http_allowed("GET", "/v1/extension/execute")
    assert not project_safe_http_allowed("GET", "/future/new/route")
    assert not project_safe_http_allowed("", "")


def test_project_safe_http_options_only_for_reviewed_paths():
    assert project_safe_http_allowed("OPTIONS", "/mcp")
    assert project_safe_http_allowed("OPTIONS", "/v1/extension/execute")
    assert not project_safe_http_allowed("OPTIONS", "/v1/exec")
    assert not project_safe_http_allowed("OPTIONS", "/future/new/route")



def test_project_safe_http_middleware_blocks_before_handler(monkeypatch):
    calls = {"danger": 0}

    async def scenario():
        async def health(_request):
            return web.json_response({"ok": True})

        async def danger(_request):
            calls["danger"] += 1
            return web.json_response({"executed": True})

        app = web.Application(middlewares=[project_safe_http_middleware])
        app.router.add_get("/health", health)
        app.router.add_post("/v1/exec", danger)

        client = TestClient(TestServer(app))
        await client.start_server()
        try:
            allowed = await client.get("/health")
            assert allowed.status == 200

            blocked = await client.post("/v1/exec")
            assert blocked.status == 403
            payload = await blocked.json()
            assert payload["error"] == "project_safe_ingress_denied"
            assert calls["danger"] == 0
        finally:
            await client.close()

    monkeypatch.setenv("ARENA_PROJECT_SAFE", "1")
    asyncio.run(scenario())


def test_project_safe_http_middleware_is_transparent_when_disabled(monkeypatch):
    calls = {"danger": 0}

    async def scenario():
        async def danger(_request):
            calls["danger"] += 1
            return web.json_response({"executed": True})

        app = web.Application(middlewares=[project_safe_http_middleware])
        app.router.add_post("/v1/exec", danger)

        client = TestClient(TestServer(app))
        await client.start_server()
        try:
            response = await client.post("/v1/exec")
            assert response.status == 200
            assert calls["danger"] == 1
        finally:
            await client.close()

    monkeypatch.delenv("ARENA_PROJECT_SAFE", raising=False)
    asyncio.run(scenario())
