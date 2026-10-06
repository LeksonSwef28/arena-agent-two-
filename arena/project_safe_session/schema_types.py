"""Enums and constants for project-safe durable-session schemas."""
from __future__ import annotations

from enum import Enum

SCHEMA_VERSION = 1


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


__all__ = [
    "ActionState", "BindingStatus", "BrowserRole", "CheckpointKind",
    "EffectStatus", "LifecyclePhase", "LifecycleReason", "LifecycleStatus",
    "RequestedMode", "ResourceType", "RiskClass", "SCHEMA_VERSION",
]
