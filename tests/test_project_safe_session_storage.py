"""P1-A3 durable state, payload, and strict JSONL storage tests."""
from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest

from arena.project_safe_session import (
    JournalCorruptionError,
    LeaseRequiredError,
    PayloadIntegrityError,
    ProjectLease,
    ProjectSafeSessionStore,
    StateRevisionError,
    StateSnapshot,
    StorageError,
    TruncatedLastRecordError,
    project_fingerprint,
    strict_json_loads,
)

SHA256_A = "a" * 64
SHA256_B = "b" * 64
SHA256_C = "c" * 64
SHA1_A = "a" * 40
NOW = "2026-10-07T01:30:00Z"


def _uuid() -> str:
    return str(uuid.uuid4())


def _state(project: Path, session_id: str, *, revision: int) -> StateSnapshot:
    checkpoint = _uuid()
    raw = {
        "schema_version": 1,
        "state_revision": revision,
        "session_id": session_id,
        "session_fingerprint": SHA256_A,
        "parent_session_id": None,
        "project_fingerprint": project_fingerprint(project),
        "project": {
            "canonical_root": str(project.resolve()),
            "requested_mode": "write",
        },
        "goal": {
            "initial": "Build durable session storage",
            "current": "Build durable session storage",
            "revision": 1,
            "last_changed_at": NOW,
        },
        "lifecycle": {
            "status": "ACTIVE",
            "phase": "PLANNING",
            "reason": None,
            "changed_at": NOW,
        },
        "workspace": {
            "kind": "git_worktree",
            "session_baseline": {
                "checkpoint_id": checkpoint,
                "head_sha": SHA1_A,
                "workspace_digest": SHA256_A,
                "clean": True,
                "captured_at": NOW,
            },
            "workspace_digest_last_verified": SHA256_A,
            "last_verified_checkpoint_id": checkpoint,
            "last_verified_head_sha": SHA1_A,
            "last_verified_at": NOW,
        },
        "browser": {
            "required_roles": [],
            "bindings": {},
        },
        "execution": {
            "active_flow_id": None,
            "pending_action_id": None,
            "last_terminal_action_id": None,
            "last_action_seq": 0,
            "last_event_seq": 0,
        },
        "recovery": None,
        "limits": {
            "max_actions": 100,
            "max_messages_per_role": 50,
            "max_active_minutes": 120,
        },
        "created_at": NOW,
        "updated_at": NOW,
    }
    return StateSnapshot.from_dict(raw)


def _draft(
    action_id: str,
    payload_ref: str,
    payload_sha256: str,
    *,
    state: str = "PREPARED",
    effect: str = "NONE",
) -> dict:
    return {
        "action_id": action_id,
        "flow_id": _uuid(),
        "attempt_seq": 1,
        "attempt_id": SHA256_B,
        "state": state,
        "effect": {
            "status": effect,
            "before_digest": SHA256_A,
            "after_digest": None,
            "expected_after_digest": SHA256_C,
        },
        "recorded_at": NOW,
        "proposal_id": "proposal-1",
        "proposal_digest": SHA256_B,
        "action_type": "fs.edit",
        "effect_target_fingerprint": SHA256_C,
        "risk": "dangerous",
        "input": {
            "args_hash": SHA256_A,
            "payload_ref": payload_ref,
            "payload_sha256": payload_sha256,
            "summary": {"path": "src/foo.py", "op": "edit"},
        },
        "workspace_digest_context": SHA256_A,
        "reason": None,
    }


def _store(tmp_path: Path):
    project = tmp_path / "project"
    project.mkdir()
    state = tmp_path / "state"
    lease = ProjectLease(project, state).acquire()
    session_id = _uuid()
    return project, lease, ProjectSafeSessionStore(lease, session_id)


def test_store_requires_live_project_lease(tmp_path: Path):
    project = tmp_path / "project"
    project.mkdir()
    state = tmp_path / "state"
    lease = ProjectLease(project, state)

    with pytest.raises(LeaseRequiredError):
        ProjectSafeSessionStore(lease, _uuid())


def test_state_revision_is_strict_compare_and_advance(tmp_path: Path):
    project, lease, store = _store(tmp_path)
    try:
        state1 = _state(project, store.session_id, revision=1)
        store.write_state(state1, expected_current_revision=0)
        assert store.read_state() == state1
        assert store.state_path.read_bytes().endswith(b"\n")
        assert not store.state_path.with_name("state.json.tmp").exists()

        with pytest.raises(StateRevisionError, match="next state_revision"):
            store.write_state(state1, expected_current_revision=1)

        state2 = _state(project, store.session_id, revision=2)
        with pytest.raises(StateRevisionError, match="state revision changed"):
            store.write_state(state2, expected_current_revision=0)

        store.write_state(state2, expected_current_revision=1)
        assert store.read_state() == state2
    finally:
        lease.release()


def test_state_write_fails_after_lease_release(tmp_path: Path):
    project, lease, store = _store(tmp_path)
    state1 = _state(project, store.session_id, revision=1)
    lease.release()

    with pytest.raises(LeaseRequiredError):
        store.write_state(state1)


def test_state_reader_rejects_duplicate_json_keys(tmp_path: Path):
    project, lease, store = _store(tmp_path)
    try:
        store.session_dir.mkdir(parents=True)
        store.state_path.write_bytes(b'{"schema_version":1,"schema_version":1}\n')

        with pytest.raises(StorageError, match="duplicate JSON key"):
            store.read_state()
    finally:
        lease.release()


def test_strict_json_loads_rejects_duplicate_keys_and_nan():
    with pytest.raises(ValueError, match="duplicate JSON key"):
        strict_json_loads(b'{"a":1,"a":2}')
    with pytest.raises(ValueError, match="non-finite"):
        strict_json_loads(b'{"a":NaN}')


def test_action_journal_owns_sequence_and_hash_chain(tmp_path: Path):
    _, lease, store = _store(tmp_path)
    try:
        payload_ref, payload_sha = store.write_action_payload(
            SHA256_A, "input.json", b'{"path":"src/foo.py"}'
        )
        first = store.append_action(_draft(SHA256_A, payload_ref, payload_sha))
        second_draft = _draft(
            SHA256_A,
            payload_ref,
            payload_sha,
            state="EXECUTING",
            effect="NONE",
        )
        second_draft["flow_id"] = first.flow_id
        second = store.append_action(second_draft)

        assert first.journal_seq == 1
        assert first.action_seq == 1
        assert first.transition_seq == 1
        assert first.previous_record_hash is None
        assert second.journal_seq == 2
        assert second.action_seq == 1
        assert second.transition_seq == 2
        assert second.previous_record_hash == first.record_hash

        reread = store.read_actions()
        assert reread.records == (first, second)
        assert reread.missing_trailing_newline is False
    finally:
        lease.release()


def test_action_journal_detects_hash_chain_corruption(tmp_path: Path):
    _, lease, store = _store(tmp_path)
    try:
        payload_ref, payload_sha = store.write_action_payload(
            SHA256_A, "input.json", b'{"path":"src/foo.py"}'
        )
        store.append_action(_draft(SHA256_A, payload_ref, payload_sha))

        raw = bytearray(store.actions_path.read_bytes())
        marker = b'"proposal_id":"proposal-1"'
        assert marker in raw
        raw = raw.replace(marker, b'"proposal_id":"proposal-X"', 1)
        store.actions_path.write_bytes(bytes(raw))

        with pytest.raises(JournalCorruptionError, match="record_hash mismatch"):
            store.read_actions()
    finally:
        lease.release()


def test_invalid_unterminated_last_record_is_truncated_not_silently_skipped(tmp_path: Path):
    _, lease, store = _store(tmp_path)
    try:
        store.session_dir.mkdir(parents=True)
        store.actions_path.write_bytes(b'{"schema_version":1')

        with pytest.raises(TruncatedLastRecordError):
            store.read_actions()
    finally:
        lease.release()


def test_valid_record_without_newline_is_accepted_and_repaired_before_append(tmp_path: Path):
    _, lease, store = _store(tmp_path)
    try:
        payload_ref, payload_sha = store.write_action_payload(
            SHA256_A, "input.json", b'{"path":"src/foo.py"}'
        )
        first = store.append_action(_draft(SHA256_A, payload_ref, payload_sha))
        data = store.actions_path.read_bytes()
        assert data.endswith(b"\n")
        store.actions_path.write_bytes(data[:-1])

        reread = store.read_actions()
        assert reread.records == (first,)
        assert reread.missing_trailing_newline is True

        second_draft = _draft(
            SHA256_A,
            payload_ref,
            payload_sha,
            state="EXECUTING",
            effect="NONE",
        )
        second_draft["flow_id"] = first.flow_id
        store.append_action(second_draft)

        final = store.actions_path.read_bytes()
        assert final.endswith(b"\n")
        assert len(final.splitlines()) == 2
        assert len(store.read_actions().records) == 2
    finally:
        lease.release()


def test_payload_is_immutable_and_digest_verified(tmp_path: Path):
    _, lease, store = _store(tmp_path)
    try:
        relative, digest = store.write_action_payload(SHA256_A, "input.json", b"alpha")
        assert store.write_action_payload(SHA256_A, "input.json", b"alpha") == (relative, digest)

        with pytest.raises(PayloadIntegrityError, match="different bytes"):
            store.write_action_payload(SHA256_A, "input.json", b"beta")

        payload_path = store.session_dir / relative
        payload_path.write_bytes(b"corrupt")
        with pytest.raises(PayloadIntegrityError, match="digest mismatch"):
            store.read_action_payload(relative, digest)
    finally:
        lease.release()


def test_event_journal_is_hash_chained_and_sequenced(tmp_path: Path):
    _, lease, store = _store(tmp_path)
    try:
        first = store.append_event(
            {"event_type": "SESSION_CREATED", "recorded_at": NOW, "data": {"goal_revision": 1}}
        )
        second = store.append_event(
            {"event_type": "PHASE_CHANGED", "recorded_at": NOW, "data": {"to": "PLANNING"}}
        )

        assert first.event_seq == 1
        assert second.event_seq == 2
        assert second.previous_record_hash == first.record_hash
        assert store.read_events().records == (first, second)
    finally:
        lease.release()
