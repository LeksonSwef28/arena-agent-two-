"""Fail-closed project-safe policy for local development agents.

Enabled only when ARENA_PROJECT_SAFE=1.  The policy is intentionally smaller
than the normal cautious profile: a project agent sees project files, read-only
Git, and (optionally) workspace writes / fenced code.  Host shell, installs,
networking, services, desktop/mobile control and external MCP remain unavailable.
"""
from __future__ import annotations

import os
from typing import Any, Iterable

_TRUE = frozenset({"1", "true", "yes", "on"})

READ_TOOLS = frozenset({
    "exec.ping", "exec.echo",
    "fs.read", "fs.view", "fs.list", "fs.search", "fs.grep", "fs.tree", "fs.diff",
    "git.status", "git.diff", "git.log",
    "sys.status", "workbench.status",
    "runtime.probe", "runtime.list", "runtime.compat",
})

WRITE_TOOLS = frozenset({
    "fs.create", "fs.edit", "fs.write",
})

FENCED_CODE_TOOLS = frozenset({"code.run"})


def _flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in _TRUE


def project_safe_enabled() -> bool:
    return _flag("ARENA_PROJECT_SAFE")


def project_writes_enabled() -> bool:
    return _flag("ARENA_PROJECT_SAFE_WRITES")


def project_code_enabled() -> bool:
    return _flag("ARENA_PROJECT_SAFE_CODE")


def project_safe_secondary_server_block_reason(server_name: str) -> str | None:
    """Return a fail-closed reason for secondary listeners in project-safe mode."""
    if not project_safe_enabled():
        return None
    name = str(server_name or "secondary server").strip() or "secondary server"
    return (
        f"{name} is disabled in ARENA_PROJECT_SAFE mode; "
        "use the unified loopback bridge and canonical project-safe ingress"
    )


def require_project_safe_secondary_server_disabled(server_name: str) -> None:
    """Raise before any secondary server binds/spawns while project-safe is active."""
    reason = project_safe_secondary_server_block_reason(server_name)
    if reason:
        raise RuntimeError(reason)


def project_safe_block_reason(tool: str) -> str | None:
    """Return a refusal reason for a tool, or None when project-safe allows it."""
    if not project_safe_enabled():
        return None
    name = str(tool or "").strip()
    if name in READ_TOOLS:
        return None
    if name in WRITE_TOOLS:
        if project_writes_enabled():
            return None
        return "workspace writes are disabled for this project-safe session"
    if name in FENCED_CODE_TOOLS:
        if project_code_enabled():
            return None
        return "fenced code execution is disabled for this project-safe session"
    return f"tool {name!r} is outside the project-safe capability set"


def filter_project_safe_tools(tools: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Hide blocked capabilities from tool catalogs to reduce model confusion."""
    if not project_safe_enabled():
        return tools if isinstance(tools, list) else list(tools)
    rows = list(tools)
    return [tool for tool in rows if project_safe_block_reason(str(tool.get("name", ""))) is None]


__all__ = [
    "READ_TOOLS", "WRITE_TOOLS", "FENCED_CODE_TOOLS",
    "project_safe_enabled", "project_writes_enabled", "project_code_enabled",
    "project_safe_secondary_server_block_reason",
    "require_project_safe_secondary_server_disabled",
    "project_safe_block_reason", "filter_project_safe_tools",
]
