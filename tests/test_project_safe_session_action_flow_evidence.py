"""T82: cross-file action/flow provenance and verified workspace checkpoint evidence."""
from __future__ import annotations

import hashlib
import uuid

import pytest

from arena.project_safe_session import (
    FileResourceBefore,
    StateSnapshot,
    build_checkpoint_manifest,
    checkpoint_resource_from_file_before,
)
from arena.project_safe_session.workspace import compute_workspace_digest_v1
from tests.test_project_safe_session_action_contract import ACTION_ARGS, SHA_C, _draft, _identity
from tests.test_project_safe_session_coordinator import NOW1, NOW2, NOW3, NOW4, _coordinator, _git
from tests.test_project_safe_session_lifecycle_evidence import _assess_without_writes


@pytest.fixture
def action_flow_session(tmp_path):
    repo, lease, coordinator = _coordinator(tmp_path)
    try:
        state = coordinator.create_session(goal="Change foo", requested_mode="write", at=NOW1)
        coordinator.activate_session(state.session_id, at=NOW2)
        state = coordinator.start_flow(state.session_id, at=NOW3)
        yield repo, lease, coordinator, state, coordinator._store(state.session_id)
    finally:
        lease.release()


def _write_state(store, state, raw):
    raw["state_revision"] = state.state_revision + 1
    updated = StateSnapshot.from_dict(raw)
    store.write_state(updated, expected_current_revision=state.state_revision)
    return updated


def _append_action(store, state, *, last="FAILED", target=SHA_C, flow_id=None, context=None):
    action_id = _identity(state.session_id, target=target)
    payload_ref, _, payload_sha = store.write_action_input(action_id, ACTION_ARGS)
    states = ["PREPARED"]
    if last == "SUCCEEDED":
        states.extend(["EXECUTING", "VERIFYING", "SUCCEEDED"])
    elif last != "PREPARED":
        states.append(last)
    for action_state in states:
        reason = "injected failure" if action_state == "FAILED" else None
        if action_state == "ABANDONED":
            reason = "USER_CANCELLED"
        draft = _draft(store, action_id, payload_ref, payload_sha, flow_id or state.active_flow.flow_id,
                       state=action_state, effect="EXPECTED" if action_state == "SUCCEEDED" else "NONE",
                       reason=reason)
        draft["effect_target_fingerprint"] = target
        draft["workspace_digest_context"] = context or state.active_flow.workspace_digest_expected_current
        record = store.append_action(draft)
    raw = state.to_dict()
    if last == "PREPARED":
        raw["execution"]["pending_action_id"] = action_id
    else:
        raw["execution"].update(pending_action_id=None, last_terminal_action_id=action_id,
                                 last_action_seq=record.action_seq)
    return _write_state(store, state, raw), action_id


def _checkpoint(store, state, repo, action_id, *, kind="RESOURCE_AFTER"):
    data = (repo / "foo.py").read_bytes()
    before = FileResourceBefore.from_dict({
        "project_fingerprint": state.project_fingerprint, "canonical_relative_path": "foo.py",
        "resource_type": "file", "exists": True, "content_sha256": hashlib.sha256(data).hexdigest(),
    })
    resource = checkpoint_resource_from_file_before(before, data)
    workspace, digest = compute_workspace_digest_v1(repo)
    manifest = build_checkpoint_manifest(
        checkpoint_id=str(uuid.uuid4()), session_id=state.session_id, action_id=action_id,
        kind=kind, created_at=NOW4, workspace_digest=digest, head_sha=workspace.head_sha,
        resources=[resource],
    )
    assert resource.backup_ref is not None
    store.write_checkpoint(manifest, {resource.backup_ref: data})
    return manifest


def _publish_verified(store, state, checkpoint, *, expected=None):
    raw = state.to_dict()
    raw["workspace"].update(
        workspace_digest_last_verified=checkpoint.workspace_digest,
        last_verified_checkpoint_id=checkpoint.checkpoint_id,
        last_verified_head_sha=checkpoint.head_sha, last_verified_at=NOW4,
    )
    if raw["active_flow"] is not None:
        raw["active_flow"]["workspace_digest_expected_current"] = expected or checkpoint.workspace_digest
    return _write_state(store, state, raw)


@pytest.mark.parametrize("last", ["FAILED", "ABANDONED", "SUCCEEDED"])
def test_action_flow_evidence_rejects_unknown_terminal_flow(action_flow_session, last):
    _, lease, coordinator, state, store = action_flow_session
    state, _ = _append_action(store, state, last=last, flow_id=str(uuid.uuid4()))
    result = _assess_without_writes(lease, coordinator, state.session_id)
    assert result.required is True
    assert result.reason.value == "STATE_JOURNAL_MISMATCH"


@pytest.mark.parametrize("last", ["FAILED", "ABANDONED", "PREPARED"])
def test_action_flow_evidence_rejects_context_before_first_success(action_flow_session, last):
    _, lease, coordinator, state, store = action_flow_session
    state, _ = _append_action(store, state, last=last, context="b" * 64)
    if last == "PREPARED":
        raw = state.to_dict()
        raw["active_flow"]["workspace_digest_expected_current"] = "b" * 64
        state = _write_state(store, state, raw)
    result = _assess_without_writes(lease, coordinator, state.session_id)
    assert result.reason.value == "STATE_JOURNAL_MISMATCH"


@pytest.mark.parametrize("case", ["active", "paused", "failed", "abandoned"])
def test_action_flow_evidence_rejects_unearned_expected_digest(action_flow_session, case):
    repo, lease, coordinator, state, store = action_flow_session
    if case in {"failed", "abandoned"}:
        state, _ = _append_action(store, state, last=case.upper())
    if case == "paused":
        state = coordinator.pause_session(state.session_id, at=NOW4)
    (repo / "foo.py").write_text("foo = 2\n", encoding="utf-8", newline="")
    _, digest = compute_workspace_digest_v1(repo)
    raw = state.to_dict()
    raw["active_flow"]["workspace_digest_expected_current"] = digest
    state = _write_state(store, state, raw)
    result = _assess_without_writes(lease, coordinator, state.session_id)
    assert result.required is True
    assert result.reason.value == "STATE_JOURNAL_MISMATCH"


@pytest.mark.parametrize("case", ["no_actions", "failed", "abandoned"])
def test_action_flow_evidence_rejects_verification_without_success(action_flow_session, case):
    repo, lease, coordinator, state, store = action_flow_session
    action_id = "b" * 64
    if case != "no_actions":
        state, action_id = _append_action(store, state, last=case.upper())
    checkpoint = _checkpoint(store, state, repo, action_id)
    state = _publish_verified(store, state, checkpoint)
    result = _assess_without_writes(lease, coordinator, state.session_id)
    assert result.required is True
    assert result.reason.value == "STATE_JOURNAL_MISMATCH"


@pytest.mark.parametrize("case", ["baseline", "before", "wrong_action", "stale_success", "wrong_expected"])
def test_action_flow_evidence_rejects_unbound_success_checkpoint(action_flow_session, case):
    repo, lease, coordinator, state, store = action_flow_session
    state, action_id = _append_action(store, state, last="SUCCEEDED")
    if case != "baseline":
        owner = "b" * 64 if case == "wrong_action" else action_id
        checkpoint = _checkpoint(store, state, repo, owner,
                                 kind="RESOURCE_BEFORE" if case == "before" else "RESOURCE_AFTER")
        state = _publish_verified(store, state, checkpoint,
                                  expected="b" * 64 if case == "wrong_expected" else None)
    if case == "stale_success":
        state, _ = _append_action(store, state, last="SUCCEEDED", target="d" * 64)
    result = _assess_without_writes(lease, coordinator, state.session_id)
    assert result.required is True
    assert result.reason.value == "STATE_JOURNAL_MISMATCH"


@pytest.mark.parametrize("case", ["mutated", "closed", "new_flow", "pending_next", "failed_next", "two_successes"])
def test_action_flow_evidence_accepts_verified_progression(action_flow_session, case):
    repo, lease, coordinator, state, store = action_flow_session
    state, action_id = _append_action(store, state, last="SUCCEEDED")
    (repo / "foo.py").write_text("foo = 2\n", encoding="utf-8", newline="")
    checkpoint = _checkpoint(store, state, repo, action_id)
    state = _publish_verified(store, state, checkpoint)
    assert state.active_flow.workspace_digest_expected_current != state.active_flow.workspace_digest_baseline
    assert store.read_actions().records[-1].effect.after_digest is None
    if case == "closed":
        state = coordinator.close_flow(state.session_id, at=NOW4)
    elif case == "new_flow":
        previous_flow_id = state.active_flow.flow_id
        coordinator.close_flow(state.session_id, at=NOW4)
        _git(repo, "add", "foo.py")
        _git(repo, "commit", "-m", "retain verified result")
        state = coordinator.start_flow(state.session_id, at=NOW4)
        assert state.active_flow is not None
        assert state.active_flow.flow_id != previous_flow_id
        assert state.active_flow.workspace_digest_baseline != checkpoint.workspace_digest
    elif case in {"pending_next", "failed_next", "two_successes"}:
        last = {"pending_next": "PREPARED", "failed_next": "FAILED", "two_successes": "SUCCEEDED"}[case]
        state, action_id = _append_action(store, state, last=last, target="d" * 64)
        if case == "two_successes":
            (repo / "foo.py").write_text("foo = 3\n", encoding="utf-8", newline="")
            state = _publish_verified(store, state, _checkpoint(store, state, repo, action_id))
    result = _assess_without_writes(lease, coordinator, state.session_id)
    if case == "pending_next":
        assert result.reason.value == "INTERRUPTED_ACTION"
    else:
        assert result.required is False


@pytest.mark.parametrize("last", ["FAILED", "ABANDONED", "PREPARED"])
def test_action_flow_evidence_accepts_unchanged_baseline_context(action_flow_session, last):
    _, lease, coordinator, state, store = action_flow_session
    state, _ = _append_action(store, state, last=last)
    result = _assess_without_writes(lease, coordinator, state.session_id)
    if last == "PREPARED":
        assert result.reason.value == "INTERRUPTED_ACTION"
    else:
        assert result.required is False


def test_action_flow_evidence_rejects_replacement_baseline_checkpoint(action_flow_session):
    repo, lease, coordinator, state, store = action_flow_session
    workspace, digest = compute_workspace_digest_v1(repo)
    checkpoint = build_checkpoint_manifest(
        checkpoint_id=str(uuid.uuid4()), session_id=state.session_id, action_id=None,
        kind="SESSION_BASELINE", created_at=NOW4, workspace_digest=digest,
        head_sha=workspace.head_sha, resources=[],
    )
    store.write_checkpoint(checkpoint, {})
    state = _publish_verified(store, state, checkpoint)
    result = _assess_without_writes(lease, coordinator, state.session_id)
    assert result.required is True
    assert result.reason.value == "STATE_JOURNAL_MISMATCH"


def test_action_flow_evidence_rejects_other_flow_latest_verification(action_flow_session):
    repo, lease, coordinator, state, store = action_flow_session
    old_flow = state.active_flow
    coordinator.close_flow(state.session_id, at=NOW4)
    state = coordinator.start_flow(state.session_id, at=NOW4)
    state, action_id = _append_action(store, state, last="SUCCEEDED")
    state = _publish_verified(store, state, _checkpoint(store, state, repo, action_id))
    state, old_action_id = _append_action(store, state, last="SUCCEEDED", target="d" * 64,
                                         flow_id=old_flow.flow_id, context=old_flow.workspace_digest_baseline)
    state = _publish_verified(store, state, _checkpoint(store, state, repo, old_action_id))
    result = _assess_without_writes(lease, coordinator, state.session_id)
    assert result.required is True
    assert result.reason.value == "STATE_JOURNAL_MISMATCH"
