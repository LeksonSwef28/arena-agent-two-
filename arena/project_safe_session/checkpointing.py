"""High-level durable RESOURCE_BEFORE checkpoint capture for project-safe v1."""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone

from .checkpoint_contract import (
    CheckpointContractError,
    build_checkpoint_manifest,
    checkpoint_resource_from_file_before,
)
from .checkpoint_models import CheckpointManifest
from .paths import project_fingerprint
from .storage import ProjectSafeSessionStore
from .workspace import (
    FileResourceBefore,
    capture_file_resource_before,
    read_file_resource_backup_v1,
)


def _utc_now() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z")
    )


def create_file_resource_before_checkpoint(
    store: ProjectSafeSessionStore,
    project_root: str | os.PathLike[str],
    *,
    action_id: str,
    canonical_relative_path: str,
    workspace_digest: str,
    head_sha: str,
    checkpoint_id: str | None = None,
    created_at: str | None = None,
) -> tuple[CheckpointManifest, FileResourceBefore]:
    """Capture exact before-bytes and durably publish a RESOURCE_BEFORE checkpoint."""
    if project_fingerprint(project_root) != store.project_fingerprint:
        raise CheckpointContractError(
            "checkpoint project_root does not match the store's leased project"
        )

    resource_before = capture_file_resource_before(
        project_root,
        canonical_relative_path,
    )
    backup = read_file_resource_backup_v1(project_root, resource_before)
    checkpoint_resource = checkpoint_resource_from_file_before(
        resource_before,
        backup,
    )

    manifest = build_checkpoint_manifest(
        checkpoint_id=checkpoint_id or str(uuid.uuid4()),
        session_id=store.session_id,
        action_id=action_id,
        kind="RESOURCE_BEFORE",
        created_at=created_at or _utc_now(),
        workspace_digest=workspace_digest,
        head_sha=head_sha,
        resources=[checkpoint_resource],
    )
    backups = (
        {checkpoint_resource.backup_ref: backup}
        if checkpoint_resource.backup_ref is not None and backup is not None
        else {}
    )
    store.write_checkpoint(manifest, backups)
    return manifest, resource_before


__all__ = ["create_file_resource_before_checkpoint"]
