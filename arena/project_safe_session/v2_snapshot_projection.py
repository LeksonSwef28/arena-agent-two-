"""Opt-in pure snapshot projection; never repairs state or declares admission clean."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from .checkpoint_contract import validate_checkpoint_manifest_digest
from .event_models import SessionEventRecord
from .flow_evidence import flow_history_mismatch
from .recovery import _TERMINAL_ACTION_STATES
from .schema_types import CheckpointKind
from .schema_utils import SchemaError
from .v2_models import ActionRecordRef, CheckpointManifestV2, JournalRecordV2, StateSnapshotV2
from .v2_reference_history import validate_v2_reference_history


@dataclass(frozen=True)
class V2SnapshotProjection:
    applied_action_ref: ActionRecordRef | None
    unapplied_actions: tuple[JournalRecordV2, ...]
    unapplied_events: tuple[SessionEventRecord, ...]

    @property
    def snapshot_behind(self) -> bool:
        return bool(self.unapplied_actions or self.unapplied_events)


def assess_v2_snapshot_projection(
    state: StateSnapshotV2,
    events: tuple[SessionEventRecord, ...],
    actions: tuple[JournalRecordV2, ...],
    checkpoints: Mapping[str, CheckpointManifestV2],
) -> V2SnapshotProjection:
    """Compare action/workspace/flow fields at the snapshot's exact prefixes.

    All action references, including the unapplied suffix, are validated first.
    Complete session/goal/lifecycle semantics, payload/backup bytes, resource CAS,
    timestamps and current workspace remain caller obligations. An empty suffix
    is not a clean recovery/admission verdict; pending/effects still need policy.
    """
    state = StateSnapshotV2.from_dict(state.to_dict())
    successes = validate_v2_reference_history(state.session_id, events, actions, checkpoints)
    event_count = state.execution.last_event_seq
    if event_count > len(events):
        raise SchemaError("snapshot event watermark is ahead of event journal")
    reference = state.execution.last_applied_action_ref
    action_count = 0 if reference is None else reference.journal_seq
    if action_count > len(actions):
        raise SchemaError("snapshot action watermark is ahead of action journal")
    if reference is not None and actions[action_count - 1].record_hash != reference.record_hash:
        raise SchemaError("snapshot action watermark hash mismatch")
    prefix = actions[:action_count]
    if any(record.prepared_event_ref.event_seq > event_count for record in prefix):
        raise SchemaError("applied action references an event beyond snapshot event watermark")

    baseline_state = state.workspace.session_baseline
    baseline = checkpoints.get(baseline_state.checkpoint_id)
    if baseline is None:
        raise SchemaError("session baseline checkpoint is missing")
    CheckpointManifestV2.from_dict(baseline.to_dict())
    validate_checkpoint_manifest_digest(baseline)
    if (baseline.checkpoint_id != baseline_state.checkpoint_id
            or baseline.session_id != state.session_id
            or baseline.kind is not CheckpointKind.SESSION_BASELINE
            or baseline.workspace_digest != baseline_state.workspace_digest
            or baseline.head_sha != baseline_state.head_sha):
        raise SchemaError("session baseline checkpoint projection mismatch")

    applied_successes = [item for item in successes if item.action.journal_seq <= action_count]
    verified = baseline if not applied_successes else applied_successes[-1].checkpoint
    if (state.workspace.last_verified_checkpoint_id != verified.checkpoint_id
            or state.workspace.workspace_digest_last_verified != verified.workspace_digest
            or state.workspace.last_verified_head_sha != verified.head_sha):
        raise SchemaError("last verified workspace projection mismatch at action watermark")

    # Preserve v1's latest-state-per-logical-action terminal counter semantics.
    latest = {record.action_id: record for record in prefix}
    terminal = [record for record in latest.values() if record.state in _TERMINAL_ACTION_STATES]
    last_terminal = max(terminal, key=lambda record: record.action_seq, default=None)
    expected_seq = 0 if last_terminal is None else last_terminal.action_seq
    expected_id = None if last_terminal is None else last_terminal.action_id
    if (state.execution.last_action_seq != expected_seq
            or state.execution.last_terminal_action_id != expected_id):
        raise SchemaError("terminal action projection mismatch at action watermark")
    pending = [record for record in latest.values() if record.state not in _TERMINAL_ACTION_STATES]
    if len(pending) > 1:
        raise SchemaError("snapshot prefix has more than one pending action")
    pending_id = None if not pending else pending[0].action_id
    if state.execution.pending_action_id != pending_id:
        raise SchemaError("pending action projection mismatch at action watermark")
    flow_mismatch = flow_history_mismatch(state, events[:event_count])
    if flow_mismatch is not None:
        raise SchemaError(flow_mismatch)
    flow = state.active_flow
    if pending and (flow is None or pending[0].flow_id != flow.flow_id):
        raise SchemaError("pending action does not belong to snapshot active flow")
    if flow is not None:
        flow_success = next((item for item in reversed(applied_successes)
                             if item.action.flow_id == flow.flow_id), None)
        expected_digest = (flow.workspace_digest_baseline if flow_success is None
                           else flow_success.checkpoint.workspace_digest)
        if flow.workspace_digest_expected_current != expected_digest:
            raise SchemaError("active flow expected-current projection mismatch")
        if pending and pending[0].workspace_digest_context != expected_digest:
            raise SchemaError("pending context disagrees with snapshot active flow")
    return V2SnapshotProjection(reference, actions[action_count:], events[event_count:])


__all__ = ["V2SnapshotProjection", "assess_v2_snapshot_projection"]
