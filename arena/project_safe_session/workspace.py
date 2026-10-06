"""Workspace digest v1 and resource CAS for project-safe sessions."""
from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from arena.mcp.git_safe import run_project_safe_git
from arena.mcp.project_boundary import resolve_project_safe_path

from .action_models import FileResourceBefore
from .canonical import canonical_sha256
from .paths import project_fingerprint
from .schema_utils import relative_path_value, sha256_value

RESOURCE_BACKUP_LIMIT = 16 * 1024 * 1024


class WorkspaceError(RuntimeError):
    reason = "WORKSPACE_ERROR"


class WorkspaceGitError(WorkspaceError):
    reason = "WORKSPACE_GIT_ERROR"


class UnsupportedRepoLayoutError(WorkspaceError):
    reason = "UNSUPPORTED_REPO_LAYOUT"


class WorkspaceUnstableError(WorkspaceError):
    reason = "WORKSPACE_UNSTABLE"


class WorkspaceDriftError(WorkspaceError):
    reason = "WORKSPACE_DRIFT"


class ResourceBoundaryError(WorkspaceError):
    reason = "POLICY_DENIED"


class ResourceTooLargeError(WorkspaceError):
    reason = "RESOURCE_TOO_LARGE"


class ResourceDriftError(WorkspaceError):
    reason = "TARGET_DRIFT"


@dataclass(frozen=True)
class IndexEntry:
    path: str
    mode: str
    object_id: str
    stage: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "mode": self.mode,
            "object_id": self.object_id,
            "stage": self.stage,
        }


@dataclass(frozen=True)
class WorktreeEntry:
    path: str
    state: str
    content_sha256: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "state": self.state,
            "content_sha256": self.content_sha256,
        }


@dataclass(frozen=True)
class WorkspaceManifest:
    head_sha: str
    index: tuple[IndexEntry, ...]
    worktree: tuple[WorktreeEntry, ...]
    untracked: tuple[WorktreeEntry, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": "workspace-v1",
            "head_sha": self.head_sha,
            "index": [item.to_dict() for item in self.index],
            "worktree": [item.to_dict() for item in self.worktree],
            "untracked": [item.to_dict() for item in self.untracked],
        }

    @property
    def digest(self) -> str:
        return canonical_sha256(self.to_dict())


def _git(repo: Path, args: list[str], *, timeout: int = 30) -> bytes:
    code, stdout, stderr = run_project_safe_git(repo, args, timeout=timeout)
    if code != 0:
        message = stderr.decode("utf-8", "replace").strip()
        raise WorkspaceGitError(
            f"git {' '.join(args[:2])} failed (exit={code}): {message}"
        )
    return stdout


def _canonical_repo_root(project_root: str | os.PathLike[str]) -> Path:
    try:
        root = Path(project_root).expanduser().resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise WorkspaceError(f"project root is not resolvable: {exc}") from exc
    if not root.is_dir():
        raise WorkspaceError("project root must be a directory")

    top_raw = _git(root, ["rev-parse", "--show-toplevel"]).strip()
    if not top_raw:
        raise WorkspaceGitError("git did not report a worktree root")
    top = Path(os.fsdecode(top_raw)).resolve(strict=True)
    if top != root:
        raise UnsupportedRepoLayoutError(
            f"project-safe v1 requires project root == Git worktree root; git={top}, project={root}"
        )
    return root


def _parse_index(raw: bytes) -> tuple[IndexEntry, ...]:
    entries: list[IndexEntry] = []
    for chunk in raw.split(b"\0"):
        if not chunk:
            continue
        meta, sep, path_bytes = chunk.partition(b"\t")
        if not sep:
            raise WorkspaceGitError("unexpected git ls-files --stage record")
        parts = meta.split()
        if len(parts) != 3:
            raise WorkspaceGitError("unexpected git index metadata shape")
        mode_b, object_b, stage_b = parts
        mode = mode_b.decode("ascii", "strict")
        object_id = object_b.decode("ascii", "strict").lower()
        stage = int(stage_b.decode("ascii", "strict"))
        if mode == "160000":
            raise UnsupportedRepoLayoutError("Git submodules are unsupported in project-safe v1")
        if stage != 0:
            raise UnsupportedRepoLayoutError(
                "unmerged/conflicted Git index stages are unsupported in project-safe v1"
            )
        if len(object_id) not in {40, 64} or any(ch not in "0123456789abcdef" for ch in object_id):
            raise WorkspaceGitError("unexpected Git object id in index")
        path = relative_path_value(os.fsdecode(path_bytes), "git index path")
        entries.append(IndexEntry(path=path, mode=mode, object_id=object_id, stage=stage))
    return tuple(sorted(entries, key=lambda item: (item.path, item.stage, item.mode, item.object_id)))


def _hash_regular_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _worktree_entry(root: Path, relative: str) -> WorktreeEntry:
    relative = relative_path_value(relative, "workspace path")
    path = root / relative
    try:
        if path.is_symlink():
            target = os.readlink(path)
            digest = hashlib.sha256(os.fsencode(target)).hexdigest()
            return WorktreeEntry(path=relative, state="SYMLINK", content_sha256=digest)
        if not path.exists():
            return WorktreeEntry(path=relative, state="DELETED", content_sha256=None)
        resolved, reason = resolve_project_safe_path(relative, root, for_write=False)
        if reason or resolved is None:
            raise UnsupportedRepoLayoutError(
                f"workspace path is outside project-safe boundary: {relative}: {reason}"
            )
        if not resolved.is_file():
            raise UnsupportedRepoLayoutError(
                f"workspace path changed to unsupported file type: {relative}"
            )
        return WorktreeEntry(
            path=relative,
            state="MODIFIED",
            content_sha256=_hash_regular_file(resolved),
        )
    except OSError as exc:
        raise WorkspaceError(f"cannot hash workspace path {relative}: {exc}") from exc


def _parse_porcelain_status(raw: bytes) -> tuple[list[str], list[str]]:
    """Return (unstaged_tracked, untracked) from porcelain-v1 -z --no-renames."""
    changed: set[str] = set()
    untracked: set[str] = set()
    for chunk in raw.split(b"\0"):
        if not chunk:
            continue
        if len(chunk) < 4 or chunk[2:3] != b" ":
            raise WorkspaceGitError("unexpected git status --porcelain=v1 record")
        status = chunk[:2].decode("ascii", "strict")
        path = relative_path_value(os.fsdecode(chunk[3:]), "git status path")
        if status == "??":
            untracked.add(path)
            continue
        if status == "!!":
            continue
        if status[1] != " ":
            changed.add(path)
    return sorted(changed), sorted(untracked)


def compute_workspace_manifest_once(
    project_root: str | os.PathLike[str],
) -> WorkspaceManifest:
    root = _canonical_repo_root(project_root)

    head = _git(root, ["rev-parse", "--verify", "HEAD"]).strip().decode("ascii", "strict").lower()
    if len(head) not in {40, 64} or any(ch not in "0123456789abcdef" for ch in head):
        raise WorkspaceGitError("HEAD did not resolve to a supported Git object id")

    index = _parse_index(_git(root, ["ls-files", "--stage", "-z"]))

    status_raw = _git(
        root,
        [
            "-c", "core.autocrlf=input",
            "-c", "core.fileMode=false",
            "status", "--porcelain=v1", "-z", "--untracked-files=all",
            "--ignore-submodules=none", "--no-renames",
        ],
    )
    changed, untracked_names = _parse_porcelain_status(status_raw)

    worktree = tuple(_worktree_entry(root, name) for name in changed)
    untracked = tuple(_worktree_entry(root, name) for name in untracked_names)
    return WorkspaceManifest(
        head_sha=head,
        index=index,
        worktree=worktree,
        untracked=untracked,
    )


def compute_workspace_digest_v1(
    project_root: str | os.PathLike[str],
) -> tuple[WorkspaceManifest, str]:
    """Double-read the Git-visible workspace and reject an unstable snapshot."""
    first = compute_workspace_manifest_once(project_root)
    second = compute_workspace_manifest_once(project_root)
    if first != second:
        raise WorkspaceUnstableError(
            "workspace changed while workspace_digest_v1 was being captured"
        )
    return second, second.digest


def assert_flow_workspace_guard(
    project_root: str | os.PathLike[str],
    expected_digest: str,
) -> WorkspaceManifest:
    expected = sha256_value(expected_digest, "flow.workspace_digest_baseline")
    assert expected is not None
    manifest, actual = compute_workspace_digest_v1(project_root)
    if actual != expected:
        raise WorkspaceDriftError(
            f"workspace digest drifted: expected {expected}, got {actual}"
        )
    return manifest


def _resolve_resource(
    project_root: str | os.PathLike[str],
    relative_path: str,
) -> tuple[Path, Path]:
    root = Path(project_root).expanduser().resolve(strict=True)
    resolved, reason = resolve_project_safe_path(relative_path, root, for_write=True)
    if reason or resolved is None:
        raise ResourceBoundaryError(reason or "resource path is not allowed")
    return root, resolved


def capture_file_resource_before(
    project_root: str | os.PathLike[str],
    relative_path: str,
    *,
    size_limit: int = RESOURCE_BACKUP_LIMIT,
) -> FileResourceBefore:
    root, resolved = _resolve_resource(project_root, relative_path)
    canonical_relative = resolved.relative_to(root).as_posix()

    if not resolved.exists():
        return FileResourceBefore.from_dict(
            {
                "project_fingerprint": project_fingerprint(root),
                "canonical_relative_path": canonical_relative,
                "resource_type": "file",
                "exists": False,
                "content_sha256": None,
            }
        )
    if not resolved.is_file():
        raise ResourceBoundaryError("resource target must be a regular file")
    try:
        size = resolved.stat().st_size
    except OSError as exc:
        raise WorkspaceError(f"cannot stat resource: {exc}") from exc
    if size > size_limit:
        raise ResourceTooLargeError(
            f"resource is {size} bytes; P1-v1 limit is {size_limit} bytes"
        )

    first_hash = _hash_regular_file(resolved)
    second_hash = _hash_regular_file(resolved)
    if first_hash != second_hash:
        raise WorkspaceUnstableError("resource changed while resource_before was captured")

    return FileResourceBefore.from_dict(
        {
            "project_fingerprint": project_fingerprint(root),
            "canonical_relative_path": canonical_relative,
            "resource_type": "file",
            "exists": True,
            "content_sha256": second_hash,
        }
    )


def file_effect_target_fingerprint(resource_before: FileResourceBefore) -> str:
    return canonical_sha256(
        {
            "version": "file-target-v1",
            "project_fingerprint": resource_before.project_fingerprint,
            "canonical_relative_path": resource_before.canonical_relative_path,
            "resource_type": resource_before.resource_type.value,
            "before": {
                "exists": resource_before.exists,
                "content_sha256": resource_before.content_sha256,
            },
        }
    )


def validate_file_resource_cas(
    project_root: str | os.PathLike[str],
    resource_before: FileResourceBefore,
) -> Path:
    root, resolved = _resolve_resource(
        project_root,
        resource_before.canonical_relative_path,
    )
    if project_fingerprint(root) != resource_before.project_fingerprint:
        raise ResourceDriftError("resource project fingerprint changed")

    canonical_relative = resolved.relative_to(root).as_posix()
    if canonical_relative != resource_before.canonical_relative_path:
        raise ResourceDriftError("resource canonical path changed")

    exists = resolved.exists()
    if exists != resource_before.exists:
        raise ResourceDriftError(
            f"resource existence changed: expected {resource_before.exists}, got {exists}"
        )
    if not exists:
        return resolved
    if not resolved.is_file():
        raise ResourceDriftError("resource is no longer a regular file")

    actual_hash = _hash_regular_file(resolved)
    if actual_hash != resource_before.content_sha256:
        raise ResourceDriftError(
            f"resource content changed: expected {resource_before.content_sha256}, got {actual_hash}"
        )
    return resolved



def read_file_resource_backup_v1(
    project_root: str | os.PathLike[str],
    resource_before: FileResourceBefore,
    *,
    size_limit: int = RESOURCE_BACKUP_LIMIT,
) -> bytes | None:
    """Read exact before-bytes for a durable checkpoint under the same CAS."""
    target = validate_file_resource_cas(project_root, resource_before)
    if not resource_before.exists:
        return None
    try:
        size = target.stat().st_size
    except OSError as exc:
        raise WorkspaceError(f"cannot stat checkpoint resource: {exc}") from exc
    if size > size_limit:
        raise ResourceTooLargeError(
            f"resource is {size} bytes; P1-v1 limit is {size_limit} bytes"
        )
    try:
        data = target.read_bytes()
    except OSError as exc:
        raise WorkspaceUnstableError(
            f"resource changed while checkpoint bytes were read: {exc}"
        ) from exc
    digest = hashlib.sha256(data).hexdigest()
    if digest != resource_before.content_sha256:
        raise ResourceDriftError(
            "checkpoint bytes no longer match resource_before.content_sha256"
        )
    # Catch a mutation that raced with the read before publishing the checkpoint.
    validate_file_resource_cas(project_root, resource_before)
    return data


__all__ = [
    "IndexEntry", "RESOURCE_BACKUP_LIMIT", "ResourceBoundaryError",
    "ResourceDriftError", "ResourceTooLargeError", "UnsupportedRepoLayoutError",
    "WorkspaceDriftError", "WorkspaceError", "WorkspaceGitError",
    "WorkspaceManifest", "WorkspaceUnstableError", "WorktreeEntry",
    "assert_flow_workspace_guard", "capture_file_resource_before",
    "compute_workspace_digest_v1", "compute_workspace_manifest_once",
    "file_effect_target_fingerprint", "read_file_resource_backup_v1",
    "validate_file_resource_cas",
]
