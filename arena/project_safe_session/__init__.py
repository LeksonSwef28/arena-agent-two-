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

from .canonical import canonical_json_bytes, canonical_sha256, strict_json_loads
from .models import (
    ActiveFlowState,
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
    "ActiveFlowState",
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

from .storage import (
    CheckpointIntegrityError,
    JournalCorruptionError,
    LeaseRequiredError,
    PayloadIntegrityError,
    ProjectSafeSessionStore,
    StateRevisionError,
    StorageError,
    TruncatedLastRecordError,
)

__all__ += [
    "CheckpointIntegrityError",
    "JournalCorruptionError",
    "LeaseRequiredError",
    "PayloadIntegrityError",
    "ProjectSafeSessionStore",
    "StateRevisionError",
    "StorageError",
    "TruncatedLastRecordError",
    "strict_json_loads",
]

from .workspace import (
    RESOURCE_BACKUP_LIMIT,
    ResourceBoundaryError,
    ResourceDriftError,
    ResourceTooLargeError,
    UnsupportedRepoLayoutError,
    WorkspaceDriftError,
    WorkspaceError,
    WorkspaceGitError,
    WorkspaceManifest,
    WorkspaceUnstableError,
    assert_flow_workspace_guard,
    capture_file_resource_before,
    compute_workspace_digest_v1,
    file_effect_target_fingerprint,
    read_file_resource_backup_v1,
    validate_file_resource_cas,
)

__all__ += [
    "RESOURCE_BACKUP_LIMIT",
    "ResourceBoundaryError",
    "ResourceDriftError",
    "ResourceTooLargeError",
    "UnsupportedRepoLayoutError",
    "WorkspaceDriftError",
    "WorkspaceError",
    "WorkspaceGitError",
    "WorkspaceManifest",
    "WorkspaceUnstableError",
    "assert_flow_workspace_guard",
    "capture_file_resource_before",
    "compute_workspace_digest_v1",
    "file_effect_target_fingerprint",
    "read_file_resource_backup_v1",
    "validate_file_resource_cas",
]

from .action_contract import (
    ActionContractError,
    compute_action_id,
    compute_args_hash,
    compute_attempt_id,
    validate_action_history,
    validate_action_record,
)

__all__ += [
    "ActionContractError",
    "compute_action_id",
    "compute_args_hash",
    "compute_attempt_id",
    "validate_action_history",
    "validate_action_record",
]

from .checkpoint_contract import (
    CheckpointContractError,
    build_checkpoint_manifest,
    checkpoint_manifest_sha256,
    checkpoint_resource_from_file_before,
    validate_checkpoint_manifest_digest,
)
from .checkpointing import create_file_resource_before_checkpoint

__all__ += [
    "CheckpointContractError",
    "build_checkpoint_manifest",
    "checkpoint_manifest_sha256",
    "checkpoint_resource_from_file_before",
    "create_file_resource_before_checkpoint",
    "validate_checkpoint_manifest_digest",
]

from .registry import (
    ActiveSessionConflictError,
    ProjectRegistry,
    ProjectRegistryStore,
    RegistryError,
    RegistryRevisionError,
)

__all__ += [
    "ActiveSessionConflictError",
    "ProjectRegistry",
    "ProjectRegistryStore",
    "RegistryError",
    "RegistryRevisionError",
]
