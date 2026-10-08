"""Explicit storage parser policy; no automatic upgrade or admission policy."""
from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from .action_contract import validate_action_history
from .action_models import JournalRecord
from .canonical import strict_json_loads
from .checkpoint_models import CheckpointManifest
from .schema_utils import SchemaError, object_value
from .state_models import StateSnapshot
from .v2_action_history import validate_v2_action_history
from .v2_models import CheckpointManifestV2, FormatVersionsV2, JournalRecordV2, StateSnapshotV2


@dataclass(frozen=True)
class StorageFormat:
    version: int
    state_model: type[StateSnapshot]
    action_model: type[JournalRecord]
    checkpoint_model: type[CheckpointManifest]
    validate_actions: Callable[[Sequence[JournalRecord]], None]
    extra_action_keys: frozenset[str] = frozenset()

    def require_session_format(self, state_path: Path) -> None:
        # Check format only; state semantic validation belongs to read_state.
        # A missing state permits baseline-first publication for a new session.
        if not state_path.exists():
            return
        raw = object_value(strict_json_loads(state_path.read_bytes()), "state")
        version = raw.get("schema_version")
        if type(version) is not int or version != self.version:
            raise SchemaError("session format does not match selected storage version")
        if self.version == 2:
            FormatVersionsV2.from_dict(raw.get("formats"))
        elif "formats" in raw:
            raise SchemaError("v1 state cannot declare a v2 format tuple")


V1_STORAGE_FORMAT = StorageFormat(1, StateSnapshot, JournalRecord,
                                 CheckpointManifest, validate_action_history)
V2_STORAGE_FORMAT = StorageFormat(2, StateSnapshotV2, JournalRecordV2,
                                 CheckpointManifestV2, validate_v2_action_history,
                                 frozenset({"flow_creation_ref", "prepared_event_ref",
                                            "preceding_success_ref", "workspace_verification"}))
