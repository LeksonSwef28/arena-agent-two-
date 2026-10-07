"""T73: import and minimal fail-closed project-safe recovery regressions."""
from __future__ import annotations

import pytest

import arena.project_safe_session.recovery as recovery_module
from arena.project_safe_session import StateSnapshot
from arena.project_safe_session.recovery import (
    ProjectSafeRecoveryManager,
    RecoveryOperationError,
)
from arena.project_safe_session.storage import LeaseRequiredError
from arena.project_safe_session.workspace import WorkspaceError, WorkspaceUnstableError
from tests.test_project_safe_session_action_contract import ACTION_ARGS, _draft, _identity
from tests.test_project_safe_session_coordinator import NOW1, NOW2, NOW3, NOW4, _coordinator


def test_recovery_import_and_clean_session_assessment_are_read_only(tmp_path):
    _, lease, coordinator = _coordinator(tmp_path)
    try:
        state = coordinator.create_session(goal="Inspect", requested_mode="read", at=NOW1)
        store = coordinator._store(state.session_id)
        before = (store.state_path.read_bytes(), store.events_path.read_bytes())

        assessment = ProjectSafeRecoveryManager(lease).assess(state.session_id)

        assert assessment.required is False
        assert assessment.reason is None
        assert before == (store.state_path.read_bytes(), store.events_path.read_bytes())
    finally:
        lease.release()


@pytest.mark.parametrize("error_type", [WorkspaceError, WorkspaceUnstableError])
def test_workspace_evidence_errors_require_recovery_without_writes(tmp_path, monkeypatch, error_type):
    _, lease, coordinator = _coordinator(tmp_path)
    try:
        state = coordinator.create_session(goal="Inspect", requested_mode="read", at=NOW1)
        coordinator.activate_session(state.session_id, at=NOW2)
        coordinator.start_flow(state.session_id, at=NOW3)
        store = coordinator._store(state.session_id)
        before = (store.state_path.read_bytes(), store.events_path.read_bytes())

        def unstable_workspace(_root):
            raise error_type("injected workspace evidence failure")

        monkeypatch.setattr(recovery_module, "compute_workspace_digest_v1", unstable_workspace)
        assessment = ProjectSafeRecoveryManager(lease).assess(state.session_id)

        assert assessment.required is True
        assert assessment.reason.value == "WORKSPACE_DRIFT"
        assert "injected workspace evidence failure" in assessment.message
        assert before == (store.state_path.read_bytes(), store.events_path.read_bytes())
        assert not store.actions_path.exists()
    finally:
        lease.release()


@pytest.mark.parametrize("interrupted_state", ["EXECUTING", "VERIFYING"])
def test_interrupted_marker_is_unknown_idempotent_and_does_not_retry(tmp_path, interrupted_state):
    repo, lease, coordinator = _coordinator(tmp_path)
    try:
        state = coordinator.create_session(goal="Change foo", requested_mode="write", at=NOW1)
        coordinator.activate_session(state.session_id, at=NOW2)
        state = coordinator.start_flow(state.session_id, at=NOW3)
        store = coordinator._store(state.session_id)
        action_id = _identity(state.session_id)
        payload_ref, _, payload_sha = store.write_action_input(action_id, ACTION_ARGS)
        states = ["PREPARED", "EXECUTING"]
        if interrupted_state == "VERIFYING":
            states.append("VERIFYING")
        for action_state in states:
            draft = _draft(
                store, action_id, payload_ref, payload_sha, state.active_flow.flow_id,
                state=action_state, effect="NONE",
            )
            draft["workspace_digest_context"] = state.active_flow.workspace_digest_expected_current
            store.append_action(draft)
        raw = state.to_dict()
        raw["state_revision"] += 1
        raw["execution"]["pending_action_id"] = action_id
        raw["lifecycle"]["phase"] = interrupted_state
        store.write_state(StateSnapshot.from_dict(raw), expected_current_revision=state.state_revision)
        before_bytes = (repo / "foo.py").read_bytes()
        manager = ProjectSafeRecoveryManager(lease)

        assessment = manager.assess(state.session_id)
        assert assessment.reason.value == "INTERRUPTED_ACTION"
        recovered = manager.enter_interrupted_recovery(state.session_id, assessment, at=NOW4)

        records = store.read_actions().records
        assert len(records) == len(states) + 1
        assert records[-1].state.value == "INTERRUPTED"
        assert records[-1].effect.status.value == "UNKNOWN"
        assert records[-1].attempt_seq == 1
        assert recovered.lifecycle.status.value == "WAITING"
        assert recovered.lifecycle.phase.value == "RECOVERY"
        assert recovered.lifecycle.reason.value == "RECOVERY_REQUIRED"
        assert store.read_events().records[-1].event_type.value == "RECOVERY_DETECTED"
        after = (store.state_path.read_bytes(), store.events_path.read_bytes(), store.actions_path.read_bytes())
        assert manager.enter_interrupted_recovery(state.session_id, assessment, at=NOW4) == recovered
        assert after == (store.state_path.read_bytes(), store.events_path.read_bytes(), store.actions_path.read_bytes())
        assert (repo / "foo.py").read_bytes() == before_bytes
    finally:
        lease.release()


def test_recovery_requires_live_lease_for_construction_and_later_reads(tmp_path):
    _, lease, coordinator = _coordinator(tmp_path)
    state = coordinator.create_session(goal="Inspect", requested_mode="read", at=NOW1)
    manager = ProjectSafeRecoveryManager(lease)
    lease.release()

    with pytest.raises(RecoveryOperationError, match="live ProjectLease"):
        ProjectSafeRecoveryManager(lease)
    with pytest.raises(LeaseRequiredError, match="live ProjectLease"):
        manager.assess(state.session_id)
