"""Cross-file action ownership and latest verified workspace evidence checks."""
from __future__ import annotations

from .action_models import JournalRecord
from .checkpoint_models import CheckpointManifest
from .event_models import SessionEventRecord
from .schema_types import ActionState, CheckpointKind, EventType
from .state_models import StateSnapshot


def action_flow_mismatch(
    state: StateSnapshot,
    events: tuple[SessionEventRecord, ...],
    actions: tuple[JournalRecord, ...],
    last_verified: CheckpointManifest | None,
) -> str | None:
    """Requires validated flow history/checkpoints; does not infer generic effect digest meaning."""
    baselines = {
        event.data["flow_id"]: event.data["workspace_digest_baseline"]
        for event in events if event.event_type is EventType.FLOW_CREATED
    }
    succeeded_flows: set[str] = set()
    latest_success: JournalRecord | None = None
    for record in actions:
        flow_id = record.flow_id
        if flow_id is None or flow_id not in baselines:
            return "action journal references a flow absent from FLOW_CREATED history"
        if (
            record.state is ActionState.PREPARED and flow_id not in succeeded_flows
            and record.workspace_digest_context != baselines[flow_id]
        ):
            return "PREPARED workspace context differs from flow baseline before its first SUCCEEDED"
        if record.state is ActionState.SUCCEEDED:
            succeeded_flows.add(flow_id)
            latest_success = record

    if last_verified is None:
        return "last-verified workspace has no validated checkpoint"
    if latest_success is None:
        if (
            last_verified.kind is not CheckpointKind.SESSION_BASELINE
            or last_verified.checkpoint_id != state.workspace.session_baseline.checkpoint_id
        ):
            return "last-verified checkpoint changed without SUCCEEDED journal evidence"
    elif (
        last_verified.kind is not CheckpointKind.RESOURCE_AFTER
        or last_verified.action_id != latest_success.action_id
    ):
        return "last-verified checkpoint is not RESOURCE_AFTER for the latest SUCCEEDED action"

    flow = state.active_flow
    if flow is not None:
        expected = flow.workspace_digest_baseline
        if flow.flow_id in succeeded_flows:
            if latest_success is None or latest_success.flow_id != flow.flow_id:
                return "active flow does not own the latest SUCCEEDED workspace verification"
            expected = last_verified.workspace_digest
        if flow.workspace_digest_expected_current != expected:
            return "active flow expected-current digest disagrees with baseline/verified checkpoint evidence"
    return None
