"""P1-A2 strict schema and canonical-JSON contract tests."""
from __future__ import annotations

import math
import uuid

import pytest

from arena.project_safe_session import (
    CheckpointManifest,
    FileResourceBefore,
    JournalRecord,
    SchemaError,
    StateSnapshot,
    canonical_json_bytes,
    canonical_sha256,
)

SHA256_A = "a" * 64
SHA256_B = "b" * 64
SHA256_C = "c" * 64
SHA1_A = "a" * 40
NOW = "2026-10-07T01:00:00Z"


def _uuid() -> str:
    return str(uuid.uuid4())


def _state() -> dict:
    checkpoint = _uuid()
    session = _uuid()
    browser_instance = _uuid()
    return {
        "schema_version": 1,
        "state_revision": 1,
        "session_id": session,
        "session_fingerprint": SHA256_A,
        "parent_session_id": None,
        "project_fingerprint": SHA256_B,
        "project": {
            "canonical_root": r"F:\project",
            "requested_mode": "write",
        },
        "goal": {
            "initial": "Fix preflight",
            "current": "Fix preflight",
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
            "required_roles": ["MAIN_GPT"],
            "bindings": {
                "MAIN_GPT": {
                    "provider": "chatgpt",
                    "status": "BOUND",
                    "binding_revision": 1,
                    "browser_instance_id": browser_instance,
                    "tab_id": "9123",
                    "origin": "https://chatgpt.com",
                    "conversation_url": "https://chatgpt.com/c/example",
                    "conversation_fingerprint": "conv-example",
                    "bound_at": NOW,
                    "last_verified_at": NOW,
                    "stale_reason": None,
                }
            },
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


def _journal(*, state: str = "PREPARED", effect: str = "NONE", reason=None) -> dict:
    return {
        "schema_version": 1,
        "journal_seq": 1,
        "action_seq": 1,
        "transition_seq": 1,
        "action_id": SHA256_A,
        "session_id": _uuid(),
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
        "previous_record_hash": None,
        "record_hash": SHA256_C,
        "proposal_id": "proposal-1",
        "proposal_digest": SHA256_B,
        "action_type": "fs.edit",
        "effect_target_fingerprint": SHA256_A,
        "risk": "dangerous",
        "input": {
            "args_hash": SHA256_A,
            "payload_ref": "actions/example/input.json",
            "payload_sha256": SHA256_B,
            "summary": {"path": "src/foo.py", "op": "edit"},
        },
        "workspace_digest_context": SHA256_C,
        "reason": reason,
    }


def _checkpoint() -> dict:
    return {
        "schema_version": 1,
        "checkpoint_id": _uuid(),
        "session_id": _uuid(),
        "action_id": SHA256_A,
        "kind": "RESOURCE_BEFORE",
        "created_at": NOW,
        "workspace_digest": SHA256_B,
        "head_sha": SHA1_A,
        "resources": [
            {
                "canonical_relative_path": "src/foo.py",
                "existed": True,
                "size": 3,
                "content_sha256": SHA256_C,
                "backup_ref": f"files/{SHA256_C}.bin",
                "backup_sha256": SHA256_C,
            }
        ],
        "manifest_sha256": SHA256_A,
    }


def test_canonical_json_is_order_independent_and_exact_utf8():
    left = {"b": 2, "a": "кириллица"}
    right = {"a": "кириллица", "b": 2}

    assert canonical_json_bytes(left) == canonical_json_bytes(right)
    assert canonical_json_bytes(left) == '{"a":"кириллица","b":2}'.encode("utf-8")
    assert canonical_sha256(left) == canonical_sha256(right)


def test_canonical_json_rejects_nan_and_infinity():
    with pytest.raises(ValueError):
        canonical_json_bytes({"x": math.nan})
    with pytest.raises(ValueError):
        canonical_json_bytes({"x": math.inf})


def test_state_snapshot_round_trip_preserves_binding_role_names():
    raw = _state()

    parsed = StateSnapshot.from_dict(raw)
    rendered = parsed.to_dict()

    assert rendered == raw
    assert set(rendered["browser"]["bindings"]) == {"MAIN_GPT"}


def test_state_snapshot_rejects_unknown_top_level_field():
    raw = _state()
    raw["surprise"] = True

    with pytest.raises(SchemaError, match="keys mismatch"):
        StateSnapshot.from_dict(raw)


def test_state_snapshot_rejects_bool_as_integer_revision():
    raw = _state()
    raw["state_revision"] = True

    with pytest.raises(SchemaError, match="integer"):
        StateSnapshot.from_dict(raw)


def test_waiting_requires_reason_and_non_waiting_forbids_reason():
    raw = _state()
    raw["lifecycle"]["status"] = "WAITING"
    with pytest.raises(SchemaError, match="reason is required"):
        StateSnapshot.from_dict(raw)

    raw = _state()
    raw["lifecycle"]["reason"] = "NEED_USER"
    with pytest.raises(SchemaError, match="must be null"):
        StateSnapshot.from_dict(raw)


def test_recovery_phase_requires_recovery_object():
    raw = _state()
    raw["lifecycle"] = {
        "status": "WAITING",
        "phase": "RECOVERY",
        "reason": "RECOVERY_REQUIRED",
        "changed_at": NOW,
    }

    with pytest.raises(SchemaError, match="recovery object is required"):
        StateSnapshot.from_dict(raw)


def test_stale_binding_requires_reason():
    raw = _state()
    binding = raw["browser"]["bindings"]["MAIN_GPT"]
    binding["status"] = "STALE"

    with pytest.raises(SchemaError, match="stale_reason is required"):
        StateSnapshot.from_dict(raw)


def test_file_resource_before_distinguishes_absent_from_existing_empty():
    absent = {
        "project_fingerprint": SHA256_A,
        "canonical_relative_path": "empty.txt",
        "resource_type": "file",
        "exists": False,
        "content_sha256": None,
    }
    parsed = FileResourceBefore.from_dict(absent)
    assert parsed.exists is False

    invalid = dict(absent)
    invalid["exists"] = True
    with pytest.raises(SchemaError, match="requires content_sha256"):
        FileResourceBefore.from_dict(invalid)


def test_file_resource_before_rejects_parent_traversal():
    raw = {
        "project_fingerprint": SHA256_A,
        "canonical_relative_path": "../outside.txt",
        "resource_type": "file",
        "exists": False,
        "content_sha256": None,
    }

    with pytest.raises(SchemaError, match="safe relative path"):
        FileResourceBefore.from_dict(raw)


def test_prepared_requires_effect_none():
    raw = _journal(state="PREPARED", effect="UNKNOWN")

    with pytest.raises(SchemaError, match="PREPARED requires effect=NONE"):
        JournalRecord.from_dict(raw)


def test_succeeded_requires_expected_effect():
    raw = _journal(state="SUCCEEDED", effect="UNKNOWN")

    with pytest.raises(SchemaError, match="SUCCEEDED requires effect=EXPECTED"):
        JournalRecord.from_dict(raw)


def test_abandoned_is_terminal_without_effect_and_requires_reason():
    parsed = JournalRecord.from_dict(
        _journal(state="ABANDONED", effect="NONE", reason="WORKSPACE_DRIFT")
    )
    assert parsed.state.value == "ABANDONED"

    with pytest.raises(SchemaError, match="ABANDONED requires effect=NONE"):
        JournalRecord.from_dict(
            _journal(state="ABANDONED", effect="UNKNOWN", reason="WORKSPACE_DRIFT")
        )

    with pytest.raises(SchemaError, match="supported abandonment reason"):
        JournalRecord.from_dict(_journal(state="ABANDONED", effect="NONE", reason=None))


def test_journal_rejects_malformed_sha_and_uuid():
    raw = _journal()
    raw["action_id"] = "not-a-sha"
    with pytest.raises(SchemaError, match="64 lowercase hex"):
        JournalRecord.from_dict(raw)

    raw = _journal()
    raw["session_id"] = str(uuid.uuid1())
    with pytest.raises(SchemaError, match="UUID v4"):
        JournalRecord.from_dict(raw)


def test_checkpoint_existing_backup_must_match_content_digest():
    raw = _checkpoint()
    raw["resources"][0]["backup_sha256"] = SHA256_B

    with pytest.raises(SchemaError, match="must equal original"):
        CheckpointManifest.from_dict(raw)


def test_checkpoint_absent_resource_has_no_backup_payload():
    raw = _checkpoint()
    raw["resources"] = [
        {
            "canonical_relative_path": "new.txt",
            "existed": False,
            "size": 0,
            "content_sha256": None,
            "backup_ref": None,
            "backup_sha256": None,
        }
    ]

    parsed = CheckpointManifest.from_dict(raw)
    assert parsed.resources[0].existed is False


def test_checkpoint_rejects_duplicate_resource_paths():
    raw = _checkpoint()
    raw["resources"].append(dict(raw["resources"][0]))

    with pytest.raises(SchemaError, match="duplicate paths"):
        CheckpointManifest.from_dict(raw)
