"""T80: status/reason/timestamp evidence despite valid event counts and hashes."""
from __future__ import annotations

import pytest

from arena.project_safe_session import StateSnapshot
from arena.project_safe_session.recovery import ProjectSafeRecoveryManager
from tests.test_project_safe_session_action_contract import ACTION_ARGS, _draft, _identity
from tests.test_project_safe_session_coordinator import NOW1, NOW2, NOW3, NOW4, _coordinator


def _drafts(store):
    return [{"event_type": event.event_type.value, "recorded_at": event.recorded_at,
             "data": dict(event.data)} for event in store.read_events().records]


def _replace_events(store, state, drafts):
    store.events_path.write_bytes(b"")
    for draft in drafts:
        store.append_event(draft)
    if len(drafts) != state.execution.last_event_seq:
        raw = state.to_dict()
        raw["state_revision"] += 1
        raw["execution"]["last_event_seq"] = len(drafts)
        store.write_state(StateSnapshot.from_dict(raw), expected_current_revision=state.state_revision)
    assert len(store.read_events().records) == store.read_state().execution.last_event_seq


def _assess_without_writes(lease, coordinator, session_id):
    store = coordinator._store(session_id)
    before = {path.relative_to(store.session_dir): path.read_bytes()
              for path in store.session_dir.rglob("*") if path.is_file()}
    registry = coordinator.registry.path.read_bytes()
    result = ProjectSafeRecoveryManager(lease).assess(session_id)
    assert before == {path.relative_to(store.session_dir): path.read_bytes()
                      for path in store.session_dir.rglob("*") if path.is_file()}
    assert registry == coordinator.registry.path.read_bytes()
    return result


@pytest.mark.parametrize("change,reason", [
    ({"from": "PAUSED"}, "EVENT_JOURNAL_CORRUPT"),
    ({"from": True}, "EVENT_JOURNAL_CORRUPT"),
    ({"to": "CREATED"}, "EVENT_JOURNAL_CORRUPT"),
    ({"to": "PAUSED"}, "EVENT_JOURNAL_CORRUPT"),
    ({"to": "unknown"}, "EVENT_JOURNAL_CORRUPT"),
    ({"reason": "WORKSPACE_DIRTY"}, "EVENT_JOURNAL_CORRUPT"),
    ({"reason": False}, "EVENT_JOURNAL_CORRUPT"),
    ({"missing": "from"}, "EVENT_JOURNAL_CORRUPT"),
    ({"extra": "unknown"}, "EVENT_JOURNAL_CORRUPT"),
    ({"recorded_at": NOW3}, "STATE_JOURNAL_MISMATCH"),
    ({"event_type": "BINDING_STALE"}, "STATE_JOURNAL_MISMATCH"),
])
def test_status_evidence_rejects_rehashed_activation(tmp_path, change, reason):
    _, lease, coordinator = _coordinator(tmp_path)
    try:
        state = coordinator.create_session(goal="Inspect", requested_mode="read", at=NOW1)
        state = coordinator.activate_session(state.session_id, at=NOW2)
        store = coordinator._store(state.session_id)
        drafts = _drafts(store)
        for field, value in change.items():
            if field in {"recorded_at", "event_type"}:
                drafts[-1][field] = value
            elif field == "missing":
                drafts[-1]["data"].pop(value)
            elif field == "extra":
                drafts[-1]["data"][value] = "unexpected"
            else:
                drafts[-1]["data"][field] = value
        _replace_events(store, state, drafts)
        result = _assess_without_writes(lease, coordinator, state.session_id)
        assert result.required is True
        assert result.reason.value == reason
    finally:
        lease.release()


@pytest.mark.parametrize("case", ["duplicate_activation", "flow_before_activation", "wrong_pause_from",
                                      "dirty_without_reason", "dirty_wrong_reason", "resolved_without_marker",
                                      "resolved_wrong_from", "read_dirty_wait", "drift_without_flow"])
def test_status_evidence_rejects_impossible_history(tmp_path, case):
    repo, lease, coordinator = _coordinator(tmp_path)
    try:
        mode = "read" if case == "read_dirty_wait" else "write"
        state = coordinator.create_session(goal="Inspect", requested_mode=mode, at=NOW1)
        state = coordinator.activate_session(state.session_id, at=NOW2)
        dirty = repo / "note.txt"
        if case == "flow_before_activation":
            state = coordinator.start_flow(state.session_id, at=NOW3)
        elif case in {"wrong_pause_from", "drift_without_flow"}:
            state = coordinator.pause_session(state.session_id, at=NOW3)
            if case == "drift_without_flow":
                state = coordinator.activate_session(state.session_id, at=NOW4)
        elif case.startswith("dirty_") or case.startswith("resolved_"):
            dirty.write_text("external change", encoding="utf-8")
            state = coordinator.start_flow(state.session_id, at=NOW3)
            if case.startswith("resolved_"):
                dirty.unlink()
                state = coordinator.start_flow(state.session_id, at=NOW4)
        store = coordinator._store(state.session_id)
        drafts = _drafts(store)
        if case == "duplicate_activation":
            drafts.append(dict(drafts[-1]))
        elif case == "flow_before_activation":
            drafts[-1], drafts[-2] = drafts[-2], drafts[-1]
        elif case == "wrong_pause_from":
            drafts[-1]["data"]["from"] = "CREATED"
        elif case == "dirty_without_reason":
            drafts[-1]["data"]["reason"] = None
        elif case == "dirty_wrong_reason":
            drafts[-1]["data"]["reason"] = "NEED_USER"
        elif case == "resolved_without_marker":
            drafts[-2]["data"]["reason"] = None
        elif case == "resolved_wrong_from":
            drafts[-2]["data"]["from"] = "PAUSED"
        elif case == "read_dirty_wait":
            drafts.append({"event_type": "STATUS_CHANGED", "recorded_at": NOW3,
                           "data": {"from": "ACTIVE", "to": "WAITING", "reason": "WORKSPACE_DIRTY"}})
        elif case == "drift_without_flow":
            drafts[-1]["data"].update(to="WAITING", reason="WORKSPACE_DRIFT")
        _replace_events(store, state, drafts)
        result = _assess_without_writes(lease, coordinator, state.session_id)
        assert result.required is True
        assert result.reason.value == "EVENT_JOURNAL_CORRUPT"
    finally:
        lease.release()


@pytest.mark.parametrize("case", ["status", "reason", "activation_time", "flow_time", "close_time", "goal_time"])
def test_status_evidence_rejects_snapshot_mismatch(tmp_path, case):
    repo, lease, coordinator = _coordinator(tmp_path)
    try:
        state = coordinator.create_session(goal="Inspect", requested_mode="write", at=NOW1)
        state = coordinator.activate_session(state.session_id, at=NOW2)
        if case == "reason":
            (repo / "note.txt").write_text("external change", encoding="utf-8")
            state = coordinator.start_flow(state.session_id, at=NOW3)
        elif case in {"flow_time", "close_time", "goal_time"}:
            state = coordinator.start_flow(state.session_id, at=NOW3)
            if case == "close_time":
                state = coordinator.close_flow(state.session_id, at=NOW4)
            elif case == "goal_time":
                state = coordinator.refine_goal(state.session_id, new_goal="New goal", at=NOW4)
        store = coordinator._store(state.session_id)
        raw = state.to_dict()
        raw["state_revision"] += 1
        if case == "status":
            raw["lifecycle"].update(status="WAITING", reason="NEED_USER")
        elif case == "reason":
            raw["lifecycle"]["reason"] = "RATE_LIMIT"
        else:
            raw["lifecycle"]["changed_at"] = NOW1
        store.write_state(StateSnapshot.from_dict(raw), expected_current_revision=state.state_revision)
        result = _assess_without_writes(lease, coordinator, state.session_id)
        assert result.required is True
        assert result.reason.value == "STATE_JOURNAL_MISMATCH"
    finally:
        lease.release()


@pytest.mark.parametrize("case", ["created", "active", "paused_idle", "paused_flow", "resumed_idle",
                                      "resumed_flow", "dirty_wait", "dirty_resolved", "drift_wait",
                                      "closed_paused", "refined_paused"])
def test_status_evidence_accepts_coordinator_history(tmp_path, case):
    repo, lease, coordinator = _coordinator(tmp_path)
    try:
        state = coordinator.create_session(goal="Inspect", requested_mode="write", at=NOW1)
        session_id = state.session_id
        if case != "created":
            coordinator.activate_session(session_id, at=NOW2)
        if case in {"paused_flow", "resumed_flow", "drift_wait", "closed_paused", "refined_paused"}:
            coordinator.start_flow(session_id, at=NOW3)
        if case.startswith("paused_") or case.startswith("resumed_") or case in {
            "drift_wait", "closed_paused", "refined_paused",
        }:
            coordinator.pause_session(session_id, at=NOW4)
        if case == "drift_wait":
            (repo / "note.txt").write_text("external change", encoding="utf-8")
        if case.startswith("resumed_") or case == "drift_wait":
            coordinator.activate_session(session_id, at=NOW4)
        if case.startswith("dirty_"):
            dirty = repo / "note.txt"
            dirty.write_text("external change", encoding="utf-8")
            coordinator.start_flow(session_id, at=NOW3)
            if case == "dirty_resolved":
                dirty.unlink()
                coordinator.start_flow(session_id, at=NOW4)
        if case == "closed_paused":
            coordinator.close_flow(session_id, reason="", at=NOW4)
        elif case == "refined_paused":
            coordinator.refine_goal(session_id, new_goal="New goal", at=NOW4)
        result = _assess_without_writes(lease, coordinator, session_id)
        if case == "drift_wait":
            assert result.reason.value == "WORKSPACE_DRIFT"
        else:
            assert result.required is False
    finally:
        lease.release()


@pytest.mark.parametrize("change", [None, "status", "reason", "changed_at"])
def test_status_evidence_tracks_recovery_detection(tmp_path, change):
    _, lease, coordinator = _coordinator(tmp_path)
    try:
        state = coordinator.create_session(goal="Change foo", requested_mode="write", at=NOW1)
        coordinator.activate_session(state.session_id, at=NOW2)
        state = coordinator.start_flow(state.session_id, at=NOW3)
        store = coordinator._store(state.session_id)
        action_id = _identity(state.session_id)
        payload_ref, _, payload_sha = store.write_action_input(action_id, ACTION_ARGS)
        draft = _draft(store, action_id, payload_ref, payload_sha, state.active_flow.flow_id,
                       state="PREPARED", effect="NONE")
        draft["workspace_digest_context"] = state.active_flow.workspace_digest_expected_current
        store.append_action(draft)
        raw = state.to_dict()
        raw["state_revision"] += 1
        raw["execution"]["pending_action_id"] = action_id
        store.write_state(StateSnapshot.from_dict(raw), expected_current_revision=state.state_revision)
        manager = ProjectSafeRecoveryManager(lease)
        state = manager.enter_interrupted_recovery(state.session_id, manager.assess(state.session_id), at=NOW4)
        if change is not None:
            raw = state.to_dict()
            raw["state_revision"] += 1
            if change == "status":
                raw["lifecycle"].update(status="ACTIVE", phase="PLANNING", reason=None)
                raw["recovery"] = None
            elif change == "reason":
                raw["lifecycle"]["reason"] = "NEED_USER"
            else:
                raw["lifecycle"]["changed_at"] = NOW1
            store.write_state(StateSnapshot.from_dict(raw), expected_current_revision=state.state_revision)
        result = _assess_without_writes(lease, coordinator, state.session_id)
        assert result.reason.value == ("INTERRUPTED_ACTION" if change is None else "STATE_JOURNAL_MISMATCH")
    finally:
        lease.release()
