"""Strict stdlib-only schemas for project-safe durable sessions.

P1-A2 deliberately contains no filesystem, Git, browser, or journal I/O.
The models validate exact JSON shapes so later persistence code cannot silently
accept missing/extra fields or bool-as-int values.
"""
from __future__ import annotations

import re
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Mapping, TypeVar

SCHEMA_VERSION = 1

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_GIT_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$")


class SchemaError(ValueError):
    """Persisted/project-safe data does not match the frozen v1 contract."""


class _StrEnum(str, Enum):
    pass


class RequestedMode(_StrEnum):
    READ = "read"
    WRITE = "write"


class LifecycleStatus(_StrEnum):
    CREATED = "CREATED"
    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"
    WAITING = "WAITING"
    COMPLETED = "COMPLETED"
    ABORTED = "ABORTED"


class LifecyclePhase(_StrEnum):
    IDLE = "IDLE"
    PLANNING = "PLANNING"
    REVIEWING = "REVIEWING"
    EXECUTING = "EXECUTING"
    VERIFYING = "VERIFYING"
    RECOVERY = "RECOVERY"


class LifecycleReason(_StrEnum):
    NEED_USER = "NEED_USER"
    NEED_REBIND = "NEED_REBIND"
    RECOVERY_REQUIRED = "RECOVERY_REQUIRED"
    WORKSPACE_DRIFT = "WORKSPACE_DRIFT"
    WORKSPACE_DIRTY = "WORKSPACE_DIRTY"
    RATE_LIMIT = "RATE_LIMIT"
    QUOTA = "QUOTA"
    CONTEXT_EXHAUSTED = "CONTEXT_EXHAUSTED"
    POLICY_DENIED = "POLICY_DENIED"
    RESOURCE_TOO_LARGE = "RESOURCE_TOO_LARGE"
    UNSUPPORTED_REPO_LAYOUT = "UNSUPPORTED_REPO_LAYOUT"


class BindingStatus(_StrEnum):
    UNBOUND = "UNBOUND"
    BOUND = "BOUND"
    STALE = "STALE"


class BrowserRole(_StrEnum):
    MAIN_GPT = "MAIN_GPT"
    DEEPSEEK_REVIEW = "DEEPSEEK_REVIEW"
    QWEN_REVIEW = "QWEN_REVIEW"


class ActionState(_StrEnum):
    PREPARED = "PREPARED"
    EXECUTING = "EXECUTING"
    VERIFYING = "VERIFYING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    INTERRUPTED = "INTERRUPTED"
    ABANDONED = "ABANDONED"


class EffectStatus(_StrEnum):
    NONE = "NONE"
    EXPECTED = "EXPECTED"
    PARTIAL = "PARTIAL"
    UNKNOWN = "UNKNOWN"


class CheckpointKind(_StrEnum):
    SESSION_BASELINE = "SESSION_BASELINE"
    RESOURCE_BEFORE = "RESOURCE_BEFORE"
    RESOURCE_AFTER = "RESOURCE_AFTER"


class ResourceType(_StrEnum):
    FILE = "file"
    BROWSER = "browser"


class RiskClass(_StrEnum):
    SAFE = "safe"
    MEDIUM = "medium"
    DANGEROUS = "dangerous"
    UNKNOWN = "unknown"


TEnum = TypeVar("TEnum", bound=Enum)


def _obj(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise SchemaError(f"{label} must be an object")
    return dict(value)


def _exact(obj: Mapping[str, Any], expected: set[str], label: str) -> None:
    keys = set(obj)
    if keys != expected:
        missing = sorted(expected - keys)
        extra = sorted(keys - expected)
        raise SchemaError(f"{label} keys mismatch; missing={missing}, extra={extra}")


def _str(value: Any, label: str, *, empty: bool = False) -> str:
    if not isinstance(value, str) or (not empty and not value):
        raise SchemaError(f"{label} must be a {'string' if empty else 'non-empty string'}")
    return value


def _opt_str(value: Any, label: str) -> str | None:
    if value is None:
        return None
    return _str(value, label)


def _int(value: Any, label: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise SchemaError(f"{label} must be an integer >= {minimum}")
    return value


def _bool(value: Any, label: str) -> bool:
    if not isinstance(value, bool):
        raise SchemaError(f"{label} must be a boolean")
    return value


def _enum(enum_type: type[TEnum], value: Any, label: str) -> TEnum:
    if not isinstance(value, str):
        raise SchemaError(f"{label} must be a string enum")
    try:
        return enum_type(value)
    except ValueError as exc:
        raise SchemaError(f"unsupported {label}: {value!r}") from exc


def _sha256(value: Any, label: str, *, optional: bool = False) -> str | None:
    if value is None and optional:
        return None
    text = _str(value, label)
    if _SHA256_RE.fullmatch(text) is None:
        raise SchemaError(f"{label} must be 64 lowercase hex characters")
    return text


def _git_sha(value: Any, label: str, *, optional: bool = False) -> str | None:
    if value is None and optional:
        return None
    text = _str(value, label)
    if _GIT_SHA_RE.fullmatch(text) is None:
        raise SchemaError(f"{label} must be 40 lowercase hex characters")
    return text


def _uuid4(value: Any, label: str, *, optional: bool = False) -> str | None:
    if value is None and optional:
        return None
    text = _str(value, label)
    try:
        parsed = uuid.UUID(text)
    except (ValueError, AttributeError) as exc:
        raise SchemaError(f"{label} must be a UUID v4") from exc
    if parsed.version != 4 or str(parsed) != text:
        raise SchemaError(f"{label} must be canonical lowercase UUID v4")
    return text


def _utc(value: Any, label: str) -> str:
    text = _str(value, label)
    if _UTC_RE.fullmatch(text) is None:
        raise SchemaError(f"{label} must be UTC ISO-8601 ending in Z")
    try:
        datetime.fromisoformat(text[:-1] + "+00:00")
    except ValueError as exc:
        raise SchemaError(f"{label} is not a valid UTC timestamp") from exc
    return text


def _to_data(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if hasattr(value, "__dataclass_fields__"):
        return {key: _to_data(val) for key, val in asdict(value).items()}
    if isinstance(value, dict):
        return {str(key): _to_data(val) for key, val in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_data(item) for item in value]
    return value


@dataclass(frozen=True)
class ProjectState:
    canonical_root: str
    requested_mode: RequestedMode

    @classmethod
    def from_dict(cls, value: Any) -> "ProjectState":
        obj = _obj(value, "project")
        _exact(obj, {"canonical_root", "requested_mode"}, "project")
        return cls(
            canonical_root=_str(obj["canonical_root"], "project.canonical_root"),
            requested_mode=_enum(RequestedMode, obj["requested_mode"], "project.requested_mode"),
        )


@dataclass(frozen=True)
class GoalState:
    initial: str
    current: str
    revision: int
    last_changed_at: str

    @classmethod
    def from_dict(cls, value: Any) -> "GoalState":
        obj = _obj(value, "goal")
        _exact(obj, {"initial", "current", "revision", "last_changed_at"}, "goal")
        return cls(
            initial=_str(obj["initial"], "goal.initial"),
            current=_str(obj["current"], "goal.current"),
            revision=_int(obj["revision"], "goal.revision", minimum=1),
            last_changed_at=_utc(obj["last_changed_at"], "goal.last_changed_at"),
        )


@dataclass(frozen=True)
class LifecycleState:
    status: LifecycleStatus
    phase: LifecyclePhase
    reason: LifecycleReason | None
    changed_at: str

    @classmethod
    def from_dict(cls, value: Any) -> "LifecycleState":
        obj = _obj(value, "lifecycle")
        _exact(obj, {"status", "phase", "reason", "changed_at"}, "lifecycle")
        status = _enum(LifecycleStatus, obj["status"], "lifecycle.status")
        phase = _enum(LifecyclePhase, obj["phase"], "lifecycle.phase")
        reason_raw = obj["reason"]
        reason = None if reason_raw is None else _enum(LifecycleReason, reason_raw, "lifecycle.reason")
        if status is LifecycleStatus.WAITING and reason is None:
            raise SchemaError("lifecycle.reason is required when status=WAITING")
        if status is not LifecycleStatus.WAITING and reason is not None:
            raise SchemaError("lifecycle.reason must be null unless status=WAITING")
        if phase is LifecyclePhase.RECOVERY and status is not LifecycleStatus.WAITING:
            raise SchemaError("RECOVERY phase requires WAITING status")
        return cls(status=status, phase=phase, reason=reason, changed_at=_utc(obj["changed_at"], "lifecycle.changed_at"))


@dataclass(frozen=True)
class WorkspaceBaseline:
    checkpoint_id: str
    head_sha: str
    workspace_digest: str
    clean: bool
    captured_at: str

    @classmethod
    def from_dict(cls, value: Any) -> "WorkspaceBaseline":
        obj = _obj(value, "workspace.session_baseline")
        _exact(obj, {"checkpoint_id", "head_sha", "workspace_digest", "clean", "captured_at"}, "workspace.session_baseline")
        return cls(
            checkpoint_id=_uuid4(obj["checkpoint_id"], "workspace.session_baseline.checkpoint_id") or "",
            head_sha=_git_sha(obj["head_sha"], "workspace.session_baseline.head_sha") or "",
            workspace_digest=_sha256(obj["workspace_digest"], "workspace.session_baseline.workspace_digest") or "",
            clean=_bool(obj["clean"], "workspace.session_baseline.clean"),
            captured_at=_utc(obj["captured_at"], "workspace.session_baseline.captured_at"),
        )


@dataclass(frozen=True)
class WorkspaceState:
    kind: str
    session_baseline: WorkspaceBaseline
    workspace_digest_last_verified: str
    last_verified_checkpoint_id: str
    last_verified_head_sha: str
    last_verified_at: str

    @classmethod
    def from_dict(cls, value: Any) -> "WorkspaceState":
        obj = _obj(value, "workspace")
        _exact(
            obj,
            {
                "kind", "session_baseline", "workspace_digest_last_verified",
                "last_verified_checkpoint_id", "last_verified_head_sha", "last_verified_at",
            },
            "workspace",
        )
        kind = _str(obj["kind"], "workspace.kind")
        if kind != "git_worktree":
            raise SchemaError("workspace.kind must be 'git_worktree'")
        return cls(
            kind=kind,
            session_baseline=WorkspaceBaseline.from_dict(obj["session_baseline"]),
            workspace_digest_last_verified=_sha256(
                obj["workspace_digest_last_verified"], "workspace.workspace_digest_last_verified"
            ) or "",
            last_verified_checkpoint_id=_uuid4(
                obj["last_verified_checkpoint_id"], "workspace.last_verified_checkpoint_id"
            ) or "",
            last_verified_head_sha=_git_sha(
                obj["last_verified_head_sha"], "workspace.last_verified_head_sha"
            ) or "",
            last_verified_at=_utc(obj["last_verified_at"], "workspace.last_verified_at"),
        )


@dataclass(frozen=True)
class BrowserBinding:
    provider: str
    status: BindingStatus
    binding_revision: int
    browser_instance_id: str
    tab_id: str
    origin: str
    conversation_url: str
    conversation_fingerprint: str
    bound_at: str
    last_verified_at: str
    stale_reason: str | None

    @classmethod
    def from_dict(cls, value: Any, label: str = "binding") -> "BrowserBinding":
        obj = _obj(value, label)
        _exact(
            obj,
            {
                "provider", "status", "binding_revision", "browser_instance_id", "tab_id",
                "origin", "conversation_url", "conversation_fingerprint", "bound_at",
                "last_verified_at", "stale_reason",
            },
            label,
        )
        status = _enum(BindingStatus, obj["status"], f"{label}.status")
        stale_reason = _opt_str(obj["stale_reason"], f"{label}.stale_reason")
        if status is BindingStatus.STALE and stale_reason is None:
            raise SchemaError(f"{label}.stale_reason is required when status=STALE")
        if status is not BindingStatus.STALE and stale_reason is not None:
            raise SchemaError(f"{label}.stale_reason must be null unless status=STALE")
        return cls(
            provider=_str(obj["provider"], f"{label}.provider"),
            status=status,
            binding_revision=_int(obj["binding_revision"], f"{label}.binding_revision", minimum=1),
            browser_instance_id=_uuid4(obj["browser_instance_id"], f"{label}.browser_instance_id") or "",
            tab_id=_str(obj["tab_id"], f"{label}.tab_id"),
            origin=_str(obj["origin"], f"{label}.origin"),
            conversation_url=_str(obj["conversation_url"], f"{label}.conversation_url"),
            conversation_fingerprint=_str(
                obj["conversation_fingerprint"], f"{label}.conversation_fingerprint"
            ),
            bound_at=_utc(obj["bound_at"], f"{label}.bound_at"),
            last_verified_at=_utc(obj["last_verified_at"], f"{label}.last_verified_at"),
            stale_reason=stale_reason,
        )


@dataclass(frozen=True)
class BrowserState:
    required_roles: tuple[BrowserRole, ...]
    bindings: dict[BrowserRole, BrowserBinding | None]

    @classmethod
    def from_dict(cls, value: Any) -> "BrowserState":
        obj = _obj(value, "browser")
        _exact(obj, {"required_roles", "bindings"}, "browser")
        raw_roles = obj["required_roles"]
        if not isinstance(raw_roles, list):
            raise SchemaError("browser.required_roles must be an array")
        roles = tuple(_enum(BrowserRole, item, "browser.required_roles[]") for item in raw_roles)
        if len(set(roles)) != len(roles):
            raise SchemaError("browser.required_roles contains duplicates")
        raw_bindings = _obj(obj["bindings"], "browser.bindings")
        valid_keys = {role.value for role in BrowserRole}
        if not set(raw_bindings).issubset(valid_keys):
            raise SchemaError("browser.bindings contains unsupported roles")
        bindings: dict[BrowserRole, BrowserBinding | None] = {}
        for key, raw in raw_bindings.items():
            role = BrowserRole(key)
            bindings[role] = None if raw is None else BrowserBinding.from_dict(raw, f"browser.bindings.{key}")
        for role in roles:
            if role not in bindings:
                raise SchemaError(f"required browser role {role.value} is missing from bindings")
        return cls(required_roles=roles, bindings=bindings)


@dataclass(frozen=True)
class ExecutionState:
    active_flow_id: str | None
    pending_action_id: str | None
    last_terminal_action_id: str | None
    last_action_seq: int
    last_event_seq: int

    @classmethod
    def from_dict(cls, value: Any) -> "ExecutionState":
        obj = _obj(value, "execution")
        _exact(
            obj,
            {"active_flow_id", "pending_action_id", "last_terminal_action_id", "last_action_seq", "last_event_seq"},
            "execution",
        )
        return cls(
            active_flow_id=_uuid4(obj["active_flow_id"], "execution.active_flow_id", optional=True),
            pending_action_id=_sha256(obj["pending_action_id"], "execution.pending_action_id", optional=True),
            last_terminal_action_id=_sha256(
                obj["last_terminal_action_id"], "execution.last_terminal_action_id", optional=True
            ),
            last_action_seq=_int(obj["last_action_seq"], "execution.last_action_seq"),
            last_event_seq=_int(obj["last_event_seq"], "execution.last_event_seq"),
        )


@dataclass(frozen=True)
class RecoveryState:
    recovery_id: str
    reason: str
    interrupted_action_id: str
    detected_at: str
    workspace_checkpoint_id: str

    @classmethod
    def from_dict(cls, value: Any) -> "RecoveryState":
        obj = _obj(value, "recovery")
        _exact(
            obj,
            {"recovery_id", "reason", "interrupted_action_id", "detected_at", "workspace_checkpoint_id"},
            "recovery",
        )
        reason = _str(obj["reason"], "recovery.reason")
        if reason not in {
            "INTERRUPTED_ACTION", "JOURNAL_CORRUPT", "TRUNCATED_LAST_RECORD",
            "PAYLOAD_INTEGRITY_FAILURE", "WORKSPACE_DRIFT", "LEASE_AMBIGUOUS",
        }:
            raise SchemaError(f"unsupported recovery.reason: {reason!r}")
        return cls(
            recovery_id=_uuid4(obj["recovery_id"], "recovery.recovery_id") or "",
            reason=reason,
            interrupted_action_id=_sha256(obj["interrupted_action_id"], "recovery.interrupted_action_id") or "",
            detected_at=_utc(obj["detected_at"], "recovery.detected_at"),
            workspace_checkpoint_id=_uuid4(obj["workspace_checkpoint_id"], "recovery.workspace_checkpoint_id") or "",
        )


@dataclass(frozen=True)
class LimitsState:
    max_actions: int
    max_messages_per_role: int
    max_active_minutes: int

    @classmethod
    def from_dict(cls, value: Any) -> "LimitsState":
        obj = _obj(value, "limits")
        _exact(obj, {"max_actions", "max_messages_per_role", "max_active_minutes"}, "limits")
        return cls(
            max_actions=_int(obj["max_actions"], "limits.max_actions", minimum=1),
            max_messages_per_role=_int(obj["max_messages_per_role"], "limits.max_messages_per_role", minimum=1),
            max_active_minutes=_int(obj["max_active_minutes"], "limits.max_active_minutes", minimum=1),
        )


@dataclass(frozen=True)
class StateSnapshot:
    schema_version: int
    state_revision: int
    session_id: str
    session_fingerprint: str
    parent_session_id: str | None
    project_fingerprint: str
    project: ProjectState
    goal: GoalState
    lifecycle: LifecycleState
    workspace: WorkspaceState
    browser: BrowserState
    execution: ExecutionState
    recovery: RecoveryState | None
    limits: LimitsState
    created_at: str
    updated_at: str

    @classmethod
    def from_dict(cls, value: Any) -> "StateSnapshot":
        obj = _obj(value, "state")
        _exact(
            obj,
            {
                "schema_version", "state_revision", "session_id", "session_fingerprint",
                "parent_session_id", "project_fingerprint", "project", "goal",
                "lifecycle", "workspace", "browser", "execution", "recovery",
                "limits", "created_at", "updated_at",
            },
            "state",
        )
        if _int(obj["schema_version"], "state.schema_version", minimum=1) != SCHEMA_VERSION:
            raise SchemaError("unsupported state.schema_version")
        lifecycle = LifecycleState.from_dict(obj["lifecycle"])
        recovery = None if obj["recovery"] is None else RecoveryState.from_dict(obj["recovery"])
        if lifecycle.phase is LifecyclePhase.RECOVERY and recovery is None:
            raise SchemaError("recovery object is required during RECOVERY phase")
        if lifecycle.phase is not LifecyclePhase.RECOVERY and recovery is not None:
            raise SchemaError("recovery object must be null outside RECOVERY phase")
        return cls(
            schema_version=SCHEMA_VERSION,
            state_revision=_int(obj["state_revision"], "state.state_revision", minimum=1),
            session_id=_uuid4(obj["session_id"], "state.session_id") or "",
            session_fingerprint=_sha256(obj["session_fingerprint"], "state.session_fingerprint") or "",
            parent_session_id=_uuid4(obj["parent_session_id"], "state.parent_session_id", optional=True),
            project_fingerprint=_sha256(obj["project_fingerprint"], "state.project_fingerprint") or "",
            project=ProjectState.from_dict(obj["project"]),
            goal=GoalState.from_dict(obj["goal"]),
            lifecycle=lifecycle,
            workspace=WorkspaceState.from_dict(obj["workspace"]),
            browser=BrowserState.from_dict(obj["browser"]),
            execution=ExecutionState.from_dict(obj["execution"]),
            recovery=recovery,
            limits=LimitsState.from_dict(obj["limits"]),
            created_at=_utc(obj["created_at"], "state.created_at"),
            updated_at=_utc(obj["updated_at"], "state.updated_at"),
        )

    def to_dict(self) -> dict[str, Any]:
        return _to_data(self)


@dataclass(frozen=True)
class FileResourceBefore:
    project_fingerprint: str
    canonical_relative_path: str
    resource_type: ResourceType
    exists: bool
    content_sha256: str | None

    @classmethod
    def from_dict(cls, value: Any) -> "FileResourceBefore":
        obj = _obj(value, "resource_before")
        _exact(
            obj,
            {"project_fingerprint", "canonical_relative_path", "resource_type", "exists", "content_sha256"},
            "resource_before",
        )
        resource_type = _enum(ResourceType, obj["resource_type"], "resource_before.resource_type")
        if resource_type is not ResourceType.FILE:
            raise SchemaError("file resource_before.resource_type must be 'file'")
        exists = _bool(obj["exists"], "resource_before.exists")
        content = _sha256(obj["content_sha256"], "resource_before.content_sha256", optional=True)
        if exists and content is None:
            raise SchemaError("existing file resource requires content_sha256")
        if not exists and content is not None:
            raise SchemaError("non-existent file resource must have null content_sha256")
        path = _str(obj["canonical_relative_path"], "resource_before.canonical_relative_path")
        if path.startswith(("/", "\\")) or ".." in path.replace("\\", "/").split("/"):
            raise SchemaError("resource_before.canonical_relative_path must be a safe relative path")
        return cls(
            project_fingerprint=_sha256(obj["project_fingerprint"], "resource_before.project_fingerprint") or "",
            canonical_relative_path=path.replace("\\", "/"),
            resource_type=resource_type,
            exists=exists,
            content_sha256=content,
        )

    def to_dict(self) -> dict[str, Any]:
        return _to_data(self)


@dataclass(frozen=True)
class BrowserResourceTarget:
    resource_type: ResourceType
    provider: str
    origin: str
    conversation_fingerprint: str

    @classmethod
    def from_dict(cls, value: Any) -> "BrowserResourceTarget":
        obj = _obj(value, "browser_target")
        _exact(obj, {"resource_type", "provider", "origin", "conversation_fingerprint"}, "browser_target")
        resource_type = _enum(ResourceType, obj["resource_type"], "browser_target.resource_type")
        if resource_type is not ResourceType.BROWSER:
            raise SchemaError("browser_target.resource_type must be 'browser'")
        return cls(
            resource_type=resource_type,
            provider=_str(obj["provider"], "browser_target.provider"),
            origin=_str(obj["origin"], "browser_target.origin"),
            conversation_fingerprint=_str(
                obj["conversation_fingerprint"], "browser_target.conversation_fingerprint"
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return _to_data(self)


@dataclass(frozen=True)
class EffectEvidence:
    status: EffectStatus
    before_digest: str | None
    after_digest: str | None
    expected_after_digest: str | None

    @classmethod
    def from_dict(cls, value: Any) -> "EffectEvidence":
        obj = _obj(value, "effect")
        _exact(obj, {"status", "before_digest", "after_digest", "expected_after_digest"}, "effect")
        return cls(
            status=_enum(EffectStatus, obj["status"], "effect.status"),
            before_digest=_sha256(obj["before_digest"], "effect.before_digest", optional=True),
            after_digest=_sha256(obj["after_digest"], "effect.after_digest", optional=True),
            expected_after_digest=_sha256(
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
        obj = _obj(value, "input")
        _exact(obj, {"args_hash", "payload_ref", "payload_sha256", "summary"}, "input")
        summary = _obj(obj["summary"], "input.summary")
        return cls(
            args_hash=_sha256(obj["args_hash"], "input.args_hash") or "",
            payload_ref=_str(obj["payload_ref"], "input.payload_ref"),
            payload_sha256=_sha256(obj["payload_sha256"], "input.payload_sha256") or "",
            summary=summary,
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
        obj = _obj(value, "journal_record")
        _exact(
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
        if _int(obj["schema_version"], "journal_record.schema_version", minimum=1) != SCHEMA_VERSION:
            raise SchemaError("unsupported journal_record.schema_version")
        state = _enum(ActionState, obj["state"], "journal_record.state")
        effect = EffectEvidence.from_dict(obj["effect"])
        reason = _opt_str(obj["reason"], "journal_record.reason")
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
            journal_seq=_int(obj["journal_seq"], "journal_record.journal_seq", minimum=1),
            action_seq=_int(obj["action_seq"], "journal_record.action_seq", minimum=1),
            transition_seq=_int(obj["transition_seq"], "journal_record.transition_seq", minimum=1),
            action_id=_sha256(obj["action_id"], "journal_record.action_id") or "",
            session_id=_uuid4(obj["session_id"], "journal_record.session_id") or "",
            flow_id=_uuid4(obj["flow_id"], "journal_record.flow_id", optional=True),
            attempt_seq=_int(obj["attempt_seq"], "journal_record.attempt_seq", minimum=1),
            attempt_id=_sha256(obj["attempt_id"], "journal_record.attempt_id") or "",
            state=state,
            effect=effect,
            recorded_at=_utc(obj["recorded_at"], "journal_record.recorded_at"),
            previous_record_hash=_sha256(
                obj["previous_record_hash"], "journal_record.previous_record_hash", optional=True
            ),
            record_hash=_sha256(obj["record_hash"], "journal_record.record_hash") or "",
            proposal_id=_str(obj["proposal_id"], "journal_record.proposal_id"),
            proposal_digest=_sha256(obj["proposal_digest"], "journal_record.proposal_digest") or "",
            action_type=_str(obj["action_type"], "journal_record.action_type"),
            effect_target_fingerprint=_sha256(
                obj["effect_target_fingerprint"], "journal_record.effect_target_fingerprint"
            ) or "",
            risk=_enum(RiskClass, obj["risk"], "journal_record.risk"),
            input=ActionInputRef.from_dict(obj["input"]),
            workspace_digest_context=_sha256(
                obj["workspace_digest_context"], "journal_record.workspace_digest_context"
            ) or "",
            reason=reason,
        )

    def to_dict(self) -> dict[str, Any]:
        return _to_data(self)


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
        obj = _obj(value, "checkpoint_resource")
        _exact(
            obj,
            {"canonical_relative_path", "existed", "size", "content_sha256", "backup_ref", "backup_sha256"},
            "checkpoint_resource",
        )
        existed = _bool(obj["existed"], "checkpoint_resource.existed")
        size = _int(obj["size"], "checkpoint_resource.size")
        content = _sha256(obj["content_sha256"], "checkpoint_resource.content_sha256", optional=True)
        backup_ref = _opt_str(obj["backup_ref"], "checkpoint_resource.backup_ref")
        backup_sha = _sha256(obj["backup_sha256"], "checkpoint_resource.backup_sha256", optional=True)
        if existed and (content is None or backup_ref is None or backup_sha is None):
            raise SchemaError("existing checkpoint resource requires content and backup integrity fields")
        if not existed and any(item is not None for item in (content, backup_ref, backup_sha)):
            raise SchemaError("non-existent checkpoint resource must not contain backup fields")
        if not existed and size != 0:
            raise SchemaError("non-existent checkpoint resource size must be zero")
        if existed and content != backup_sha:
            raise SchemaError("checkpoint backup_sha256 must equal original content_sha256 in v1")
        return cls(
            canonical_relative_path=_str(
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
        obj = _obj(value, "checkpoint")
        _exact(
            obj,
            {
                "schema_version", "checkpoint_id", "session_id", "action_id", "kind",
                "created_at", "workspace_digest", "head_sha", "resources", "manifest_sha256",
            },
            "checkpoint",
        )
        if _int(obj["schema_version"], "checkpoint.schema_version", minimum=1) != SCHEMA_VERSION:
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
            checkpoint_id=_uuid4(obj["checkpoint_id"], "checkpoint.checkpoint_id") or "",
            session_id=_uuid4(obj["session_id"], "checkpoint.session_id") or "",
            action_id=_sha256(obj["action_id"], "checkpoint.action_id", optional=True),
            kind=_enum(CheckpointKind, obj["kind"], "checkpoint.kind"),
            created_at=_utc(obj["created_at"], "checkpoint.created_at"),
            workspace_digest=_sha256(obj["workspace_digest"], "checkpoint.workspace_digest") or "",
            head_sha=_git_sha(obj["head_sha"], "checkpoint.head_sha") or "",
            resources=resources,
            manifest_sha256=_sha256(obj["manifest_sha256"], "checkpoint.manifest_sha256") or "",
        )

    def to_dict(self) -> dict[str, Any]:
        return _to_data(self)


__all__ = [
    "ActionInputRef", "ActionState", "BindingStatus", "BrowserBinding",
    "BrowserResourceTarget", "BrowserRole", "BrowserState", "CheckpointKind",
    "CheckpointManifest", "CheckpointResource", "EffectEvidence", "EffectStatus",
    "ExecutionState", "FileResourceBefore", "GoalState", "JournalRecord",
    "LifecyclePhase", "LifecycleReason", "LifecycleState", "LifecycleStatus",
    "LimitsState", "ProjectState", "RecoveryState", "RequestedMode", "ResourceType",
    "RiskClass", "SCHEMA_VERSION", "SchemaError", "StateSnapshot", "WorkspaceBaseline",
    "WorkspaceState",
]
