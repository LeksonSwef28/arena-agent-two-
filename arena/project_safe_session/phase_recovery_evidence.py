"""Bind phase/recovery snapshots to existing durable events and action markers."""
from __future__ import annotations

from .action_models import JournalRecord
from .event_models import SessionEventRecord
from .schema_types import ActionState, EventType, LifecyclePhase, RecoveryReason
from .schema_utils import SchemaError, enum_value, exact_keys, sha256_value, string_value
from .state_models import StateSnapshot


def phase_recovery_mismatch(
    state: StateSnapshot,
    projected_phase: LifecyclePhase,
    events: tuple[SessionEventRecord, ...],
    actions: tuple[JournalRecord, ...],
) -> str | None:
    detections = tuple(event for event in events if event.event_type is EventType.RECOVERY_DETECTED)
    # No recovery-resolution emitter exists yet; entry is idempotent until resolution.
    if len(detections) > 1:
        raise SchemaError("duplicate RECOVERY_DETECTED without a supported resolution")
    if detections:
        event = detections[0]
        data = event.data
        exact_keys(data, {"reason", "interrupted_action_id", "message"}, "RECOVERY_DETECTED.data")
        reason = enum_value(RecoveryReason, data["reason"], "RECOVERY_DETECTED.reason")
        if reason is not RecoveryReason.INTERRUPTED_ACTION:
            raise SchemaError("RECOVERY_DETECTED requires INTERRUPTED_ACTION reason")
        action_id = sha256_value(data["interrupted_action_id"], "RECOVERY_DETECTED.interrupted_action_id")
        string_value(data["message"], "RECOVERY_DETECTED.message", empty=True)
        snapshot = state.recovery
        if snapshot is None:
            return "RECOVERY_DETECTED has no recovery snapshot"
        for field, actual, expected in (
            ("reason", snapshot.reason, reason),
            ("interrupted_action_id", snapshot.interrupted_action_id, action_id),
            ("detected_at", snapshot.detected_at, event.recorded_at),
            ("pending_action_id", state.execution.pending_action_id, action_id),
        ):
            if actual != expected:
                return f"recovery {field} disagrees with RECOVERY_DETECTED"
        matching = tuple(record for record in actions if record.action_id == action_id)
        if not matching or state.active_flow is None:
            return "RECOVERY_DETECTED lacks interrupted action/flow journal evidence"
        latest = matching[-1]
        if latest.flow_id != state.active_flow.flow_id:
            return "RECOVERY_DETECTED action does not belong to active flow"
        if latest.state not in {ActionState.PREPARED, ActionState.INTERRUPTED}:
            return "RECOVERY_DETECTED action lacks a PREPARED/INTERRUPTED recovery marker"
    elif state.recovery is not None:
        return "recovery snapshot has no RECOVERY_DETECTED evidence"

    if state.lifecycle.phase is projected_phase:
        return None
    # Journals may be ahead of snapshots after a crash. Do not require phase equality
    # with the latest action; only admit action phases backed by same-flow markers.
    if projected_phase is LifecyclePhase.PLANNING and state.active_flow is not None:
        marker = {
            LifecyclePhase.EXECUTING: ActionState.EXECUTING,
            LifecyclePhase.VERIFYING: ActionState.VERIFYING,
        }.get(state.lifecycle.phase)
        if marker is not None and any(
            record.flow_id == state.active_flow.flow_id and record.state is marker
            and (state.execution.pending_action_id is None or record.action_id == state.execution.pending_action_id)
            for record in actions
        ):
            return None
    return "lifecycle.phase lacks matching event/action evidence"
