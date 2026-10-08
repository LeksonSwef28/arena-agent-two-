"""T73: import and minimal fail-closed project-safe recovery regressions."""
from __future__ import annotations

import uuid

import pytest

import arena.project_safe_session.recovery as recovery_module
from arena.project_safe_session import StateSnapshot
from arena.project_safe_session.flow_evidence import flow_history_mismatch
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


@pytest.mark.parametrize("change,reason", [
    ({"event_type": "PHASE_CHANGED"}, "EVENT_JOURNAL_CORRUPT"),
    ({"goal_revision": True}, "EVENT_JOURNAL_CORRUPT"),
    ({"goal_revision": 2}, "EVENT_JOURNAL_CORRUPT"),
    ({"requested_mode": "invalid"}, "EVENT_JOURNAL_CORRUPT"),
    ({"missing": "workspace_digest"}, "EVENT_JOURNAL_CORRUPT"),
    ({"extra": "unknown_field"}, "EVENT_JOURNAL_CORRUPT"),
    ({"requested_mode": "write"}, "STATE_JOURNAL_MISMATCH"),
    ({"workspace_digest": "a" * 64}, "STATE_JOURNAL_MISMATCH"),
    ({"workspace_clean": False}, "STATE_JOURNAL_MISMATCH"),
    ({"recorded_at": NOW2}, "STATE_JOURNAL_MISMATCH"),
])
def test_recovery_rejects_rehashed_creation_evidence_without_writes(tmp_path, change, reason):
    _, lease, coordinator = _coordinator(tmp_path)
    try:
        state = coordinator.create_session(goal="Inspect", requested_mode="read", at=NOW1)
        store = coordinator._store(state.session_id)
        event = store.read_events().records[0]
        draft = {
            "event_type": event.event_type.value, "recorded_at": event.recorded_at,
            "data": dict(event.data),
        }
        for field, value in change.items():
            if field in {"event_type", "recorded_at"}:
                draft[field] = value
            elif field == "missing":
                draft["data"].pop(value)
            elif field == "extra":
                draft["data"][value] = "unexpected"
            else:
                draft["data"][field] = value
        # Republish through the real writer so count, sequence and hashes are valid.
        store.events_path.write_bytes(b"")
        store.append_event(draft)
        assert len(store.read_events().records) == state.execution.last_event_seq
        before = (store.state_path.read_bytes(), store.events_path.read_bytes(), coordinator.registry.path.read_bytes())

        assessment = ProjectSafeRecoveryManager(lease).assess(state.session_id)

        assert assessment.required is True
        assert assessment.reason.value == reason
        assert "SESSION_CREATED" in assessment.message
        assert before == (store.state_path.read_bytes(), store.events_path.read_bytes(), coordinator.registry.path.read_bytes())
    finally:
        lease.release()


def test_recovery_rejects_duplicate_creation_even_when_event_count_matches(tmp_path):
    _, lease, coordinator = _coordinator(tmp_path)
    try:
        state = coordinator.create_session(goal="Inspect", requested_mode="read", at=NOW1)
        store = coordinator._store(state.session_id)
        first = store.read_events().records[0]
        event = store.append_event({
            "event_type": "SESSION_CREATED", "recorded_at": NOW2, "data": dict(first.data),
        })
        raw = state.to_dict()
        raw["state_revision"] += 1
        raw["execution"]["last_event_seq"] = event.event_seq
        store.write_state(StateSnapshot.from_dict(raw), expected_current_revision=state.state_revision)
        before = (store.state_path.read_bytes(), store.events_path.read_bytes(), coordinator.registry.path.read_bytes())

        assessment = ProjectSafeRecoveryManager(lease).assess(state.session_id)

        assert assessment.required is True
        assert assessment.reason.value == "EVENT_JOURNAL_CORRUPT"
        assert "SESSION_CREATED" in assessment.message
        assert before == (store.state_path.read_bytes(), store.events_path.read_bytes(), coordinator.registry.path.read_bytes())
    finally:
        lease.release()


@pytest.mark.parametrize("transition", ["ACTIVE", "PAUSED", "REFINED"])
def test_creation_evidence_remains_valid_after_normal_transitions(tmp_path, transition):
    _, lease, coordinator = _coordinator(tmp_path)
    try:
        state = coordinator.create_session(goal="Inspect", requested_mode="read", at=NOW1)
        coordinator.activate_session(state.session_id, at=NOW2)
        coordinator.start_flow(state.session_id, at=NOW3)
        if transition == "PAUSED":
            coordinator.pause_session(state.session_id, at=NOW4)
        elif transition == "REFINED":
            coordinator.refine_goal(state.session_id, new_goal="Inspect again", at=NOW4)
        store = coordinator._store(state.session_id)
        before = (store.state_path.read_bytes(), store.events_path.read_bytes(), coordinator.registry.path.read_bytes())
        assert ProjectSafeRecoveryManager(lease).assess(state.session_id).required is False
        assert before == (store.state_path.read_bytes(), store.events_path.read_bytes(), coordinator.registry.path.read_bytes())
    finally:
        lease.release()


@pytest.mark.parametrize("case,reason", [
    ("missing_creation", "EVENT_JOURNAL_CORRUPT"),
    ("baseline_timestamp", "STATE_JOURNAL_MISMATCH"),
])
def test_recovery_rejects_creation_snapshot_gaps(tmp_path, case, reason):
    _, lease, coordinator = _coordinator(tmp_path)
    try:
        state = coordinator.create_session(goal="Inspect", requested_mode="read", at=NOW1)
        store = coordinator._store(state.session_id)
        raw = state.to_dict()
        raw["state_revision"] += 1
        if case == "missing_creation":
            store.events_path.write_bytes(b"")
            raw["execution"]["last_event_seq"] = 0
        else:
            raw["workspace"]["session_baseline"]["captured_at"] = NOW2
        store.write_state(StateSnapshot.from_dict(raw), expected_current_revision=state.state_revision)
        before = (store.state_path.read_bytes(), store.events_path.read_bytes(), coordinator.registry.path.read_bytes())
        assessment = ProjectSafeRecoveryManager(lease).assess(state.session_id)
        assert assessment.required is True
        assert assessment.reason.value == reason
        assert "SESSION_CREATED" in assessment.message
        assert before == (store.state_path.read_bytes(), store.events_path.read_bytes(), coordinator.registry.path.read_bytes())
    finally:
        lease.release()


@pytest.mark.parametrize("change,reason", [
    ({"revision": True}, "EVENT_JOURNAL_CORRUPT"),
    ({"revision": 1}, "EVENT_JOURNAL_CORRUPT"),
    ({"revision": 3}, "EVENT_JOURNAL_CORRUPT"),
    ({"from": "Wrong goal"}, "EVENT_JOURNAL_CORRUPT"),
    ({"to": "Inspect"}, "EVENT_JOURNAL_CORRUPT"),
    ({"to": None}, "EVENT_JOURNAL_CORRUPT"),
    ({"to": "   "}, "EVENT_JOURNAL_CORRUPT"),
    ({"to": "  New   goal "}, "EVENT_JOURNAL_CORRUPT"),
    ({"reason": False}, "EVENT_JOURNAL_CORRUPT"),
    ({"missing": "revision"}, "EVENT_JOURNAL_CORRUPT"),
    ({"extra": "unknown_field"}, "EVENT_JOURNAL_CORRUPT"),
    ({"to": "Other goal"}, "STATE_JOURNAL_MISMATCH"),
    ({"event_type": "PHASE_CHANGED"}, "STATE_JOURNAL_MISMATCH"),
])
def test_recovery_rejects_rehashed_goal_evidence(tmp_path, change, reason):
    _, lease, coordinator = _coordinator(tmp_path)
    try:
        state = coordinator.create_session(goal="Inspect", requested_mode="read", at=NOW1)
        state = coordinator.refine_goal(state.session_id, new_goal="New goal", at=NOW2)
        store = coordinator._store(state.session_id)
        records = store.read_events().records
        drafts = [{"event_type": event.event_type.value, "recorded_at": event.recorded_at,
                   "data": dict(event.data)} for event in records]
        for field, value in change.items():
            if field == "event_type":
                drafts[-1][field] = value
            elif field == "missing":
                drafts[-1]["data"].pop(value)
            elif field == "extra":
                drafts[-1]["data"][value] = "unexpected"
            else:
                drafts[-1]["data"][field] = value
        store.events_path.write_bytes(b"")
        for draft in drafts:
            store.append_event(draft)
        assert len(store.read_events().records) == state.execution.last_event_seq
        before = (store.state_path.read_bytes(), store.events_path.read_bytes(), coordinator.registry.path.read_bytes())
        assessment = ProjectSafeRecoveryManager(lease).assess(state.session_id)
        assert assessment.required is True
        assert assessment.reason.value == reason
        assert before == (store.state_path.read_bytes(), store.events_path.read_bytes(), coordinator.registry.path.read_bytes())
    finally:
        lease.release()


@pytest.mark.parametrize("field,value", [
    ("current", "Other goal"), ("revision", 3), ("last_changed_at", NOW3),
    ("initial", "Different initial goal"), ("session_fingerprint", "a" * 64),
])
def test_recovery_rejects_goal_snapshot_mismatch(tmp_path, field, value):
    _, lease, coordinator = _coordinator(tmp_path)
    try:
        state = coordinator.create_session(goal="Inspect", requested_mode="read", at=NOW1)
        state = coordinator.refine_goal(state.session_id, new_goal="New goal", at=NOW2)
        store = coordinator._store(state.session_id)
        raw = state.to_dict()
        raw["state_revision"] += 1
        if field == "session_fingerprint":
            raw[field] = value
        else:
            raw["goal"][field] = value
        store.write_state(StateSnapshot.from_dict(raw), expected_current_revision=state.state_revision)
        before = (store.state_path.read_bytes(), store.events_path.read_bytes(), coordinator.registry.path.read_bytes())
        assessment = ProjectSafeRecoveryManager(lease).assess(state.session_id)
        assert assessment.required is True
        assert assessment.reason.value == "STATE_JOURNAL_MISMATCH"
        assert before == (store.state_path.read_bytes(), store.events_path.read_bytes(), coordinator.registry.path.read_bytes())
    finally:
        lease.release()


@pytest.mark.parametrize("case", ["consecutive", "noop", "interleaved"])
def test_goal_replay_accepts_normal_refinement_history(tmp_path, case):
    _, lease, coordinator = _coordinator(tmp_path)
    try:
        state = coordinator.create_session(goal="  Inspect  ", requested_mode="read", at=NOW1)
        if case == "noop":
            assert coordinator.refine_goal(state.session_id, new_goal=" Inspect ", at=NOW2) == state
        else:
            state = coordinator.refine_goal(state.session_id, new_goal=" Cafe\u0301   inspect ", reason="", at=NOW2)
            assert state.goal.current == "Café inspect"
            if case == "interleaved":
                coordinator.activate_session(state.session_id, at=NOW3)
                coordinator.start_flow(state.session_id, at=NOW3)
            state = coordinator.refine_goal(state.session_id, new_goal="Inspect", at=NOW4)
            assert state.goal.revision == 3
        store = coordinator._store(state.session_id)
        before = (store.state_path.read_bytes(), store.events_path.read_bytes(), coordinator.registry.path.read_bytes())
        assert ProjectSafeRecoveryManager(lease).assess(state.session_id).required is False
        assert before == (store.state_path.read_bytes(), store.events_path.read_bytes(), coordinator.registry.path.read_bytes())
    finally:
        lease.release()


@pytest.mark.parametrize("change,reason", [
    ({"flow_id": "invalid"}, "EVENT_JOURNAL_CORRUPT"),
    ({"goal_revision": True}, "EVENT_JOURNAL_CORRUPT"),
    ({"goal_revision": 2}, "EVENT_JOURNAL_CORRUPT"),
    ({"workspace_digest_expected_current": "a" * 64}, "EVENT_JOURNAL_CORRUPT"),
    ({"workspace_digest_baseline": "a" * 64, "workspace_digest_expected_current": "a" * 64}, "STATE_JOURNAL_MISMATCH"),
    ({"workspace_clean": 1}, "EVENT_JOURNAL_CORRUPT"),
    ({"workspace_clean": False}, "EVENT_JOURNAL_CORRUPT"),
    ({"head_sha": "invalid"}, "EVENT_JOURNAL_CORRUPT"),
    ({"recorded_at": NOW4}, "STATE_JOURNAL_MISMATCH"),
    ({"missing": "head_sha"}, "EVENT_JOURNAL_CORRUPT"),
    ({"extra": "unknown_field"}, "EVENT_JOURNAL_CORRUPT"),
])
def test_recovery_rejects_rehashed_flow_creation(tmp_path, change, reason):
    _, lease, coordinator = _coordinator(tmp_path)
    try:
        state = coordinator.create_session(goal="Inspect", requested_mode="write", at=NOW1)
        coordinator.activate_session(state.session_id, at=NOW2)
        state = coordinator.start_flow(state.session_id, at=NOW3)
        store = coordinator._store(state.session_id)
        drafts = [{"event_type": event.event_type.value, "recorded_at": event.recorded_at,
                   "data": dict(event.data)} for event in store.read_events().records]
        for field, value in change.items():
            if field == "recorded_at":
                drafts[-1][field] = value
            elif field == "missing":
                drafts[-1]["data"].pop(value)
            elif field == "extra":
                drafts[-1]["data"][value] = "unexpected"
            else:
                drafts[-1]["data"][field] = value
        store.events_path.write_bytes(b"")
        for draft in drafts:
            store.append_event(draft)
        before = (store.state_path.read_bytes(), store.events_path.read_bytes(), coordinator.registry.path.read_bytes())
        assessment = ProjectSafeRecoveryManager(lease).assess(state.session_id)
        assert assessment.required is True
        assert assessment.reason.value == reason
        assert before == (store.state_path.read_bytes(), store.events_path.read_bytes(), coordinator.registry.path.read_bytes())
    finally:
        lease.release()


@pytest.mark.parametrize("field,value", [
    ("flow_id", None),
    ("workspace_digest_baseline", "a" * 64), ("created_at", NOW4),
])
def test_recovery_rejects_immutable_flow_snapshot_mismatch(tmp_path, field, value):
    _, lease, coordinator = _coordinator(tmp_path)
    try:
        state = coordinator.create_session(goal="Inspect", requested_mode="read", at=NOW1)
        coordinator.activate_session(state.session_id, at=NOW2)
        state = coordinator.start_flow(state.session_id, at=NOW3)
        store = coordinator._store(state.session_id)
        raw = state.to_dict()
        raw["state_revision"] += 1
        raw["active_flow"][field] = str(uuid.uuid4()) if field == "flow_id" else value
        store.write_state(StateSnapshot.from_dict(raw), expected_current_revision=state.state_revision)
        before = (store.state_path.read_bytes(), store.events_path.read_bytes(), coordinator.registry.path.read_bytes())
        assessment = ProjectSafeRecoveryManager(lease).assess(state.session_id)
        assert assessment.required is True
        assert assessment.reason.value == "STATE_JOURNAL_MISMATCH"
        assert before == (store.state_path.read_bytes(), store.events_path.read_bytes(), coordinator.registry.path.read_bytes())
    finally:
        lease.release()


@pytest.mark.parametrize("case,reason", [
    ("overlap", "EVENT_JOURNAL_CORRUPT"), ("reused_id", "EVENT_JOURNAL_CORRUPT"),
    ("wrong_close", "EVENT_JOURNAL_CORRUPT"), ("duplicate_close", "EVENT_JOURNAL_CORRUPT"),
    ("invalid_close_reason", "EVENT_JOURNAL_CORRUPT"),
    ("missing_create", "STATE_JOURNAL_MISMATCH"), ("missing_close", "STATE_JOURNAL_MISMATCH"),
    ("goal_without_close", "EVENT_JOURNAL_CORRUPT"),
])
def test_recovery_rejects_inconsistent_flow_lineage(tmp_path, case, reason):
    _, lease, coordinator = _coordinator(tmp_path)
    try:
        state = coordinator.create_session(goal="Inspect", requested_mode="read", at=NOW1)
        coordinator.activate_session(state.session_id, at=NOW2)
        state = coordinator.start_flow(state.session_id, at=NOW3)
        first_id = state.active_flow.flow_id
        if case in {"reused_id", "wrong_close", "duplicate_close", "invalid_close_reason", "missing_close"}:
            state = coordinator.close_flow(state.session_id, at=NOW4)
        if case == "reused_id":
            state = coordinator.start_flow(state.session_id, at=NOW4)
        elif case == "goal_without_close":
            state = coordinator.refine_goal(state.session_id, new_goal="New goal", at=NOW4)
        store = coordinator._store(state.session_id)
        drafts = [{"event_type": event.event_type.value, "recorded_at": event.recorded_at,
                   "data": dict(event.data)} for event in store.read_events().records]
        if case in {"overlap", "duplicate_close"}:
            drafts.append(dict(drafts[-1]))
        elif case == "reused_id":
            drafts[-1]["data"]["flow_id"] = first_id
        elif case == "wrong_close":
            drafts[-1]["data"]["flow_id"] = str(uuid.uuid4())
        elif case == "invalid_close_reason":
            drafts[-1]["data"]["reason"] = False
        elif case == "goal_without_close":
            drafts[-2]["event_type"] = "PHASE_CHANGED"
        else:
            drafts[-1]["event_type"] = "PHASE_CHANGED"
        store.events_path.write_bytes(b"")
        for draft in drafts:
            store.append_event(draft)
        raw = state.to_dict()
        raw["state_revision"] += 1
        raw["execution"]["last_event_seq"] = len(drafts)
        store.write_state(StateSnapshot.from_dict(raw), expected_current_revision=state.state_revision)
        before = (store.state_path.read_bytes(), store.events_path.read_bytes(), coordinator.registry.path.read_bytes())
        assessment = ProjectSafeRecoveryManager(lease).assess(state.session_id)
        assert assessment.required is True
        assert assessment.reason.value == reason
        assert before == (store.state_path.read_bytes(), store.events_path.read_bytes(), coordinator.registry.path.read_bytes())
    finally:
        lease.release()


@pytest.mark.parametrize("case", ["open", "closed", "paused", "refined", "two_flows", "dirty_read"])
def test_flow_replay_accepts_normal_lifecycle(tmp_path, case):
    repo, lease, coordinator = _coordinator(tmp_path)
    try:
        if case == "dirty_read":
            (repo / "user-note.txt").write_text("user change", encoding="utf-8")
        state = coordinator.create_session(goal="Inspect", requested_mode="read", at=NOW1)
        coordinator.activate_session(state.session_id, at=NOW2)
        coordinator.start_flow(state.session_id, at=NOW3)
        if case in {"closed", "two_flows"}:
            coordinator.close_flow(state.session_id, reason="", at=NOW4)
        if case == "two_flows":
            coordinator.start_flow(state.session_id, at=NOW4)
        elif case == "paused":
            coordinator.pause_session(state.session_id, at=NOW4)
        elif case == "refined":
            coordinator.refine_goal(state.session_id, new_goal="New goal", at=NOW4)
        store = coordinator._store(state.session_id)
        before = (store.state_path.read_bytes(), store.events_path.read_bytes(), coordinator.registry.path.read_bytes())
        assert ProjectSafeRecoveryManager(lease).assess(state.session_id).required is False
        assert before == (store.state_path.read_bytes(), store.events_path.read_bytes(), coordinator.registry.path.read_bytes())
    finally:
        lease.release()



def test_flow_evidence_allows_mutable_expected_digest_progression(tmp_path):
    _, lease, coordinator = _coordinator(tmp_path)
    try:
        state = coordinator.create_session(goal="Inspect", requested_mode="read", at=NOW1)
        coordinator.activate_session(state.session_id, at=NOW2)
        state = coordinator.start_flow(state.session_id, at=NOW3)
        store = coordinator._store(state.session_id)
        raw = state.to_dict()
        raw["active_flow"]["workspace_digest_expected_current"] = "a" * 64
        # Action/workspace evidence owns this mutable field; this guard owns flow lineage.
        assert flow_history_mismatch(
            StateSnapshot.from_dict(raw), store.read_events().records,
        ) is None
    finally:
        lease.release()
