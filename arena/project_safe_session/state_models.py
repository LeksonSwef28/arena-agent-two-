"""Strict state.json models for project-safe v1."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .schema_types import (
    BindingStatus,
    BrowserRole,
    LifecyclePhase,
    LifecycleReason,
    LifecycleStatus,
    RequestedMode,
    SCHEMA_VERSION,
)
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
class ProjectState:
    canonical_root: str
    requested_mode: RequestedMode

    @classmethod
    def from_dict(cls, value: Any) -> "ProjectState":
        obj = object_value(value, "project")
        exact_keys(obj, {"canonical_root", "requested_mode"}, "project")
        return cls(
            canonical_root=string_value(obj["canonical_root"], "project.canonical_root"),
            requested_mode=enum_value(RequestedMode, obj["requested_mode"], "project.requested_mode"),
        )


@dataclass(frozen=True)
class GoalState:
    initial: str
    current: str
    revision: int
    last_changed_at: str

    @classmethod
    def from_dict(cls, value: Any) -> "GoalState":
        obj = object_value(value, "goal")
        exact_keys(obj, {"initial", "current", "revision", "last_changed_at"}, "goal")
        return cls(
            initial=string_value(obj["initial"], "goal.initial"),
            current=string_value(obj["current"], "goal.current"),
            revision=integer_value(obj["revision"], "goal.revision", minimum=1),
            last_changed_at=utc_value(obj["last_changed_at"], "goal.last_changed_at"),
        )


@dataclass(frozen=True)
class LifecycleState:
    status: LifecycleStatus
    phase: LifecyclePhase
    reason: LifecycleReason | None
    changed_at: str

    @classmethod
    def from_dict(cls, value: Any) -> "LifecycleState":
        obj = object_value(value, "lifecycle")
        exact_keys(obj, {"status", "phase", "reason", "changed_at"}, "lifecycle")
        status = enum_value(LifecycleStatus, obj["status"], "lifecycle.status")
        phase = enum_value(LifecyclePhase, obj["phase"], "lifecycle.phase")
        raw_reason = obj["reason"]
        reason = None if raw_reason is None else enum_value(LifecycleReason, raw_reason, "lifecycle.reason")
        if status is LifecycleStatus.WAITING and reason is None:
            raise SchemaError("lifecycle.reason is required when status=WAITING")
        if status is not LifecycleStatus.WAITING and reason is not None:
            raise SchemaError("lifecycle.reason must be null unless status=WAITING")
        if phase is LifecyclePhase.RECOVERY and status is not LifecycleStatus.WAITING:
            raise SchemaError("RECOVERY phase requires WAITING status")
        return cls(
            status=status,
            phase=phase,
            reason=reason,
            changed_at=utc_value(obj["changed_at"], "lifecycle.changed_at"),
        )


@dataclass(frozen=True)
class WorkspaceBaseline:
    checkpoint_id: str
    head_sha: str
    workspace_digest: str
    clean: bool
    captured_at: str

    @classmethod
    def from_dict(cls, value: Any) -> "WorkspaceBaseline":
        obj = object_value(value, "workspace.session_baseline")
        exact_keys(
            obj,
            {"checkpoint_id", "head_sha", "workspace_digest", "clean", "captured_at"},
            "workspace.session_baseline",
        )
        return cls(
            checkpoint_id=uuid4_value(obj["checkpoint_id"], "workspace.session_baseline.checkpoint_id") or "",
            head_sha=git_sha_value(obj["head_sha"], "workspace.session_baseline.head_sha") or "",
            workspace_digest=sha256_value(
                obj["workspace_digest"], "workspace.session_baseline.workspace_digest"
            ) or "",
            clean=boolean_value(obj["clean"], "workspace.session_baseline.clean"),
            captured_at=utc_value(obj["captured_at"], "workspace.session_baseline.captured_at"),
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
        obj = object_value(value, "workspace")
        exact_keys(
            obj,
            {
                "kind", "session_baseline", "workspace_digest_last_verified",
                "last_verified_checkpoint_id", "last_verified_head_sha", "last_verified_at",
            },
            "workspace",
        )
        kind = string_value(obj["kind"], "workspace.kind")
        if kind != "git_worktree":
            raise SchemaError("workspace.kind must be 'git_worktree'")
        return cls(
            kind=kind,
            session_baseline=WorkspaceBaseline.from_dict(obj["session_baseline"]),
            workspace_digest_last_verified=sha256_value(
                obj["workspace_digest_last_verified"], "workspace.workspace_digest_last_verified"
            ) or "",
            last_verified_checkpoint_id=uuid4_value(
                obj["last_verified_checkpoint_id"], "workspace.last_verified_checkpoint_id"
            ) or "",
            last_verified_head_sha=git_sha_value(
                obj["last_verified_head_sha"], "workspace.last_verified_head_sha"
            ) or "",
            last_verified_at=utc_value(obj["last_verified_at"], "workspace.last_verified_at"),
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
        obj = object_value(value, label)
        exact_keys(
            obj,
            {
                "provider", "status", "binding_revision", "browser_instance_id", "tab_id",
                "origin", "conversation_url", "conversation_fingerprint", "bound_at",
                "last_verified_at", "stale_reason",
            },
            label,
        )
        status = enum_value(BindingStatus, obj["status"], f"{label}.status")
        stale_reason = optional_string(obj["stale_reason"], f"{label}.stale_reason")
        if status is BindingStatus.STALE and stale_reason is None:
            raise SchemaError(f"{label}.stale_reason is required when status=STALE")
        if status is not BindingStatus.STALE and stale_reason is not None:
            raise SchemaError(f"{label}.stale_reason must be null unless status=STALE")
        return cls(
            provider=string_value(obj["provider"], f"{label}.provider"),
            status=status,
            binding_revision=integer_value(obj["binding_revision"], f"{label}.binding_revision", minimum=1),
            browser_instance_id=uuid4_value(obj["browser_instance_id"], f"{label}.browser_instance_id") or "",
            tab_id=string_value(obj["tab_id"], f"{label}.tab_id"),
            origin=string_value(obj["origin"], f"{label}.origin"),
            conversation_url=string_value(obj["conversation_url"], f"{label}.conversation_url"),
            conversation_fingerprint=string_value(
                obj["conversation_fingerprint"], f"{label}.conversation_fingerprint"
            ),
            bound_at=utc_value(obj["bound_at"], f"{label}.bound_at"),
            last_verified_at=utc_value(obj["last_verified_at"], f"{label}.last_verified_at"),
            stale_reason=stale_reason,
        )


@dataclass(frozen=True)
class BrowserState:
    required_roles: tuple[BrowserRole, ...]
    bindings: dict[BrowserRole, BrowserBinding | None]

    @classmethod
    def from_dict(cls, value: Any) -> "BrowserState":
        obj = object_value(value, "browser")
        exact_keys(obj, {"required_roles", "bindings"}, "browser")
        raw_roles = obj["required_roles"]
        if not isinstance(raw_roles, list):
            raise SchemaError("browser.required_roles must be an array")
        roles = tuple(enum_value(BrowserRole, item, "browser.required_roles[]") for item in raw_roles)
        if len(set(roles)) != len(roles):
            raise SchemaError("browser.required_roles contains duplicates")
        raw_bindings = object_value(obj["bindings"], "browser.bindings")
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
        obj = object_value(value, "execution")
        exact_keys(
            obj,
            {"active_flow_id", "pending_action_id", "last_terminal_action_id", "last_action_seq", "last_event_seq"},
            "execution",
        )
        return cls(
            active_flow_id=uuid4_value(obj["active_flow_id"], "execution.active_flow_id", optional=True),
            pending_action_id=sha256_value(obj["pending_action_id"], "execution.pending_action_id", optional=True),
            last_terminal_action_id=sha256_value(
                obj["last_terminal_action_id"], "execution.last_terminal_action_id", optional=True
            ),
            last_action_seq=integer_value(obj["last_action_seq"], "execution.last_action_seq"),
            last_event_seq=integer_value(obj["last_event_seq"], "execution.last_event_seq"),
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
        obj = object_value(value, "recovery")
        exact_keys(
            obj,
            {"recovery_id", "reason", "interrupted_action_id", "detected_at", "workspace_checkpoint_id"},
            "recovery",
        )
        reason = string_value(obj["reason"], "recovery.reason")
        if reason not in {
            "INTERRUPTED_ACTION", "JOURNAL_CORRUPT", "TRUNCATED_LAST_RECORD",
            "PAYLOAD_INTEGRITY_FAILURE", "WORKSPACE_DRIFT", "LEASE_AMBIGUOUS",
        }:
            raise SchemaError(f"unsupported recovery.reason: {reason!r}")
        return cls(
            recovery_id=uuid4_value(obj["recovery_id"], "recovery.recovery_id") or "",
            reason=reason,
            interrupted_action_id=sha256_value(
                obj["interrupted_action_id"], "recovery.interrupted_action_id"
            ) or "",
            detected_at=utc_value(obj["detected_at"], "recovery.detected_at"),
            workspace_checkpoint_id=uuid4_value(
                obj["workspace_checkpoint_id"], "recovery.workspace_checkpoint_id"
            ) or "",
        )


@dataclass(frozen=True)
class LimitsState:
    max_actions: int
    max_messages_per_role: int
    max_active_minutes: int

    @classmethod
    def from_dict(cls, value: Any) -> "LimitsState":
        obj = object_value(value, "limits")
        exact_keys(obj, {"max_actions", "max_messages_per_role", "max_active_minutes"}, "limits")
        return cls(
            max_actions=integer_value(obj["max_actions"], "limits.max_actions", minimum=1),
            max_messages_per_role=integer_value(
                obj["max_messages_per_role"], "limits.max_messages_per_role", minimum=1
            ),
            max_active_minutes=integer_value(obj["max_active_minutes"], "limits.max_active_minutes", minimum=1),
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
        obj = object_value(value, "state")
        exact_keys(
            obj,
            {
                "schema_version", "state_revision", "session_id", "session_fingerprint",
                "parent_session_id", "project_fingerprint", "project", "goal",
                "lifecycle", "workspace", "browser", "execution", "recovery",
                "limits", "created_at", "updated_at",
            },
            "state",
        )
        if integer_value(obj["schema_version"], "state.schema_version", minimum=1) != SCHEMA_VERSION:
            raise SchemaError("unsupported state.schema_version")
        lifecycle = LifecycleState.from_dict(obj["lifecycle"])
        recovery = None if obj["recovery"] is None else RecoveryState.from_dict(obj["recovery"])
        if lifecycle.phase is LifecyclePhase.RECOVERY and recovery is None:
            raise SchemaError("recovery object is required during RECOVERY phase")
        if lifecycle.phase is not LifecyclePhase.RECOVERY and recovery is not None:
            raise SchemaError("recovery object must be null outside RECOVERY phase")
        return cls(
            schema_version=SCHEMA_VERSION,
            state_revision=integer_value(obj["state_revision"], "state.state_revision", minimum=1),
            session_id=uuid4_value(obj["session_id"], "state.session_id") or "",
            session_fingerprint=sha256_value(obj["session_fingerprint"], "state.session_fingerprint") or "",
            parent_session_id=uuid4_value(obj["parent_session_id"], "state.parent_session_id", optional=True),
            project_fingerprint=sha256_value(obj["project_fingerprint"], "state.project_fingerprint") or "",
            project=ProjectState.from_dict(obj["project"]),
            goal=GoalState.from_dict(obj["goal"]),
            lifecycle=lifecycle,
            workspace=WorkspaceState.from_dict(obj["workspace"]),
            browser=BrowserState.from_dict(obj["browser"]),
            execution=ExecutionState.from_dict(obj["execution"]),
            recovery=recovery,
            limits=LimitsState.from_dict(obj["limits"]),
            created_at=utc_value(obj["created_at"], "state.created_at"),
            updated_at=utc_value(obj["updated_at"], "state.updated_at"),
        )

    def to_dict(self) -> dict[str, Any]:
        return to_data(self)


__all__ = [
    "BrowserBinding", "BrowserState", "ExecutionState", "GoalState", "LifecycleState",
    "LimitsState", "ProjectState", "RecoveryState", "StateSnapshot", "WorkspaceBaseline",
    "WorkspaceState",
]
