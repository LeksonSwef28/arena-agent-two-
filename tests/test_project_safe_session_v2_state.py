"""T86: opt-in state envelope and watermark; v1 publication stays closed."""
from __future__ import annotations

from copy import deepcopy

import pytest

from arena.project_safe_session import (
    ExecutionStateV2,
    FormatVersionsV2,
    SchemaError,
    StateSnapshot,
    StateSnapshotV2,
    StorageError,
    canonical_json_bytes,
    canonical_sha256,
)
from arena.project_safe_session.schema_types import SCHEMA_VERSION
from tests.test_project_safe_session_checkpoints import _store
from tests.test_project_safe_session_schemas import SHA256_A, _state
from tests.test_project_safe_session_storage import _state as _storage_state

FORMATS = {"state": 2, "actions": 2, "checkpoints": 2, "events": 1,
           "registry": 1, "canonical_json": 1, "workspace_digest": 1}
UUID_A = "11111111-1111-4111-8111-111111111111"


def _v2(raw=None, reference=None):
    raw = deepcopy(_state() if raw is None else raw)
    raw.update(schema_version=2, formats=FORMATS.copy())
    raw["execution"]["last_applied_action_ref"] = reference
    return raw


@pytest.mark.parametrize("reference", [None, {"journal_seq": 7, "record_hash": SHA256_A}])
def test_v2_state_round_trip_without_input_mutation(reference):
    raw = _v2(reference=reference)
    before = deepcopy(raw)
    parsed = StateSnapshotV2.from_dict(raw)
    assert parsed.to_dict() == before == raw
    assert parsed.schema_version == 2
    assert isinstance(parsed.execution, ExecutionStateV2)
    assert isinstance(parsed.formats, FormatVersionsV2)
    assert canonical_json_bytes(parsed.to_dict()) == canonical_json_bytes(raw)


@pytest.mark.parametrize("key", list(FORMATS))
@pytest.mark.parametrize("bad", [True, 0, 3, "1", None])
def test_v2_formats_rejects_unsupported_or_non_integer_version(key, bad):
    raw = _v2()
    raw["formats"][key] = bad
    with pytest.raises(SchemaError):
        StateSnapshotV2.from_dict(raw)


@pytest.mark.parametrize("key", list(FORMATS))
def test_v2_formats_requires_every_family(key):
    raw = FORMATS.copy()
    del raw[key]
    with pytest.raises(SchemaError):
        FormatVersionsV2.from_dict(raw)


@pytest.mark.parametrize("bad", [None, [], FORMATS | {"extra": 1}])
def test_v2_formats_rejects_wrong_shape(bad):
    with pytest.raises(SchemaError):
        FormatVersionsV2.from_dict(bad)


@pytest.mark.parametrize("bad", [1, 3, True, "2"])
def test_v2_state_rejects_unsupported_envelope_version(bad):
    raw = _v2()
    raw["schema_version"] = bad
    with pytest.raises(SchemaError):
        StateSnapshotV2.from_dict(raw)


@pytest.mark.parametrize("location,key", [(None, "formats"),
                                         ("execution", "last_applied_action_ref")])
def test_v2_state_rejects_missing_and_unknown_fields(location, key):
    for extra in (False, True):
        raw = _v2()
        target = raw if location is None else raw[location]
        if extra:
            target["unknown"] = 1
        else:
            del target[key]
        with pytest.raises(SchemaError):
            StateSnapshotV2.from_dict(raw)


@pytest.mark.parametrize("reference", [[], {}, {"journal_seq": 0, "record_hash": SHA256_A},
    {"journal_seq": True, "record_hash": SHA256_A},
    {"journal_seq": 1, "record_hash": "A" * 64},
    {"journal_seq": 1, "record_hash": SHA256_A, "extra": 1}])
def test_v2_state_rejects_invalid_watermark(reference):
    with pytest.raises(SchemaError):
        StateSnapshotV2.from_dict(_v2(reference=reference))


@pytest.mark.parametrize("case", ["revision", "execution", "goal", "recovery", "pending"])
def test_v2_state_preserves_common_invariants(case):
    raw = _v2()
    if case == "revision":
        raw["state_revision"] = 0
    elif case == "execution":
        raw["execution"]["last_action_seq"] = True
    elif case == "goal":
        raw["goal"]["revision"] = 0
    elif case == "recovery":
        raw["lifecycle"].update(status="WAITING", phase="RECOVERY", reason="RECOVERY_REQUIRED")
    else:
        raw["active_flow"] = None
        raw["execution"]["pending_action_id"] = SHA256_A
    with pytest.raises(SchemaError):
        StateSnapshotV2.from_dict(raw)


def test_v1_state_parser_and_bytes_remain_unchanged():
    raw = _state()
    before = canonical_json_bytes(raw)
    assert StateSnapshot.from_dict(raw).to_dict() == raw
    assert canonical_json_bytes(raw) == before
    assert SCHEMA_VERSION == 1
    with pytest.raises(SchemaError):
        StateSnapshot.from_dict(_v2(raw))
    assert FormatVersionsV2.from_dict(FORMATS).to_dict() == FORMATS


def test_v2_watermark_is_not_a_logical_action_counter():
    raw = _v2(reference={"journal_seq": 19, "record_hash": SHA256_A})
    raw["execution"]["last_action_seq"] = 1
    assert StateSnapshotV2.from_dict(raw).execution.last_applied_action_ref.journal_seq == 19


@pytest.mark.parametrize("existing", [False, True])
def test_v1_state_writer_rejects_v2_before_read_or_write(tmp_path, monkeypatch, existing):
    project, lease, store = _store(tmp_path)
    try:
        if existing:
            store.write_state(_storage_state(project, store.session_id, revision=1))
        state = StateSnapshotV2.from_dict(_v2(
            _storage_state(project, store.session_id, revision=2 if existing else 1).to_dict()))
        before = {str(p.relative_to(tmp_path)): p.read_bytes()
                  for p in tmp_path.rglob("*") if p.is_file()}

        def forbidden_read():
            pytest.fail("unsupported state must be rejected before reading current state")

        monkeypatch.setattr(store, "read_state", forbidden_read)
        with pytest.raises(StorageError, match="unsupported state schema_version"):
            store.write_state(state)
        after = {str(p.relative_to(tmp_path)): p.read_bytes()
                 for p in tmp_path.rglob("*") if p.is_file()}
        assert after == before
    finally:
        lease.release()


def test_v2_state_canonical_hash_includes_formats_and_watermark():
    raw = _v2()
    # Normalize UUIDs from the shared randomized fixture for a pinned wire hash.
    raw["session_id"] = UUID_A
    raw["workspace"]["session_baseline"]["checkpoint_id"] = UUID_A
    raw["workspace"]["last_verified_checkpoint_id"] = UUID_A
    raw["browser"]["bindings"]["MAIN_GPT"]["browser_instance_id"] = UUID_A
    digest = canonical_sha256(StateSnapshotV2.from_dict(raw).to_dict())
    assert digest == "f38c1aa15aa1b57e014aecb641c4cecbd82b3587945b83c2243fcfbe297d49c1"
    raw["execution"]["last_applied_action_ref"] = {"journal_seq": 1, "record_hash": SHA256_A}
    assert canonical_sha256(StateSnapshotV2.from_dict(raw).to_dict()) != digest
