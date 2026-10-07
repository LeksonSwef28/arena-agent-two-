"""Fail-closed recovery assessment and interrupted-action marking for P1-B2."""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from .coordinator import SessionCoordinatorError, normalize_goal_v1, session_fingerprint_v1
from .event_models import SessionEventRecord
from .flow_evidence import flow_history_mismatch
from .lease import ProjectLease
from .models import StateSnapshot
from .registry import ProjectRegistryStore, RegistryError
from .schema_types import (
    ActionState,
    EventType,
    LifecycleStatus,
    RecoveryReason,
    RequestedMode,
)
from .schema_utils import SchemaError, boolean_value, enum_value, exact_keys, integer_value, sha256_value, string_value
from .storage import (
    CheckpointIntegrityError,
    JournalCorruptionError,
    PayloadIntegrityError,
    ProjectSafeSessionStore,
    StorageError,
    TruncatedLastRecordError,
)
from .workspace import WorkspaceError, compute_workspace_digest_v1


@dataclass(frozen=True)
class RecoveryAssessment:
    required: bool
    reason: RecoveryReason | None
    message: str
    interrupted_action_id: str | None = None
    workspace_checkpoint_id: str | None = None

    @classmethod
    def clean(cls) -> "RecoveryAssessment":
        return cls(required=False, reason=None, message="consistent")


class RecoveryOperationError(RuntimeError):
    """Recovery state could not be entered deterministically."""


_TERMINAL_ACTION_STATES = {
    ActionState.SUCCEEDED,
    ActionState.FAILED,
    ActionState.ABANDONED,
}


def _session_creation_mismatch(
    state: StateSnapshot,
    events: tuple[SessionEventRecord, ...],
) -> str | None:
    if (
        not events
        or events[0].event_type is not EventType.SESSION_CREATED
        or sum(event.event_type is EventType.SESSION_CREATED for event in events) != 1
    ):
        raise SchemaError("events require exactly one SESSION_CREATED as the first record")
    creation = events[0]
    data = creation.data
    exact_keys(
        data,
        {"goal_revision", "requested_mode", "workspace_digest", "workspace_clean"},
        "SESSION_CREATED.data",
    )
    if integer_value(data["goal_revision"], "SESSION_CREATED.goal_revision", minimum=1) != 1:
        raise SchemaError("SESSION_CREATED.goal_revision must be 1")
    mode = enum_value(RequestedMode, data["requested_mode"], "SESSION_CREATED.requested_mode")
    digest = sha256_value(data["workspace_digest"], "SESSION_CREATED.workspace_digest")
    clean = boolean_value(data["workspace_clean"], "SESSION_CREATED.workspace_clean")
    baseline = state.workspace.session_baseline
    for field, actual, expected in (
        ("requested_mode", mode, state.project.requested_mode),
        ("workspace_digest", digest, baseline.workspace_digest),
        ("workspace_clean", clean, baseline.clean),
        ("recorded_at", creation.recorded_at, state.created_at),
        ("baseline.captured_at", creation.recorded_at, baseline.captured_at),
    ):
        if actual != expected:
            return f"SESSION_CREATED {field} disagrees with state/baseline"
    return None


def _goal_history_mismatch(
    state: StateSnapshot,
    events: tuple[SessionEventRecord, ...],
) -> str | None:
    try:
        initial = normalize_goal_v1(state.goal.initial)
    except SessionCoordinatorError:
        return "goal.initial is not a valid normalized goal"
    if initial != state.goal.initial:
        return "goal.initial is not normalized"
    if state.session_fingerprint != session_fingerprint_v1(state.project_fingerprint, initial):
        return "goal.initial disagrees with session_fingerprint"

    current, revision, changed_at = initial, 1, state.created_at
    for event in events:
        if event.event_type is not EventType.GOAL_REFINED:
            continue
        data = event.data
        exact_keys(data, {"from", "to", "revision", "reason"}, "GOAL_REFINED.data")
        previous = string_value(data["from"], "GOAL_REFINED.from")
        target = string_value(data["to"], "GOAL_REFINED.to")
        next_revision = integer_value(data["revision"], "GOAL_REFINED.revision", minimum=2)
        string_value(data["reason"], "GOAL_REFINED.reason", empty=True)
        try:
            normalized = normalize_goal_v1(target)
        except SessionCoordinatorError as exc:
            raise SchemaError(f"GOAL_REFINED.to is invalid: {exc}") from exc
        if target != normalized:
            raise SchemaError("GOAL_REFINED.to must be normalized")
        if previous != current or next_revision != revision + 1:
            raise SchemaError(f"GOAL_REFINED chain is discontinuous at event_seq={event.event_seq}")
        if target == current:
            raise SchemaError("GOAL_REFINED must change the current goal")
        current, revision, changed_at = target, next_revision, event.recorded_at

    for field, actual, expected in (
        ("current", state.goal.current, current),
        ("revision", state.goal.revision, revision),
        ("last_changed_at", state.goal.last_changed_at, changed_at),
    ):
        if actual != expected:
            return f"goal.{field} disagrees with GOAL_REFINED history"
    return None


class ProjectSafeRecoveryManager:
    """Inspect durable evidence only after the caller owns the project lease."""

    def __init__(self, lease: ProjectLease) -> None:
        if not lease.held:
            raise RecoveryOperationError("a live ProjectLease is required")
        self.lease = lease
        self.registry = ProjectRegistryStore(lease)

    def _assessment(
        self,
        reason: RecoveryReason,
        message: str,
        *,
        action_id: str | None = None,
        checkpoint_id: str | None = None,
    ) -> RecoveryAssessment:
        return RecoveryAssessment(
            required=True,
            reason=reason,
            message=message,
            interrupted_action_id=action_id,
            workspace_checkpoint_id=checkpoint_id,
        )

    def assess(self, session_id: str) -> RecoveryAssessment:
        store = ProjectSafeSessionStore(self.lease, session_id)

        try:
            state = store.read_state()
        except StorageError as exc:
            return self._assessment(
                RecoveryReason.STATE_CORRUPT,
                f"state.json cannot be trusted: {exc}",
            )
        if state is None:
            return self._assessment(
                RecoveryReason.STATE_CORRUPT,
                "registered/referenced session has no state.json",
            )

        try:
            registry = self.registry.read()
        except RegistryError as exc:
            return self._assessment(
                RecoveryReason.REGISTRY_MISMATCH,
                f"registry cannot be trusted: {exc}",
            )
        if registry is None or session_id not in registry.session_ids:
            return self._assessment(
                RecoveryReason.REGISTRY_MISMATCH,
                "session is not present in project registry",
            )

        expects_active_slot = state.lifecycle.status in {
            LifecycleStatus.ACTIVE,
            LifecycleStatus.WAITING,
        }
        if expects_active_slot and registry.active_session_id != session_id:
            return self._assessment(
                RecoveryReason.REGISTRY_MISMATCH,
                "active/waiting session does not own project active_session_id",
            )
        if not expects_active_slot and registry.active_session_id == session_id:
            return self._assessment(
                RecoveryReason.REGISTRY_MISMATCH,
                "inactive/paused/terminal session still owns project active_session_id",
            )

        try:
            actions = store.read_actions().records
        except TruncatedLastRecordError as exc:
            return self._assessment(
                RecoveryReason.TRUNCATED_LAST_RECORD,
                str(exc),
            )
        except PayloadIntegrityError as exc:
            return self._assessment(
                RecoveryReason.PAYLOAD_INTEGRITY_FAILURE,
                str(exc),
            )
        except JournalCorruptionError as exc:
            return self._assessment(
                RecoveryReason.JOURNAL_CORRUPT,
                str(exc),
            )

        try:
            events = store.read_events().records
        except (TruncatedLastRecordError, JournalCorruptionError) as exc:
            return self._assessment(
                RecoveryReason.EVENT_JOURNAL_CORRUPT,
                str(exc),
            )

        if state.execution.last_event_seq != len(events):
            return self._assessment(
                RecoveryReason.STATE_JOURNAL_MISMATCH,
                f"state last_event_seq={state.execution.last_event_seq}, "
                f"events journal={len(events)}",
            )

        try:
            creation_mismatch = _session_creation_mismatch(state, events)
        except SchemaError as exc:
            return self._assessment(RecoveryReason.EVENT_JOURNAL_CORRUPT, str(exc))
        if creation_mismatch is not None:
            return self._assessment(RecoveryReason.STATE_JOURNAL_MISMATCH, creation_mismatch)

        try:
            goal_mismatch = _goal_history_mismatch(state, events)
        except SchemaError as exc:
            return self._assessment(RecoveryReason.EVENT_JOURNAL_CORRUPT, str(exc))
        if goal_mismatch is not None:
            return self._assessment(RecoveryReason.STATE_JOURNAL_MISMATCH, goal_mismatch)

        try:
            flow_mismatch = flow_history_mismatch(state, events)
        except SchemaError as exc:
            return self._assessment(RecoveryReason.EVENT_JOURNAL_CORRUPT, str(exc))
        if flow_mismatch is not None:
            return self._assessment(RecoveryReason.STATE_JOURNAL_MISMATCH, flow_mismatch)

        checkpoint_ids = {
            state.workspace.session_baseline.checkpoint_id,
            state.workspace.last_verified_checkpoint_id,
        }
        for checkpoint_id in checkpoint_ids:
            try:
                checkpoint = store.read_checkpoint(checkpoint_id)
            except CheckpointIntegrityError as exc:
                return self._assessment(
                    RecoveryReason.CHECKPOINT_INTEGRITY_FAILURE,
                    str(exc),
                    checkpoint_id=checkpoint_id,
                )
            if checkpoint_id == state.workspace.session_baseline.checkpoint_id:
                if (
                    checkpoint.workspace_digest
                    != state.workspace.session_baseline.workspace_digest
                    or checkpoint.head_sha != state.workspace.session_baseline.head_sha
                ):
                    return self._assessment(
                        RecoveryReason.CHECKPOINT_INTEGRITY_FAILURE,
                        "session baseline snapshot disagrees with baseline checkpoint",
                        checkpoint_id=checkpoint_id,
                    )
            if checkpoint_id == state.workspace.last_verified_checkpoint_id:
                if (
                    checkpoint.workspace_digest
                    != state.workspace.workspace_digest_last_verified
                    or checkpoint.head_sha != state.workspace.last_verified_head_sha
                ):
                    return self._assessment(
                        RecoveryReason.CHECKPOINT_INTEGRITY_FAILURE,
                        "last-verified snapshot disagrees with checkpoint",
                        checkpoint_id=checkpoint_id,
                    )

        latest_by_action: dict[str, Any] = {}
        for record in actions:
            latest_by_action[record.action_id] = record

        terminal = [
            record
            for record in latest_by_action.values()
            if record.state in _TERMINAL_ACTION_STATES
        ]
        last_terminal = max(terminal, key=lambda item: item.action_seq, default=None)
        expected_terminal_seq = 0 if last_terminal is None else last_terminal.action_seq
        expected_terminal_id = None if last_terminal is None else last_terminal.action_id
        if state.execution.last_action_seq != expected_terminal_seq:
            return self._assessment(
                RecoveryReason.STATE_JOURNAL_MISMATCH,
                f"state last_action_seq={state.execution.last_action_seq}, "
                f"journal terminal action_seq={expected_terminal_seq}",
            )
        if state.execution.last_terminal_action_id != expected_terminal_id:
            return self._assessment(
                RecoveryReason.STATE_JOURNAL_MISMATCH,
                "state last_terminal_action_id disagrees with action journal",
            )

        pending_id = state.execution.pending_action_id
        if pending_id is not None:
            latest = latest_by_action.get(pending_id)
            if latest is None:
                return self._assessment(
                    RecoveryReason.STATE_JOURNAL_MISMATCH,
                    "pending_action_id is absent from action journal",
                    action_id=pending_id,
                )
            if latest.state in _TERMINAL_ACTION_STATES:
                return self._assessment(
                    RecoveryReason.STATE_JOURNAL_MISMATCH,
                    "pending_action_id already has a terminal journal state",
                    action_id=pending_id,
                )
            if state.active_flow is None or latest.flow_id != state.active_flow.flow_id:
                return self._assessment(
                    RecoveryReason.STATE_JOURNAL_MISMATCH,
                    "pending action does not belong to active flow",
                    action_id=pending_id,
                )
            if (
                latest.workspace_digest_context
                != state.active_flow.workspace_digest_expected_current
            ):
                return self._assessment(
                    RecoveryReason.STATE_JOURNAL_MISMATCH,
                    "pending action workspace context disagrees with active flow",
                    action_id=pending_id,
                )
            return self._assessment(
                RecoveryReason.INTERRUPTED_ACTION,
                f"pending action stopped in {latest.state.value}",
                action_id=pending_id,
            )

        nonterminal = [
            record
            for record in latest_by_action.values()
            if record.state not in _TERMINAL_ACTION_STATES
        ]
        if nonterminal:
            latest = max(nonterminal, key=lambda item: item.action_seq)
            if latest.state in {
                ActionState.EXECUTING,
                ActionState.VERIFYING,
                ActionState.INTERRUPTED,
            }:
                return self._assessment(
                    RecoveryReason.INTERRUPTED_ACTION,
                    f"journal contains unowned {latest.state.value} action",
                    action_id=latest.action_id,
                )
            return self._assessment(
                RecoveryReason.STATE_JOURNAL_MISMATCH,
                f"journal contains unowned {latest.state.value} action",
                action_id=latest.action_id,
            )

        if (
            expects_active_slot
            and state.active_flow is not None
            and state.lifecycle.status in {
                LifecycleStatus.ACTIVE,
                LifecycleStatus.WAITING,
            }
        ):
            try:
                _, current_digest = compute_workspace_digest_v1(self.lease.project_root)
            except WorkspaceError as exc:
                return self._assessment(
                    RecoveryReason.WORKSPACE_DRIFT,
                    f"workspace evidence cannot be stabilized: {exc}",
                )
            expected = state.active_flow.workspace_digest_expected_current
            if current_digest != expected:
                return self._assessment(
                    RecoveryReason.WORKSPACE_DRIFT,
                    f"active flow expected workspace {expected}, observed {current_digest}",
                )

        return RecoveryAssessment.clean()

    def enter_interrupted_recovery(
        self,
        session_id: str,
        assessment: RecoveryAssessment,
        *,
        at: str,
    ):
        """Persist RECOVERY for an interrupted action; never performs rollback/retry."""
        if (
            not assessment.required
            or assessment.reason is not RecoveryReason.INTERRUPTED_ACTION
            or assessment.interrupted_action_id is None
        ):
            raise RecoveryOperationError(
                "enter_interrupted_recovery requires INTERRUPTED_ACTION assessment"
            )

        store = ProjectSafeSessionStore(self.lease, session_id)
        state = store.read_state()
        if state is None:
            raise RecoveryOperationError("session state is missing")
        if (
            state.recovery is not None
            and state.lifecycle.phase.value == "RECOVERY"
            and state.recovery.interrupted_action_id
            == assessment.interrupted_action_id
        ):
            return state

        actions = store.read_actions().records
        same = [
            record
            for record in actions
            if record.action_id == assessment.interrupted_action_id
        ]
        if not same:
            raise RecoveryOperationError("interrupted action is absent from journal")
        latest = same[-1]

        if latest.state in {ActionState.EXECUTING, ActionState.VERIFYING}:
            raw_latest = latest.to_dict()
            effect = dict(raw_latest["effect"])
            effect["status"] = "UNKNOWN"
            interrupted = {
                "action_id": latest.action_id,
                "flow_id": latest.flow_id,
                "attempt_seq": latest.attempt_seq,
                "attempt_id": latest.attempt_id,
                "state": "INTERRUPTED",
                "effect": effect,
                "recorded_at": at,
                "proposal_id": latest.proposal_id,
                "proposal_digest": latest.proposal_digest,
                "action_type": latest.action_type,
                "effect_target_fingerprint": latest.effect_target_fingerprint,
                "risk": latest.risk.value,
                "input": raw_latest["input"],
                "workspace_digest_context": latest.workspace_digest_context,
                "reason": "PROCESS_INTERRUPTED",
            }
            store.append_action(interrupted)
        elif latest.state not in {ActionState.PREPARED, ActionState.INTERRUPTED}:
            raise RecoveryOperationError(
                f"cannot mark recovery from action state {latest.state.value}"
            )

        event = store.append_event(
            {
                "event_type": "RECOVERY_DETECTED",
                "recorded_at": at,
                "data": {
                    "reason": assessment.reason.value,
                    "interrupted_action_id": assessment.interrupted_action_id,
                    "message": assessment.message,
                },
            }
        )
        raw = state.to_dict()
        raw["state_revision"] = state.state_revision + 1
        raw["updated_at"] = at
        raw["lifecycle"] = {
            "status": "WAITING",
            "phase": "RECOVERY",
            "reason": "RECOVERY_REQUIRED",
            "changed_at": at,
        }
        raw["recovery"] = {
            "recovery_id": str(uuid.uuid4()),
            "reason": assessment.reason.value,
            "interrupted_action_id": assessment.interrupted_action_id,
            "detected_at": at,
            "workspace_checkpoint_id": assessment.workspace_checkpoint_id,
        }
        raw["execution"]["pending_action_id"] = assessment.interrupted_action_id
        raw["execution"]["last_event_seq"] = event.event_seq
        next_state = StateSnapshot.from_dict(raw)
        store.write_state(next_state, expected_current_revision=state.state_revision)
        return next_state


__all__ = [
    "ProjectSafeRecoveryManager", "RecoveryAssessment", "RecoveryOperationError",
]
