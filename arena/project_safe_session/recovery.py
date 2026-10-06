"""Fail-closed recovery assessment and interrupted-action marking for P1-B2."""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from .lease import ProjectLease
from .models import StateSnapshot
from .registry import ProjectRegistryStore, RegistryError
from .schema_types import (
    ActionState,
    LifecycleStatus,
    RecoveryReason,
)
from .storage import (
    CheckpointIntegrityError,
    JournalCorruptionError,
    PayloadIntegrityError,
    ProjectSafeSessionStore,
    StorageError,
    TruncatedLastRecordError,
)
from .workspace import WorkspaceEvidenceError, compute_workspace_digest_v1


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
            except WorkspaceEvidenceError as exc:
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
