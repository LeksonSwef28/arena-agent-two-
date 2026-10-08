"""Opt-in v2 wire models; persistence and admission still use v1 parsers."""
from __future__ import annotations

from dataclasses import dataclass, fields
from typing import Any

from .action_models import JournalRecord
from .checkpoint_models import CheckpointManifest
from .schema_types import ActionState, CheckpointKind
from .schema_utils import (
    SchemaError,
    exact_keys,
    integer_value,
    object_value,
    sha256_value,
    to_data,
    uuid4_value,
)


@dataclass(frozen=True)
class EventRecordRef:
    event_seq: int
    record_hash: str

    @classmethod
    def from_dict(cls, value: Any) -> EventRecordRef:
        obj = object_value(value, "event_ref")
        exact_keys(obj, {"event_seq", "record_hash"}, "event_ref")
        return cls(integer_value(obj["event_seq"], "event_ref.event_seq", minimum=1),
                   sha256_value(obj["record_hash"], "event_ref.record_hash") or "")

    def to_dict(self) -> dict[str, Any]:
        return to_data(self)


@dataclass(frozen=True)
class ActionRecordRef:
    journal_seq: int
    record_hash: str

    @classmethod
    def from_dict(cls, value: Any) -> ActionRecordRef:
        obj = object_value(value, "action_ref")
        exact_keys(obj, {"journal_seq", "record_hash"}, "action_ref")
        return cls(integer_value(obj["journal_seq"], "action_ref.journal_seq", minimum=1),
                   sha256_value(obj["record_hash"], "action_ref.record_hash") or "")

    def to_dict(self) -> dict[str, Any]:
        return to_data(self)


@dataclass(frozen=True)
class WorkspaceVerificationRef:
    checkpoint_id: str
    manifest_sha256: str

    @classmethod
    def from_dict(cls, value: Any) -> WorkspaceVerificationRef:
        obj = object_value(value, "workspace_verification")
        exact_keys(obj, {"checkpoint_id", "manifest_sha256"}, "workspace_verification")
        return cls(uuid4_value(obj["checkpoint_id"], "workspace_verification.checkpoint_id") or "",
                   sha256_value(obj["manifest_sha256"], "workspace_verification.manifest_sha256") or "")

    def to_dict(self) -> dict[str, Any]:
        return to_data(self)


def _v2_envelope(value: Any, model: type, additions: set[str], label: str) -> dict[str, Any]:
    """Validate the v2 envelope before reusing the strict v1 common-field parser."""
    obj = object_value(value, label)
    common = {field.name for field in fields(model)}
    exact_keys(obj, common | additions, label)
    if integer_value(obj["schema_version"], f"{label}.schema_version", minimum=1) != 2:
        raise SchemaError(f"unsupported {label}.schema_version")
    return obj


@dataclass(frozen=True)
class JournalRecordV2(JournalRecord):
    flow_creation_ref: EventRecordRef
    prepared_event_ref: EventRecordRef
    preceding_success_ref: ActionRecordRef | None
    workspace_verification: WorkspaceVerificationRef | None

    @classmethod
    def from_dict(cls, value: Any) -> JournalRecordV2:
        additions = {"flow_creation_ref", "prepared_event_ref", "preceding_success_ref",
                     "workspace_verification"}
        obj = _v2_envelope(value, JournalRecord, additions, "journal_record_v2")
        common = {key: val for key, val in obj.items() if key not in additions}
        common["schema_version"] = 1
        base = JournalRecord.from_dict(common)
        if base.flow_id is None:
            raise SchemaError("journal_record_v2 requires flow_id")
        creation = EventRecordRef.from_dict(obj["flow_creation_ref"])
        prepared = EventRecordRef.from_dict(obj["prepared_event_ref"])
        if creation.event_seq > prepared.event_seq:
            raise SchemaError("flow creation reference must be within prepared event prefix")
        if creation.event_seq == prepared.event_seq and creation.record_hash != prepared.record_hash:
            raise SchemaError("same event sequence requires the same record hash")
        predecessor = (None if obj["preceding_success_ref"] is None
                       else ActionRecordRef.from_dict(obj["preceding_success_ref"]))
        if predecessor is not None and predecessor.journal_seq >= base.journal_seq:
            raise SchemaError("preceding success reference must be strictly prior")
        verification = (None if obj["workspace_verification"] is None
                        else WorkspaceVerificationRef.from_dict(obj["workspace_verification"]))
        if (base.state is ActionState.SUCCEEDED) != (verification is not None):
            raise SchemaError("workspace verification is required exactly for SUCCEEDED")
        return cls(**{field.name: getattr(base, field.name) for field in fields(JournalRecord)
                      if field.name != "schema_version"}, schema_version=2,
                   flow_creation_ref=creation, prepared_event_ref=prepared,
                   preceding_success_ref=predecessor, workspace_verification=verification)


@dataclass(frozen=True)
class CheckpointManifestV2(CheckpointManifest):
    attempt_id: str | None

    @classmethod
    def from_dict(cls, value: Any) -> CheckpointManifestV2:
        obj = _v2_envelope(value, CheckpointManifest, {"attempt_id"}, "checkpoint_v2")
        common = {key: val for key, val in obj.items() if key != "attempt_id"}
        common["schema_version"] = 1
        base = CheckpointManifest.from_dict(common)
        attempt = sha256_value(obj["attempt_id"], "checkpoint_v2.attempt_id", optional=True)
        if (base.kind is CheckpointKind.SESSION_BASELINE) != (attempt is None):
            raise SchemaError("attempt_id must be null only for SESSION_BASELINE")
        return cls(**{field.name: getattr(base, field.name) for field in fields(CheckpointManifest)
                      if field.name != "schema_version"}, schema_version=2, attempt_id=attempt)


__all__ = [
    "ActionRecordRef", "CheckpointManifestV2", "EventRecordRef", "JournalRecordV2",
    "WorkspaceVerificationRef",
]
