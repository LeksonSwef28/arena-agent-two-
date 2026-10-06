"""Canonical workspace boundary for MCP project tools."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from arena.project_safe import project_safe_enabled

_BLOCKED_COMPONENTS = frozenset({".git"})


def workspace_root(ctx: Any) -> Path:
    """Return the configured --root, resolved once as the authority boundary."""
    try:
        cfg = ctx.app_config()
    except Exception:
        cfg = {}
    raw = cfg.get("root") if isinstance(cfg, dict) else None
    if not raw and project_safe_enabled():
        raise ValueError("project-safe mode requires an explicit configured workspace root")
    root = Path(str(raw or Path.home())).expanduser().resolve()
    if project_safe_enabled() and root == Path.home().resolve():
        raise ValueError("project-safe mode refuses the entire user home as workspace root")
    return root


def resolve_workspace_path(raw_path: str, ctx: Any) -> tuple[Path | None, str | None]:
    """Resolve an absolute/relative path and reject escape through links/junctions."""
    if not raw_path:
        return None, "missing path argument"
    if "\x00" in raw_path:
        return None, "path is not usable (embedded NUL)"
    try:
        root = workspace_root(ctx)
    except (ValueError, OSError, RuntimeError) as exc:
        return None, str(exc)
    try:
        candidate = Path(raw_path).expanduser()
        if not candidate.is_absolute():
            candidate = root / candidate
        resolved = candidate.resolve()
        rel = resolved.relative_to(root)
    except (ValueError, OSError, RuntimeError) as exc:
        return None, f"path outside configured workspace root ({type(exc).__name__})"
    if any(part in _BLOCKED_COMPONENTS for part in rel.parts):
        return None, "direct access to .git internals is not allowed; use git.* tools"
    if not ctx.under_root(resolved, root):
        return None, "path outside configured workspace root"
    return resolved, None


__all__ = ["workspace_root", "resolve_workspace_path"]
