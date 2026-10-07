"""Read-only projection of status evidence emitted by coordinator and recovery."""
from __future__ import annotations

from .event_models import SessionEventRecord
from .schema_types import EventType, LifecyclePhase, LifecycleReason, LifecycleStatus, RequestedMode
from .schema_utils import SchemaError, enum_value, exact_keys
from .state_models import LifecycleState, StateSnapshot


def _status_target(
    current: LifecycleState,
    event: SessionEventRecord,
    *,
    has_flow: bool,
    mode: RequestedMode,
) -> LifecycleState:
    data = event.data
    exact_keys(data, {"from", "to", "reason"}, "STATUS_CHANGED.data")
    previous = enum_value(LifecycleStatus, data["from"], "STATUS_CHANGED.from")
    target = enum_value(LifecycleStatus, data["to"], "STATUS_CHANGED.to")
    if previous is not current.status:
        raise SchemaError(f"STATUS_CHANGED chain is discontinuous at event_seq={event.event_seq}")
    phase = current.phase
    reason = None
    event_reason = None
    pair = (previous, target)
    if pair in {
        (LifecycleStatus.CREATED, LifecycleStatus.ACTIVE),
        (LifecycleStatus.ACTIVE, LifecycleStatus.PAUSED),
        (LifecycleStatus.PAUSED, LifecycleStatus.ACTIVE),
    }:
        pass
    elif pair == (LifecycleStatus.PAUSED, LifecycleStatus.WAITING) and has_flow:
        reason = LifecycleReason.WORKSPACE_DRIFT
        event_reason = reason.value
    elif (
        pair == (LifecycleStatus.ACTIVE, LifecycleStatus.WAITING)
        and not has_flow and mode is RequestedMode.WRITE
    ):
        phase = LifecyclePhase.IDLE
        reason = LifecycleReason.WORKSPACE_DIRTY
        event_reason = reason.value
    elif (
        pair == (LifecycleStatus.WAITING, LifecycleStatus.ACTIVE)
        and current.reason is LifecycleReason.WORKSPACE_DIRTY
        and not has_flow and mode is RequestedMode.WRITE
    ):
        # This existing event marker is not a reason stored in LifecycleState.
        event_reason = "WORKSPACE_DIRTY_RESOLVED"
    else:
        raise SchemaError(f"STATUS_CHANGED transition {previous.value}->{target.value} is unsupported")
    if data["reason"] != event_reason:
        raise SchemaError("STATUS_CHANGED.reason disagrees with transition semantics")
    return LifecycleState.from_dict({
        "status": target.value, "phase": phase.value,
        "reason": None if reason is None else reason.value, "changed_at": event.recorded_at,
    })


def lifecycle_status_mismatch(
    state: StateSnapshot,
    events: tuple[SessionEventRecord, ...],
) -> str | None:
    """Compare status/reason/time; phase and action/recovery payload proof are separate."""
    current = LifecycleState.from_dict({
        "status": "CREATED", "phase": "IDLE", "reason": None, "changed_at": state.created_at,
    })
    has_flow = False
    for event in events:
        kind = event.event_type
        if kind is EventType.STATUS_CHANGED:
            current = _status_target(current, event, has_flow=has_flow, mode=state.project.requested_mode)
            continue
        phase = current.phase
        status, reason = current.status, current.reason
        if kind is EventType.FLOW_CREATED:
            if status is not LifecycleStatus.ACTIVE:
                raise SchemaError("FLOW_CREATED requires ACTIVE lifecycle status")
            has_flow = True
            phase = LifecyclePhase.PLANNING
        elif kind is EventType.FLOW_CLOSED:
            has_flow = False
            phase = LifecyclePhase.IDLE
        elif kind is EventType.GOAL_REFINED:
            if status not in {LifecycleStatus.CREATED, LifecycleStatus.ACTIVE, LifecycleStatus.PAUSED}:
                raise SchemaError("GOAL_REFINED requires CREATED/ACTIVE/PAUSED lifecycle status")
            has_flow = False
            phase = LifecyclePhase.IDLE
        elif kind is EventType.RECOVERY_DETECTED:
            status, phase, reason = (
                LifecycleStatus.WAITING, LifecyclePhase.RECOVERY, LifecycleReason.RECOVERY_REQUIRED,
            )
        else:
            continue
        current = LifecycleState.from_dict({
            "status": status.value, "phase": phase.value,
            "reason": None if reason is None else reason.value, "changed_at": event.recorded_at,
        })
    for field in ("status", "reason", "changed_at"):
        if getattr(state.lifecycle, field) != getattr(current, field):
            return f"lifecycle.{field} disagrees with implemented lifecycle event history"
    return None
