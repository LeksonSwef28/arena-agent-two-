"""Read-only flow lineage checks after creation and goal evidence validation."""
from __future__ import annotations

from .event_models import SessionEventRecord
from .schema_types import EventType, RequestedMode
from .schema_utils import SchemaError, boolean_value, exact_keys, git_sha_value, string_value, uuid4_value
from .state_models import ActiveFlowState, StateSnapshot


def flow_history_mismatch(state: StateSnapshot, events: tuple[SessionEventRecord, ...]) -> str | None:
    """Compare flow lineage and immutable snapshot fields, not action-driven digest progression."""
    active: ActiveFlowState | None = None
    seen_ids: set[str] = set()
    goal_revision = 1
    for event in events:
        data = event.data
        if event.event_type is EventType.GOAL_REFINED:
            if active is not None:
                raise SchemaError("GOAL_REFINED requires the previous flow to be closed")
            goal_revision += 1
        elif event.event_type is EventType.FLOW_CREATED:
            exact_keys(data, {
                "flow_id", "goal_revision", "workspace_digest_baseline",
                "workspace_digest_expected_current", "workspace_clean", "head_sha",
            }, "FLOW_CREATED.data")
            flow = ActiveFlowState.from_dict({
                key: data[key] for key in (
                    "flow_id", "goal_revision", "workspace_digest_baseline",
                    "workspace_digest_expected_current",
                )
            } | {"created_at": event.recorded_at})
            clean = boolean_value(data["workspace_clean"], "FLOW_CREATED.workspace_clean")
            git_sha_value(data["head_sha"], "FLOW_CREATED.head_sha")
            if active is not None:
                raise SchemaError("FLOW_CREATED overlaps an unclosed flow")
            if flow.flow_id in seen_ids:
                raise SchemaError("FLOW_CREATED reuses a previous flow_id")
            if flow.goal_revision != goal_revision:
                raise SchemaError("FLOW_CREATED goal_revision disagrees with goal history")
            if flow.workspace_digest_expected_current != flow.workspace_digest_baseline:
                raise SchemaError("FLOW_CREATED expected-current digest must start at baseline")
            if state.project.requested_mode is RequestedMode.WRITE and not clean:
                raise SchemaError("FLOW_CREATED write flow requires a clean workspace")
            seen_ids.add(flow.flow_id)
            active = flow
        elif event.event_type is EventType.FLOW_CLOSED:
            exact_keys(data, {"flow_id", "reason"}, "FLOW_CLOSED.data")
            flow_id = uuid4_value(data["flow_id"], "FLOW_CLOSED.flow_id")
            string_value(data["reason"], "FLOW_CLOSED.reason", empty=True)
            if active is None or flow_id != active.flow_id:
                raise SchemaError("FLOW_CLOSED does not match an open flow")
            active = None

    snapshot = state.active_flow
    if (active is None) != (snapshot is None):
        return "active_flow presence disagrees with flow history"
    if active is not None and snapshot is not None:
        for field in ("flow_id", "goal_revision", "workspace_digest_baseline", "created_at"):
            if getattr(snapshot, field) != getattr(active, field):
                return f"active_flow.{field} disagrees with FLOW_CREATED history"
    return None
