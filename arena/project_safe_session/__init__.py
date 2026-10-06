"""Project-safe durable-session foundation.

P1 deliberately keeps logical project-safe sessions separate from the existing
ephemeral code-workbench sessions.  This package starts with only the state-root
boundary and project lease; schemas/journals/execution are added in later slices.
"""
from .lease import ProjectLease, ProjectLeaseBusyError, ProjectLeaseError
from .paths import (
    ProjectSafeStateError,
    default_project_safe_state_root,
    project_fingerprint,
    resolve_project_safe_state_root,
)

__all__ = [
    "ProjectLease",
    "ProjectLeaseBusyError",
    "ProjectLeaseError",
    "ProjectSafeStateError",
    "default_project_safe_state_root",
    "project_fingerprint",
    "resolve_project_safe_state_root",
]

from .canonical import canonical_json_bytes, canonical_sha256
from .models import (
    ActionState,
    BrowserRole,
    CheckpointKind,
    CheckpointManifest,
    EffectStatus,
    EventType,
    FileResourceBefore,
    JournalRecord,
    LifecyclePhase,
    LifecycleReason,
    LifecycleStatus,
    SchemaError,
    SessionEventRecord,
    StateSnapshot,
)

__all__ += [
    "ActionState",
    "BrowserRole",
    "CheckpointKind",
    "CheckpointManifest",
    "EffectStatus",
    "EventType",
    "FileResourceBefore",
    "JournalRecord",
    "LifecyclePhase",
    "LifecycleReason",
    "LifecycleStatus",
    "SchemaError",
    "SessionEventRecord",
    "StateSnapshot",
    "canonical_json_bytes",
    "canonical_sha256",
]
