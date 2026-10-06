"""Canonical workspace boundary for MCP project tools."""
from __future__ import annotations

import os
import re
import stat
from pathlib import Path, PureWindowsPath
from typing import Any

from arena.project_safe import project_safe_enabled

_BLOCKED_COMPONENTS = frozenset({".git"})
_WINDOWS_RESERVED = frozenset({
    "con", "prn", "aux", "nul",
    *(f"com{i}" for i in range(1, 10)),
    *(f"lpt{i}" for i in range(1, 10)),
})
_DRIVE_ONLY = re.compile(r"^[A-Za-z]:$")


def windows_path_rejection_reason(raw_path: str) -> str | None:
    """Return a project-safe rejection reason for ambiguous Windows path syntax."""
    raw = str(raw_path or "")
    if not raw:
        return None

    folded = raw.casefold()
    if folded.startswith(("\\\\?\\", "\\\\.\\", "\\??\\")):
        return "Windows device or extended path namespaces are not allowed"

    p = PureWindowsPath(raw)
    if p.drive and not p.root:
        return "Windows drive-relative paths are not allowed"
    if p.drive.startswith("\\\\"):
        return "UNC paths are not allowed in project-safe mode"

    anchor = p.anchor
    for part in p.parts:
        if part == anchor or part in {".", ".."} or _DRIVE_ONLY.fullmatch(part):
            continue
        if part.rstrip(" .") != part:
            return "Windows path components with trailing dots or spaces are not allowed"
        if ":" in part:
            return "NTFS alternate data streams are not allowed"
        if part.split(".", 1)[0].casefold() in _WINDOWS_RESERVED:
            return f"reserved Windows device name is not allowed: {part}"
    return None


def _is_reparse_point(path: Path) -> bool:
    try:
        st = os.lstat(path)
    except (FileNotFoundError, NotADirectoryError, OSError):
        return False
    if stat.S_ISLNK(st.st_mode):
        return True
    attrs = int(getattr(st, "st_file_attributes", 0) or 0)
    flag = int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))
    return bool(attrs & flag)


def _first_existing_reparse(root: Path, candidate: Path) -> Path | None:
    try:
        rel = candidate.relative_to(root)
    except ValueError:
        return None

    current = root
    if _is_reparse_point(current):
        return current

    for part in rel.parts:
        current = current / part
        if _is_reparse_point(current):
            return current
        if not current.exists():
            break
    return None


def _has_multiple_hardlinks(path: Path) -> bool:
    try:
        if not path.exists() or not path.is_file():
            return False
        return int(path.stat().st_nlink) > 1
    except OSError:
        return True


def workspace_root(ctx: Any) -> Path:
    """Return the configured --root as the canonical authority boundary."""
    try:
        cfg = ctx.app_config()
    except Exception:
        cfg = {}

    raw = cfg.get("root") if isinstance(cfg, dict) else None
    if not raw and project_safe_enabled():
        raise ValueError("project-safe mode requires an explicit configured workspace root")

    raw_root = Path(str(raw or Path.home())).expanduser()

    if project_safe_enabled():
        if os.name == "nt":
            reason = windows_path_rejection_reason(str(raw_root))
            if reason:
                raise ValueError(f"invalid project-safe workspace root: {reason}")
        if _is_reparse_point(raw_root):
            raise ValueError(
                "project-safe workspace root must not itself be a symlink, junction, or reparse point"
            )

    root = raw_root.resolve()
    if project_safe_enabled():
        if root == Path.home().resolve():
            raise ValueError("project-safe mode refuses the entire user home as workspace root")
        if not root.exists() or not root.is_dir():
            raise ValueError("project-safe workspace root must exist and be a directory")
    return root


def resolve_workspace_path(
    raw_path: str,
    ctx: Any,
    *,
    for_write: bool = False,
) -> tuple[Path | None, str | None]:
    """Resolve a path and reject lexical/canonical workspace escapes."""
    if not raw_path:
        return None, "missing path argument"
    if "\x00" in raw_path:
        return None, "path is not usable (embedded NUL)"

    try:
        root = workspace_root(ctx)
    except (ValueError, OSError, RuntimeError) as exc:
        return None, str(exc)

    if project_safe_enabled() and os.name == "nt":
        reason = windows_path_rejection_reason(raw_path)
        if reason:
            return None, reason

    try:
        candidate = Path(raw_path).expanduser()
        if not candidate.is_absolute():
            candidate = root / candidate

        lexical = Path(os.path.abspath(os.fspath(candidate)))
        lexical_rel = lexical.relative_to(root)
        if any(part.casefold() in _BLOCKED_COMPONENTS for part in lexical_rel.parts):
            return None, "direct access to .git internals is not allowed; use git.* tools"

        if project_safe_enabled() and for_write:
            reparse = _first_existing_reparse(root, lexical)
            if reparse is not None:
                return None, (
                    "project-safe writes through symlink, junction, or reparse points "
                    f"are not allowed: {reparse}"
                )

        resolved = candidate.resolve(strict=False)
        rel = resolved.relative_to(root)
    except (ValueError, OSError, RuntimeError) as exc:
        return None, f"path outside configured workspace root ({type(exc).__name__})"

    if any(part.casefold() in _BLOCKED_COMPONENTS for part in rel.parts):
        return None, "direct access to .git internals is not allowed; use git.* tools"

    if not ctx.under_root(resolved, root):
        return None, "path outside configured workspace root"

    if project_safe_enabled() and for_write and _has_multiple_hardlinks(resolved):
        return None, "project-safe writes to multiply-linked files are not allowed"

    return resolved, None


__all__ = [
    "workspace_root",
    "resolve_workspace_path",
    "windows_path_rejection_reason",
]
