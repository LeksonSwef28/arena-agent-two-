"""Strict checkpoint manifest models for project-safe v1."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .schema_types import CheckpointKind, SCHEMA_VERSION
from .schema_utils import (
    SchemaError,
    boolean_value,
    enum_value,
    exact_keys,
    git_sha_value,
    integer_value,
    object_value,
    optional_string,
    sha256_value,
    string_value,
    to_data,
    utc_value,
    uuid4_value,
)


@dataclass(frozen=True)
class CheckpointResource:
    canonical_relative_path: str
    existed: bool
    size: int
    content_sha256: str | None
    backup_ref: str | None
    backup_sha256: str | None

    @classmethod
    def from_dict(cls, value: Any) -> "CheckpointResource":
        obj = object_value(value, "checkpoint_resource")
        exact_keys(
            obj,
            {"canonical_relative_path", "existed", "size", "content_sha256", "backup_ref", "backup_sha256"},
            "checkpoint_resource",
        )
        existed = boolean_value(obj["existed"], "checkpoint_resource.existed")
        size = integer_value(obj["size"], "checkpoint_resource.size")
        content = sha256_value(obj["content_sha256"], "checkpoint_resource.content_sha256", optional=True)
        backup_ref = optional_string(obj["backup_ref"], "checkpoint_resource.backup_ref")
        backup_sha = sha256_value(obj["backup_sha256"], "checkpoint_resource.backup_sha256", optional=True)
        if existed and (content is None or backup_ref is None or backup_sha is None):
            raise SchemaError("existing checkpoint resource requires content and backup integrity fields")
        if not existed and any(item is not None for item in (content, backup_ref, backup_sha)):
            raise SchemaError("non-existent checkpoint resource must not contain backup fields")
        if not existed and size != 0:
            raise SchemaError("non-existent checkpoint resource size must be zero")
        if existed and content != backup_sha:
            raise SchemaError("checkpoint backup_sha256 must equal original content_sha256 in v1")
        return cls(
            canonical_relative_path=string_value(
                obj["canonical_relative_path"], "checkpoint_resource.canonical_relative_path"
            ).replace("\\", "/"),
            existed=existed,
            size=size,
            content_sha256=content,
            backup_ref=backup_ref,
            backup_sha256=backup_sha,
        )


@dataclass(frozen=True)
class CheckpointManifest:
    schema_version: int
    checkpoint_id: str
    session_id: str
    action_id: str | None
    kind: CheckpointKind
    created_at: str
    workspace_digest: str
    head_sha: str
    resources: tuple[CheckpointResource, ...]
    manifest_sha256: str

    @classmethod
    def from_dict(cls, value: Any) -> "CheckpointManifest":
        obj = object_value(value, "checkpoint")
        exact_keys(
            obj,
            {
                "schema_version", "checkpoint_id", "session_id", "action_id", "kind",
                "created_at", "workspace_digest", "head_sha", "resources", "manifest_sha256",
            },
            "checkpoint",
        )
        if integer_value(obj["schema_version"], "checkpoint.schema_version", minimum=1) != SCHEMA_VERSION:
            raise SchemaError("unsupported checkpoint.schema_version")
        raw_resources = obj["resources"]
        if not isinstance(raw_resources, list):
            raise SchemaError("checkpoint.resources must be an array")
        resources = tuple(CheckpointResource.from_dict(item) for item in raw_resources)
        paths = [item.canonical_relative_path for item in resources]
        if len(paths) != len(set(paths)):
            raise SchemaError("checkpoint.resources contains duplicate paths")
        return cls(
            schema_version=SCHEMA_VERSION,
            checkpoint_id=uuid4_value(obj["checkpoint_id"], "checkpoint.checkpoint_id") or "",
            session_id=uuid4_value(obj["session_id"], "checkpoint.session_id") or "",
            action_id=sha256_value(obj["action_id"], "checkpoint.action_id", optional=True),
            kind=enum_value(CheckpointKind, obj["kind"], "checkpoint.kind"),
            created_at=utc_value(obj["created_at"], "checkpoint.created_at"),
            workspace_digest=sha256_value(obj["workspace_digest"], "checkpoint.workspace_digest") or "",
            head_sha=git_sha_value(obj["head_sha"], "checkpoint.head_sha") or "",
            resources=resources,
            manifest_sha256=sha256_value(obj["manifest_sha256"], "checkpoint.manifest_sha256") or "",
        )

    def to_dict(self) -> dict[str, Any]:
        return to_data(self)


__all__ = ["CheckpointManifest", "CheckpointResource"]
