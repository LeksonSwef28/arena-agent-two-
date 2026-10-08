"""Standalone MCP Streamable HTTP server CLI."""
from __future__ import annotations

import argparse

from arena.mcp.standalone_common import VERSION, ThreadingHTTPServer
from arena.mcp.standalone_http import H
from arena.project_safe import require_project_safe_secondary_server_disabled


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8767)
    a = p.parse_args()
    require_project_safe_secondary_server_disabled("standalone MCP HTTP server")
    print(f"Arena MCP Stream server v{VERSION} on http://{a.host}:{a.port}/mcp", flush=True)
    srv = ThreadingHTTPServer((a.host, a.port), H)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
