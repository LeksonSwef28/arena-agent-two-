"""P1-A5 deterministic action identity and transition-adversary tests."""
from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest

from arena.project_safe_session import (
    JournalCorruptionError,
    ProjectLease,
    ProjectSafeSessionStore,
    StorageError,
    canonical_sha256,
    compute_action_id,
    compute_args_hash,
    compute_attempt_id,
)

SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64
NOW = "2026-10-07T02:30:00Z"


def _uuid() -> str:
    return str(uuid.uuid4())


def _store(tmp_path: Path):
    project = tmp_path / "project"
    project.mkdir()
    state = tmp_path / "state"
    lease = ProjectLease(project, state).acquire()
    store = ProjectSafeSessionStore(lease, _uuid())
    return lease, store


def _identity(session_id: str, *, args_hash: str = SHA_A, target: str = SHA_C) -> str:
    return compute_action_id(
        session_id=session_id,
        proposal_id="proposal-1",
        proposal_digest=SHA_B,
        action_type="fs.edit",
        effect_target_fingerprint=target,
        args_hash=args_hash,
    )


def _draft(
    store: ProjectSafeSessionStore,
    action_id: str,
    payload_ref: str,
    payload_sha: str,
    flow_id: str,
    *,
    state: str,
    effect: str,
    attempt_seq: int = 1,
    reason=None,
    summary=None,
) -> dict:
    return {
        "action_id": action_id,
        "flow_id": flow_id,
        "attempt_seq": attempt_seq,
        "attempt_id": compute_attempt_id(action_id, attempt_seq),
        "state": state,
        "effect": {
            "status": effect,
            "before_digest": SHA_A,
            "after_digest": None,
            "expected_after_digest": SHA_C,
        },
        "recorded_at": NOW,
        "proposal_id": "proposal-1",
        "proposal_digest": SHA_B,
        "action_type": "fs.edit",
        "effect_target_fingerprint": SHA_C,
        "risk": "dangerous",
        "input": {
            "args_hash": SHA_A,
            "payload_ref": payload_ref,
            "payload_sha256": payload_sha,
            "summary": summary or {"path": "src/foo.py", "op": "edit"},
        },
        "workspace_digest_context": SHA_B,
        "reason": reason,
    }


def _prepared(store: ProjectSafeSessionStore):
    action_id = _identity(store.session_id)
    payload_ref, payload_sha = store.write_action_payload(
        action_id,
        "input.json",
        b'{"path":"src/foo.py","content":"x"}',
    )
    flow_id = _uuid()
    first = store.append_action(
        _draft(
            store,
            action_id,
            payload_ref,
            payload_sha,
            flow_id,
            state="PREPARED",
            effect="NONE",
        )
    )
    return action_id, payload_ref, payload_sha, flow_id, first


def test_action_id_is_deterministic_and_workspace_context_is_not_an_input():
    session = _uuid()
    args_hash = compute_args_hash({"content": "x", "path": "src/foo.py"})

    first = compute_action_id(
        session_id=session,
        proposal_id="p1",
        proposal_digest=SHA_A,
        action_type="fs.edit",
        effect_target_fingerprint=SHA_B,
        args_hash=args_hash,
    )
    second = compute_action_id(
        session_id=session,
        proposal_id="p1",
        proposal_digest=SHA_A,
        action_type="fs.edit",
        effect_target_fingerprint=SHA_B,
        args_hash=args_hash,
    )

    assert first == second


def test_action_id_changes_for_semantic_identity_inputs():
    session = _uuid()
    base = compute_action_id(
        session_id=session,
        proposal_id="p1",
        proposal_digest=SHA_A,
        action_type="fs.edit",
        effect_target_fingerprint=SHA_B,
        args_hash=SHA_C,
    )
    assert compute_action_id(
        session_id=session,
        proposal_id="p1",
        proposal_digest=SHA_C,
        action_type="fs.edit",
        effect_target_fingerprint=SHA_B,
        args_hash=SHA_C,
    ) != base
    assert compute_action_id(
        session_id=session,
        proposal_id="p1",
        proposal_digest=SHA_A,
        action_type="fs.edit",
        effect_target_fingerprint=SHA_C,
        args_hash=SHA_C,
    ) != base
    assert compute_action_id(
        session_id=session,
        proposal_id="p1",
        proposal_digest=SHA_A,
        action_type="fs.edit",
        effect_target_fingerprint=SHA_B,
        args_hash=SHA_A,
    ) != base


def test_attempt_id_is_deterministic_and_changes_per_attempt():
    action_id = SHA_A
    assert compute_attempt_id(action_id, 1) == compute_attempt_id(action_id, 1)
    assert compute_attempt_id(action_id, 1) != compute_attempt_id(action_id, 2)


def test_storage_rejects_caller_supplied_fake_action_id(tmp_path: Path):
    lease, store = _store(tmp_path)
    try:
        real = _identity(store.session_id)
        payload_ref, payload_sha = store.write_action_payload(
            real, "input.json", b'{"path":"src/foo.py"}'
        )
        draft = _draft(
            store,
            real,
            payload_ref,
            payload_sha,
            _uuid(),
            state="PREPARED",
            effect="NONE",
        )
        draft["action_id"] = SHA_A
        draft["input"]["payload_ref"] = f"actions/{SHA_A}/input.json"

        with pytest.raises(StorageError, match="does not match deterministic"):
            store.append_action(draft)
    finally:
        lease.release()


def test_same_action_cannot_change_immutable_provenance(tmp_path: Path):
    lease, store = _store(tmp_path)
    try:
        action_id, payload_ref, payload_sha, flow_id, _ = _prepared(store)
        draft = _draft(
            store,
            action_id,
            payload_ref,
            payload_sha,
            flow_id,
            state="EXECUTING",
            effect="NONE",
            summary={"path": "src/other.py", "op": "edit"},
        )

        with pytest.raises(StorageError, match="immutable action identity"):
            store.append_action(draft)
    finally:
        lease.release()


def test_prepared_cannot_jump_directly_to_succeeded(tmp_path: Path):
    lease, store = _store(tmp_path)
    try:
        action_id, payload_ref, payload_sha, flow_id, _ = _prepared(store)
        draft = _draft(
            store,
            action_id,
            payload_ref,
            payload_sha,
            flow_id,
            state="SUCCEEDED",
            effect="EXPECTED",
        )

        with pytest.raises(StorageError, match="PREPARED -> SUCCEEDED"):
            store.append_action(draft)
    finally:
        lease.release()


def test_retry_after_failed_none_uses_same_action_new_attempt(tmp_path: Path):
    lease, store = _store(tmp_path)
    try:
        action_id, payload_ref, payload_sha, flow_id, _ = _prepared(store)
        store.append_action(
            _draft(
                store,
                action_id,
                payload_ref,
                payload_sha,
                flow_id,
                state="EXECUTING",
                effect="NONE",
            )
        )
        failed = store.append_action(
            _draft(
                store,
                action_id,
                payload_ref,
                payload_sha,
                flow_id,
                state="FAILED",
                effect="NONE",
                reason="TOOL_REJECTED_BEFORE_MUTATION",
            )
        )
        retry = store.append_action(
            _draft(
                store,
                action_id,
                payload_ref,
                payload_sha,
                flow_id,
                state="PREPARED",
                effect="NONE",
                attempt_seq=2,
            )
        )

        assert retry.action_id == action_id
        assert retry.action_seq == failed.action_seq == 1
        assert retry.transition_seq == failed.transition_seq + 1
        assert retry.attempt_seq == 2
        assert retry.attempt_id == compute_attempt_id(action_id, 2)
    finally:
        lease.release()


def test_retry_after_partial_effect_is_denied(tmp_path: Path):
    lease, store = _store(tmp_path)
    try:
        action_id, payload_ref, payload_sha, flow_id, _ = _prepared(store)
        store.append_action(
            _draft(
                store,
                action_id,
                payload_ref,
                payload_sha,
                flow_id,
                state="EXECUTING",
                effect="NONE",
            )
        )
        store.append_action(
            _draft(
                store,
                action_id,
                payload_ref,
                payload_sha,
                flow_id,
                state="FAILED",
                effect="PARTIAL",
                reason="KNOWN_PARTIAL_EFFECT",
            )
        )

        with pytest.raises(StorageError, match="only after FAILED with effect=NONE"):
            store.append_action(
                _draft(
                    store,
                    action_id,
                    payload_ref,
                    payload_sha,
                    flow_id,
                    state="PREPARED",
                    effect="NONE",
                    attempt_seq=2,
                )
            )
    finally:
        lease.release()


def test_succeeded_action_is_terminal_and_cannot_execute_again(tmp_path: Path):
    lease, store = _store(tmp_path)
    try:
        action_id, payload_ref, payload_sha, flow_id, _ = _prepared(store)
        store.append_action(
            _draft(
                store,
                action_id,
                payload_ref,
                payload_sha,
                flow_id,
                state="EXECUTING",
                effect="NONE",
            )
        )
        store.append_action(
            _draft(
                store,
                action_id,
                payload_ref,
                payload_sha,
                flow_id,
                state="VERIFYING",
                effect="EXPECTED",
            )
        )
        store.append_action(
            _draft(
                store,
                action_id,
                payload_ref,
                payload_sha,
                flow_id,
                state="SUCCEEDED",
                effect="EXPECTED",
            )
        )

        with pytest.raises(StorageError, match="SUCCEEDED is terminal"):
            store.append_action(
                _draft(
                    store,
                    action_id,
                    payload_ref,
                    payload_sha,
                    flow_id,
                    state="PREPARED",
                    effect="NONE",
                    attempt_seq=2,
                )
            )
    finally:
        lease.release()


def test_read_detects_semantic_tamper_even_with_recomputed_hash(tmp_path: Path):
    lease, store = _store(tmp_path)
    try:
        action_id, payload_ref, payload_sha, flow_id, _ = _prepared(store)
        store.append_action(
            _draft(
                store,
                action_id,
                payload_ref,
                payload_sha,
                flow_id,
                state="EXECUTING",
                effect="NONE",
            )
        )

        lines = store.actions_path.read_bytes().splitlines()
        first = json.loads(lines[0])
        second = json.loads(lines[1])
        second["flow_id"] = _uuid()
        second["record_hash"] = canonical_sha256(
            {key: value for key, value in second.items() if key != "record_hash"}
        )
        store.actions_path.write_bytes(
            json.dumps(first, sort_keys=True, separators=(",", ":")).encode("utf-8")
            + b"\n"
            + json.dumps(second, sort_keys=True, separators=(",", ":")).encode("utf-8")
            + b"\n"
        )

        with pytest.raises(JournalCorruptionError, match="action contract violation"):
            store.read_actions()
    finally:
        lease.release()


def test_first_record_for_action_must_be_prepared(tmp_path: Path):
    lease, store = _store(tmp_path)
    try:
        action_id = _identity(store.session_id)
        payload_ref, payload_sha = store.write_action_payload(
            action_id, "input.json", b'{"path":"src/foo.py"}'
        )
        with pytest.raises(StorageError, match="first record.*PREPARED"):
            store.append_action(
                _draft(
                    store,
                    action_id,
                    payload_ref,
                    payload_sha,
                    _uuid(),
                    state="EXECUTING",
                    effect="NONE",
                )
            )
    finally:
        lease.release()
