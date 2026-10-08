from __future__ import annotations

import asyncio
import importlib.util
import sys
from pathlib import Path

import pytest

import arena.grpc.runtime as grpc_runtime
import arena.mcp.standalone_rpc as standalone_rpc
import arena.mcp.standalone_server as standalone_http
import arena.mcp.standalone_tools as standalone_tools
import arena.mcp.ws_server as standalone_ws
from arena.input_helper import helper_server
from arena.project_safe import (
    project_safe_secondary_server_block_reason,
    require_project_safe_secondary_server_disabled,
)


ROOT = Path(__file__).resolve().parents[1]


def _load_script(relpath: str, module_name: str):
    path = ROOT / relpath
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_secondary_server_guard_is_transparent_outside_project_safe(monkeypatch):
    monkeypatch.delenv("ARENA_PROJECT_SAFE", raising=False)
    assert project_safe_secondary_server_block_reason("x") is None
    require_project_safe_secondary_server_disabled("x")


def test_secondary_server_guard_fails_closed_in_project_safe(monkeypatch):
    monkeypatch.setenv("ARENA_PROJECT_SAFE", "1")
    reason = project_safe_secondary_server_block_reason("test secondary")
    assert reason and "disabled" in reason and "project-safe" in reason.lower()
    with pytest.raises(RuntimeError, match="disabled"):
        require_project_safe_secondary_server_disabled("test secondary")


def test_standalone_http_refuses_before_server_bind(monkeypatch):
    monkeypatch.setenv("ARENA_PROJECT_SAFE", "1")
    monkeypatch.setattr(sys, "argv", ["arena-mcp-http"])

    def forbidden_server(*_args, **_kwargs):
        raise AssertionError("standalone HTTP server reached bind")

    monkeypatch.setattr(standalone_http, "ThreadingHTTPServer", forbidden_server)

    with pytest.raises(RuntimeError, match="disabled"):
        standalone_http.main()


def test_standalone_ws_refuses_before_socket_creation(monkeypatch):
    monkeypatch.setenv("ARENA_PROJECT_SAFE", "1")
    monkeypatch.setattr(sys, "argv", ["arena-mcp-ws"])

    def forbidden_socket(*_args, **_kwargs):
        raise AssertionError("standalone WS server created a socket")

    monkeypatch.setattr(standalone_ws.socket, "socket", forbidden_socket)

    with pytest.raises(RuntimeError, match="disabled"):
        standalone_ws.main()


def test_grpc_secondary_refuses_before_task_spawn(monkeypatch):
    monkeypatch.setenv("ARENA_PROJECT_SAFE", "1")

    def forbidden_create_task(*_args, **_kwargs):
        raise AssertionError("gRPC secondary server created a task")

    monkeypatch.setattr(grpc_runtime.asyncio, "create_task", forbidden_create_task)

    with pytest.raises(RuntimeError, match="disabled"):
        grpc_runtime.start_grpc_server({"port": 8765, "token": "x"})


def test_web_gateway_refuses_before_http_server_bind(monkeypatch):
    monkeypatch.setenv("ARENA_PROJECT_SAFE", "1")
    mod = _load_script("bin/web_gateway.py", "project_safe_web_gateway_test")
    monkeypatch.setattr(sys, "argv", ["web_gateway.py"])

    def forbidden_server(*_args, **_kwargs):
        raise AssertionError("web gateway reached bind")

    monkeypatch.setattr(mod, "ThreadingHTTPServer", forbidden_server)

    with pytest.raises(RuntimeError, match="disabled"):
        mod.main()


def test_serena_refuses_before_external_process_spawn(monkeypatch):
    monkeypatch.setenv("ARENA_PROJECT_SAFE", "1")
    mod = _load_script("scripts/start_serena_mcp.py", "project_safe_serena_test")

    def forbidden_popen(*_args, **_kwargs):
        raise AssertionError("Serena helper spawned an external MCP process")

    monkeypatch.setattr(mod.subprocess, "Popen", forbidden_popen)

    with pytest.raises(RuntimeError, match="disabled"):
        mod.start_server(project_dir=ROOT)


def test_input_helper_refuses_before_http_server_bind(monkeypatch):
    monkeypatch.setenv("ARENA_PROJECT_SAFE", "1")
    monkeypatch.setattr(sys, "argv", ["helper_server.py", "--token", "not-used"])

    def forbidden_server(*_args, **_kwargs):
        raise AssertionError("input helper reached bind")

    monkeypatch.setattr(helper_server, "HTTPServer", forbidden_server)

    assert helper_server.main() == 3


def test_standalone_dispatchers_fail_closed_even_if_called_directly(monkeypatch):
    monkeypatch.setenv("ARENA_PROJECT_SAFE", "1")

    tool_result = standalone_tools.call_tool("exec.ping", {})
    assert tool_result.get("isError") is True
    assert "standalone MCP tool dispatcher" in tool_result["content"][0]["text"]

    rpc_result = standalone_rpc.handle_rpc({
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": "exec.ping", "arguments": {}},
    })
    assert rpc_result is not None
    assert rpc_result.get("error")
    assert "standalone MCP dispatcher" in str(rpc_result["error"])



def test_grpc_loop_refuses_before_application_setup(monkeypatch):
    monkeypatch.setenv("ARENA_PROJECT_SAFE", "1")

    def forbidden_app(*_args, **_kwargs):
        raise AssertionError("gRPC loop created an aiohttp application")

    monkeypatch.setattr(grpc_runtime.web, "Application", forbidden_app)

    with pytest.raises(RuntimeError, match="disabled"):
        asyncio.run(grpc_runtime.grpc_server_loop({"port": 8765, "token": "x"}))


def test_web_gateway_handler_refuses_even_if_hosted_manually(monkeypatch):
    monkeypatch.setenv("ARENA_PROJECT_SAFE", "1")
    mod = _load_script("bin/web_gateway.py", "project_safe_web_gateway_handler_test")
    captured = {}

    class Dummy:
        path = "/run"

        def _json(self, obj, code=200):
            captured["obj"] = obj
            captured["code"] = code
            return (obj, code)

    result = mod.H.do_POST(Dummy())
    assert result[1] == 403
    assert captured["code"] == 403
    assert "disabled" in captured["obj"]["error"]


def test_input_helper_auth_refuses_project_safe_before_token_check(monkeypatch):
    monkeypatch.setenv("ARENA_PROJECT_SAFE", "1")
    captured = {"status": None, "body": b""}

    class Writer:
        def write(self, data):
            captured["body"] += data

    dummy = object.__new__(helper_server.InputHandler)
    dummy.wfile = Writer()
    dummy.send_response = lambda code: captured.__setitem__("status", code)
    dummy.end_headers = lambda: None

    assert helper_server.InputHandler._check_auth(dummy) is False
    assert captured["status"] == 503
    assert b"PROJECT-SAFE" in captured["body"]
