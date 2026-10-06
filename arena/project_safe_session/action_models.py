"""Strict action identity/journal models for project-safe v1."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .schema_types import (
    ActionState,
    EffectStatus,
    ResourceType,
    RiskClass,
    SCHEMA_VERSION,
)
from .schema_utils import (
    SchemaError,
    boolean_value,
    enum_value,
    exact_keys,
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
class FileResourceBefore:
    project_fingerprint: str
    canonical_relative_path: str
    resource_type: ResourceType
    exists: bool
    content_sha256: str | None

    @classmethod
    def from_dict(cls, value: Any) -> "FileResourceBefore":
        obj = object_value(value, "resource_before")
        exact_keys(
            obj,
            {"project_fingerprint", "canonical_relative_path", "resource_type", "exists", "content_sha256"},
            "resource_before",
        )
        resource_type = enum_value(ResourceType, obj["resource_type"], "resource_before.resource_type")
        if resource_type is not ResourceType.FILE:
            raise SchemaError("file resource_before.resource_type must be 'file'")
        exists = boolean_value(obj["exists"], "resource_before.exists")
        content = sha256_value(obj["content_sha256"], "resource_before.content_sha256", optional=True)
        if exists and content is None:
            raise SchemaError("existing file resource requires content_sha256")
        if not exists and content is not None:
            raise SchemaError("non-existent file resource must have null content_sha256")
        path = string_value(obj["canonical_relative_path"], "resource_before.canonical_relative_path")
        normalized = path.replace("\\", "/")
        if normalized.startswith("/") or ".." in normalized.split("/"):
            raise SchemaError("resource_before.canonical_relative_path must be a safe relative path")
        return cls(
            project_fingerprint=sha256_value(
                obj["project_fingerprint"], "resource_before.project_fingerprint"
            ) or "",
            canonical_relative_path=normalized,
            resource_type=resource_type,
            exists=exists,
            content_sha256=content,
        )

    def to_dict(self) -> dict[str, Any]:
        return to_data(self)


@dataclass(frozen=True)
class BrowserResourceTarget:
    resource_type: ResourceType
    provider: str
    origin: str
    conversation_fingerprint: str

    @classmethod
    def from_dict(cls, value: Any) -> "BrowserResourceTarget":
        obj = object_value(value, "browser_target")
        exact_keys(obj, {"resource_type", "provider", "origin", "conversation_fingerprint"}, "browser_target")
        resource_type = enum_value(ResourceType, obj["resource_type"], "browser_target.resource_type")
        if resource_type is not ResourceType.BROWSER:
            raise SchemaError("browser_target.resource_type must be 'browser'")
        return cls(
            resource_type=resource_type,
            provider=string_value(obj["provider"], "browser_target.provider"),
            origin=string_value(obj["origin"], "browser_target.origin"),
            conversation_fingerprint=string_value(
                obj["conversation_fingerprint"], "browser_target.conversation_fingerprint"
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return to_data(self)


@dataclass(frozen=True)
class EffectEvidence:
    status: EffectStatus
    before_digest: str | None
    after_digest: str | None
    expected_after_digest: str | None

    @classmethod
    def from_dict(cls, value: Any) -> "EffectEvidence":
        obj = object_value(value, "effect")
        exact_keys(obj, {"status", "before_digest", "after_digest", "expected_after_digest"}, "effect")
        return cls(
            status=enum_value(EffectStatus, obj["status"], "effect.status"),
            before_digest=sha256_value(obj["before_digest"], "effect.before_digest", optional=True),
            after_digest=sha256_value(obj["after_digest"], "effect.after_digest", optional=True),
            expected_after_digest=sha256_value(
                obj["expected_after_digest"], "effect.expected_after_digest", optional=True
            ),
        )


@dataclass(frozen=True)
class ActionInputRef:
    args_hash: str
    payload_ref: str
    payload_sha256: str
    summary: dict[str, Any]

    @classmethod
    def from_dict(cls, value: Any) -> "ActionInputRef":
        obj = object_value(value, "input")
        exact_keys(obj, {"args_hash", "payload_ref", "payload_sha256", "summary"}, "input")
        return cls(
            args_hash=sha256_value(obj["args_hash"], "input.args_hash") or "",
            payload_ref=string_value(obj["payload_ref"], "input.payload_ref"),
            payload_sha256=sha256_value(obj["payload_sha256"], "input.payload_sha256") or "",
            summary=object_value(obj["summary"], "input.summary"),
        )


@dataclass(frozen=True)
class JournalRecord:
    schema_version: int
    journal_seq: int
    action_seq: int
    transition_seq: int
    action_id: str
    session_id: str
    flow_id: str | None
    attempt_seq: int
    attempt_id: str
    state: ActionState
    effect: EffectEvidence
    recorded_at: str
    previous_record_hash: str | None
    record_hash: str
    proposal_id: str
    proposal_digest: str
    action_type: str
    effect_target_fingerprint: str
    risk: RiskClass
    input: ActionInputRef
    workspace_digest_context: str
    reason: str | None

    @classmethod
    def from_dict(cls, value: Any) -> "JournalRecord":
        obj = object_value(value, "journal_record")
        exact_keys(
            obj,
            {
                "schema_version", "journal_seq", "action_seq", "transition_seq",
                "action_id", "session_id", "flow_id", "attempt_seq", "attempt_id",
                "state", "effect", "recorded_at", "previous_record_hash", "record_hash",
                "proposal_id", "proposal_digest", "action_type",
                "effect_target_fingerprint", "risk", "input",
                "workspace_digest_context", "reason",
            },
            "journal_record",
        )
        if integer_value(obj["schema_version"], "journal_record.schema_version", minimum=1) != SCHEMA_VERSION:
            raise SchemaError("unsupported journal_record.schema_version")
        state = enum_value(ActionState, obj["state"], "journal_record.state")
        effect = EffectEvidence.from_dict(obj["effect"])
        reason = optional_string(obj["reason"], "journal_record.reason")
        if state is ActionState.PREPARED and effect.status is not EffectStatus.NONE:
            raise SchemaError("PREPARED requires effect=NONE")
        if state is ActionState.SUCCEEDED and effect.status is not EffectStatus.EXPECTED:
            raise SchemaError("SUCCEEDED requires effect=EXPECTED")
        if state is ActionState.ABANDONED:
            if effect.status is not EffectStatus.NONE:
                raise SchemaError("ABANDONED requires effect=NONE")
            if reason not in {"WORKSPACE_DRIFT", "SUPERSEDED", "USER_CANCELLED", "GOAL_REFINED"}:
                raise SchemaError("ABANDONED requires a supported abandonment reason")
        elif reason is not None and state not in {ActionState.FAILED, ActionState.INTERRUPTED}:
            raise SchemaError("journal_record.reason is not allowed for this action state")
        return cls(
            schema_version=SCHEMA_VERSION,
            journal_seq=integer_value(obj["journal_seq"], "journal_record.journal_seq", minimum=1),
            action_seq=integer_value(obj["action_seq"], "journal_record.action_seq", minimum=1),
            transition_seq=integer_value(obj["transition_seq"], "journal_record.transition_seq", minimum=1),
            action_id=sha256_value(obj["action_id"], "journal_record.action_id") or "",
            session_id=uuid4_value(obj["session_id"], "journal_record.session_id") or "",
            flow_id=uuid4_value(obj["flow_id"], "journal_record.flow_id", optional=True),
            attempt_seq=integer_value(obj["attempt_seq"], "journal_record.attempt_seq", minimum=1),
            attempt_id=sha256_value(obj["attempt_id"], "journal_record.attempt_id") or "",
            state=state,
            effect=effect,
            recorded_at=utc_value(obj["recorded_at"], "journal_record.recorded_at"),
            previous_record_hash=sha256_value(
                obj["previous_record_hash"], "journal_record.previous_record_hash", optional=True
            ),
            record_hash=sha256_value(obj["record_hash"], "journal_record.record_hash") or "",
            proposal_id=string_value(obj["proposal_id"], "journal_record.proposal_id"),
            proposal_digest=sha256_value(obj["proposal_digest"], "journal_record.proposal_digest") or "",
            action_type=string_value(obj["action_type"], "journal_record.action_type"),
            effect_target_fingerprint=sha256_value(
                obj["effect_target_fingerprint"], "journal_record.effect_target_fingerprint"
            ) or "",
            risk=enum_value(RiskClass, obj["risk"], "journal_record.risk"),
            input=ActionInputRef.from_dict(obj["input"]),
            workspace_digest_context=sha256_value(
                obj["workspace_digest_context"], "journal_record.workspace_digest_context"
            ) or "",
            reason=reason,
        )

    def to_dict(self) -> dict[str, Any]:
        return to_data(self)


__all__ = [
    "ActionInputRef", "BrowserResourceTarget", "EffectEvidence",
    "FileResourceBefore", "JournalRecord",
]
