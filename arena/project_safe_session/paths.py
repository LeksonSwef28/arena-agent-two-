"""Project-safe session state-root identity and boundary checks."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

from arena.mcp.project_boundary import windows_path_rejection_reason

_STATE_ROOT_ENV = "ARENA_PROJECT_SAFE_STATE_ROOT"


class ProjectSafeStateError(ValueError):
    """Project-safe durable state cannot be located safely."""


def _canonical_project_root(project_root: str | os.PathLike[str]) -> Path:
    try:
        root = Path(project_root).expanduser().resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise ProjectSafeStateError(f"project root is not resolvable: {exc}") from exc
    if not root.is_dir():
        raise ProjectSafeStateError("project root must exist and be a directory")
    return root


def _is_equal_or_descendant(candidate: Path, root: Path) -> bool:
    try:
        candidate.relative_to(root)
        return True
    except ValueError:
        return False


def default_project_safe_state_root() -> Path:
    """Return the platform-local default without creating it."""
    configured = os.environ.get(_STATE_ROOT_ENV, "").strip()
    if configured:
        return Path(configured).expanduser()

    if os.name == "nt":
        local = os.environ.get("LOCALAPPDATA", "").strip()
        if not local:
            raise ProjectSafeStateError(
                "LOCALAPPDATA is required when ARENA_PROJECT_SAFE_STATE_ROOT is not set"
            )
        return Path(local) / "Arena" / "project-safe"

    xdg = os.environ.get("XDG_STATE_HOME", "").strip()
    base = Path(xdg).expanduser() if xdg else (Path.home() / ".local" / "state")
    return base / "arena" / "project-safe"


def _validate_raw_state_root(raw: Path) -> None:
    if not raw.is_absolute():
        raise ProjectSafeStateError("project-safe state root must be an absolute path")
    if os.name == "nt":
        reason = windows_path_rejection_reason(str(raw))
        if reason:
            raise ProjectSafeStateError(f"invalid project-safe state root: {reason}")


def resolve_project_safe_state_root(
    project_root: str | os.PathLike[str],
    state_root: str | os.PathLike[str] | None = None,
    *,
    create: bool = False,
) -> Path:
    """Resolve state outside the workspace, fail-closed before any creation.

    Existing parent symlinks/reparse targets are accounted for by resolve().
    When creation is requested, canonical containment is checked again after
    mkdir so the published path cannot quietly become workspace-local.
    """
    project = _canonical_project_root(project_root)
    raw = Path(state_root).expanduser() if state_root is not None else default_project_safe_state_root()
    _validate_raw_state_root(raw)

    lexical = Path(os.path.abspath(os.fspath(raw)))
    if _is_equal_or_descendant(lexical, project):
        raise ProjectSafeStateError(
            "project-safe state root must be outside the configured workspace"
        )

    try:
        resolved = raw.resolve(strict=False)
    except (OSError, RuntimeError) as exc:
        raise ProjectSafeStateError(f"project-safe state root is not resolvable: {exc}") from exc

    if _is_equal_or_descendant(resolved, project):
        raise ProjectSafeStateError(
            "project-safe state root resolves inside the configured workspace"
        )

    if create:
        try:
            raw.mkdir(parents=True, exist_ok=True)
            resolved = raw.resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise ProjectSafeStateError(f"cannot create project-safe state root: {exc}") from exc
        if not resolved.is_dir():
            raise ProjectSafeStateError("project-safe state root is not a directory")
        if _is_equal_or_descendant(resolved, project):
            raise ProjectSafeStateError(
                "project-safe state root resolved inside the configured workspace after creation"
            )

    return resolved


def project_fingerprint(project_root: str | os.PathLike[str]) -> str:
    """Stable project-v1 fingerprint for the canonical root on this platform."""
    root = _canonical_project_root(project_root)
    identity = os.path.normcase(os.fspath(root))
    if os.name == "nt":
        identity = identity.replace("\\", "/")
    payload = ("project-v1\x00" + identity).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


__all__ = [
    "ProjectSafeStateError",
    "default_project_safe_state_root",
    "project_fingerprint",
    "resolve_project_safe_state_root",
]
