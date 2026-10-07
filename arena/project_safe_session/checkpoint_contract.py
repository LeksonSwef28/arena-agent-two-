"""Integrity contract and builders for project-safe v1 checkpoints."""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from .action_models import FileResourceBefore
from .canonical import canonical_sha256
from .checkpoint_models import CheckpointManifest, CheckpointResource
from .schema_types import SCHEMA_VERSION, CheckpointKind
from .schema_utils import (
    SchemaError,
    git_sha_value,
    sha256_value,
    utc_value,
    uuid4_value,
)


class CheckpointContractError(ValueError):
    """Checkpoint metadata or backup bytes violate the frozen v1 contract."""


def checkpoint_manifest_sha256(value: CheckpointManifest | Mapping[str, Any]) -> str:
    raw = value.to_dict() if isinstance(value, CheckpointManifest) else dict(value)
    without_self = dict(raw)
    without_self.pop("manifest_sha256", None)
    return canonical_sha256(without_self)


def validate_checkpoint_manifest_digest(manifest: CheckpointManifest) -> None:
    expected = checkpoint_manifest_sha256(manifest)
    if manifest.manifest_sha256 != expected:
        raise CheckpointContractError(
            f"checkpoint manifest digest mismatch: expected {expected}, "
            f"got {manifest.manifest_sha256}"
        )


def checkpoint_resource_from_file_before(
    resource_before: FileResourceBefore,
    backup_bytes: bytes | None,
) -> CheckpointResource:
    if resource_before.exists:
        if not isinstance(backup_bytes, bytes):
            raise CheckpointContractError("existing file resource requires exact backup bytes")
        digest = canonical_file_bytes_sha256(backup_bytes)
        if digest != resource_before.content_sha256:
            raise CheckpointContractError(
                "backup bytes do not match resource_before.content_sha256"
            )
        return CheckpointResource.from_dict(
            {
                "canonical_relative_path": resource_before.canonical_relative_path,
                "existed": True,
                "size": len(backup_bytes),
                "content_sha256": digest,
                "backup_ref": f"files/{digest}.bin",
                "backup_sha256": digest,
            }
        )

    if backup_bytes is not None:
        raise CheckpointContractError("absent file resource must not have backup bytes")
    return CheckpointResource.from_dict(
        {
            "canonical_relative_path": resource_before.canonical_relative_path,
            "existed": False,
            "size": 0,
            "content_sha256": None,
            "backup_ref": None,
            "backup_sha256": None,
        }
    )


def canonical_file_bytes_sha256(data: bytes) -> str:
    import hashlib

    if not isinstance(data, bytes):
        raise CheckpointContractError("checkpoint backup payload must be bytes")
    return hashlib.sha256(data).hexdigest()


def build_checkpoint_manifest(
    *,
    checkpoint_id: str,
    session_id: str,
    action_id: str | None,
    kind: CheckpointKind | str,
    created_at: str,
    workspace_digest: str,
    head_sha: str,
    resources: Iterable[CheckpointResource],
) -> CheckpointManifest:
    checked_checkpoint = uuid4_value(checkpoint_id, "checkpoint_id")
    checked_session = uuid4_value(session_id, "session_id")
    checked_action = sha256_value(action_id, "action_id", optional=True)
    checked_workspace = sha256_value(workspace_digest, "workspace_digest")
    checked_head = git_sha_value(head_sha, "head_sha")
    checked_created = utc_value(created_at, "created_at")
    assert checked_checkpoint and checked_session and checked_workspace and checked_head

    if isinstance(kind, str):
        try:
            checked_kind = CheckpointKind(kind)
        except ValueError as exc:
            raise CheckpointContractError(f"unsupported checkpoint kind: {kind!r}") from exc
    else:
        checked_kind = kind

    resource_list = list(resources)
    raw = {
        "schema_version": SCHEMA_VERSION,
        "checkpoint_id": checked_checkpoint,
        "session_id": checked_session,
        "action_id": checked_action,
        "kind": checked_kind.value,
        "created_at": checked_created,
        "workspace_digest": checked_workspace,
        "head_sha": checked_head,
        "resources": [item.to_dict() for item in resource_list],
        "manifest_sha256": "0" * 64,
    }
    raw["manifest_sha256"] = checkpoint_manifest_sha256(raw)
    try:
        manifest = CheckpointManifest.from_dict(raw)
    except SchemaError as exc:
        raise CheckpointContractError(str(exc)) from exc
    validate_checkpoint_manifest_digest(manifest)
    return manifest


__all__ = [
    "CheckpointContractError", "build_checkpoint_manifest",
    "canonical_file_bytes_sha256", "checkpoint_manifest_sha256",
    "checkpoint_resource_from_file_before", "validate_checkpoint_manifest_digest",
]
