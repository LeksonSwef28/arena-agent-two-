"""T85: strict opt-in v2 models; legacy persistence remains unchanged."""
from __future__ import annotations

from copy import deepcopy

import pytest

from arena.project_safe_session import (
    ActionRecordRef,
    CheckpointIntegrityError,
    CheckpointManifest,
    CheckpointManifestV2,
    EventRecordRef,
    JournalRecord,
    JournalRecordV2,
    SchemaError,
    WorkspaceVerificationRef,
    canonical_json_bytes,
    canonical_sha256,
    checkpoint_manifest_sha256,
    validate_checkpoint_manifest_digest,
)
from arena.project_safe_session.schema_types import SCHEMA_VERSION
from tests.test_project_safe_session_checkpoints import _store
from tests.test_project_safe_session_schemas import SHA256_A, SHA256_B, _checkpoint, _journal

UUID_A = "11111111-1111-4111-8111-111111111111"
UUID_B = "22222222-2222-4222-8222-222222222222"
UUID_C = "33333333-3333-4333-8333-333333333333"


def _action_v2(state="PREPARED"):
    raw = _journal(state=state, effect="EXPECTED" if state == "SUCCEEDED" else "NONE")
    raw.update(schema_version=2, session_id=UUID_A, flow_id=UUID_B,
               flow_creation_ref={"event_seq": 3, "record_hash": SHA256_A},
               prepared_event_ref={"event_seq": 3, "record_hash": SHA256_A},
               preceding_success_ref=None, workspace_verification=None)
    if state == "SUCCEEDED":
        raw["workspace_verification"] = {"checkpoint_id": UUID_C, "manifest_sha256": SHA256_B}
    raw["record_hash"] = canonical_sha256({k: v for k, v in raw.items() if k != "record_hash"})
    return raw


def _checkpoint_v2(kind="RESOURCE_AFTER"):
    raw = _checkpoint()
    raw.update(schema_version=2, checkpoint_id=UUID_C, session_id=UUID_A,
               kind=kind, attempt_id=SHA256_B)
    if kind == "SESSION_BASELINE":
        raw.update(action_id=None, attempt_id=None, resources=[])
    raw["manifest_sha256"] = checkpoint_manifest_sha256(raw)
    return raw


@pytest.mark.parametrize("model,raw", [
    (EventRecordRef, {"event_seq": 1, "record_hash": SHA256_A}),
    (ActionRecordRef, {"journal_seq": 1, "record_hash": SHA256_A}),
    (WorkspaceVerificationRef, {"checkpoint_id": UUID_C, "manifest_sha256": SHA256_A}),
])
def test_v2_reference_round_trip(model, raw):
    assert model.from_dict(raw).to_dict() == raw


@pytest.mark.parametrize("model,seq", [(EventRecordRef, "event_seq"), (ActionRecordRef, "journal_seq")])
@pytest.mark.parametrize("bad", [True, 0, -1, 1.0, "1", None])
def test_v2_reference_rejects_non_positive_integer(model, seq, bad):
    with pytest.raises(SchemaError):
        model.from_dict({seq: bad, "record_hash": SHA256_A})


@pytest.mark.parametrize("bad", [None, [], {"event_seq": 1},
    {"event_seq": 1, "record_hash": SHA256_A, "extra": 1},
    {"event_seq": 1, "record_hash": "A" * 64}])
def test_v2_reference_rejects_wrong_shape(bad):
    with pytest.raises(SchemaError):
        EventRecordRef.from_dict(bad)


@pytest.mark.parametrize("change", [{"checkpoint_id": "bad"}, {"manifest_sha256": "bad"},
                                   {"extra": True}])
def test_v2_verification_ref_rejects_invalid_fields(change):
    raw = {"checkpoint_id": UUID_C, "manifest_sha256": SHA256_A} | change
    with pytest.raises(SchemaError):
        WorkspaceVerificationRef.from_dict(raw)


@pytest.mark.parametrize("state", ["PREPARED", "SUCCEEDED"])
def test_v2_action_round_trip_does_not_mutate_input(state):
    raw = _action_v2(state)
    before = deepcopy(raw)
    parsed = JournalRecordV2.from_dict(raw)
    assert parsed.to_dict() == before == raw
    assert parsed.schema_version == 2
    assert parsed.effect.after_digest is None  # Generic evidence remains optional.


@pytest.mark.parametrize("kind", ["RESOURCE_BEFORE", "RESOURCE_AFTER", "SESSION_BASELINE"])
def test_v2_checkpoint_round_trip_and_digest(kind):
    raw = _checkpoint_v2(kind)
    before = deepcopy(raw)
    parsed = CheckpointManifestV2.from_dict(raw)
    assert parsed.to_dict() == before == raw
    validate_checkpoint_manifest_digest(parsed)


@pytest.mark.parametrize("model,fixture", [(JournalRecordV2, _action_v2),
                                          (CheckpointManifestV2, _checkpoint_v2)])
@pytest.mark.parametrize("version", [1, 3, True, "2", None])
def test_v2_models_reject_wrong_version(model, fixture, version):
    raw = fixture()
    raw["schema_version"] = version
    with pytest.raises(SchemaError):
        model.from_dict(raw)


@pytest.mark.parametrize("key", ["flow_creation_ref", "prepared_event_ref",
                                  "preceding_success_ref", "workspace_verification"])
def test_v2_action_requires_all_reference_keys(key):
    raw = _action_v2()
    del raw[key]
    with pytest.raises(SchemaError):
        JournalRecordV2.from_dict(raw)


@pytest.mark.parametrize("change", [
    {"flow_id": None}, {"surprise": True},
    {"flow_creation_ref": None}, {"prepared_event_ref": None},
    {"flow_creation_ref": {"event_seq": 4, "record_hash": SHA256_A}},
    {"prepared_event_ref": {"event_seq": 3, "record_hash": SHA256_B}},
    {"preceding_success_ref": {"journal_seq": 1, "record_hash": SHA256_A}},
    {"preceding_success_ref": {"journal_seq": 2, "record_hash": SHA256_A}},
    {"workspace_verification": {"checkpoint_id": UUID_C, "manifest_sha256": SHA256_B}},
])
def test_v2_action_rejects_invalid_local_evidence(change):
    raw = _action_v2() | change
    with pytest.raises(SchemaError):
        JournalRecordV2.from_dict(raw)


def test_v2_success_requires_verification():
    raw = _action_v2("SUCCEEDED")
    raw["workspace_verification"] = None
    with pytest.raises(SchemaError):
        JournalRecordV2.from_dict(raw)


def test_v2_action_accepts_prior_anchor_without_claiming_history_resolution():
    raw = _action_v2()
    raw.update(journal_seq=5, previous_record_hash=SHA256_B,
               prepared_event_ref={"event_seq": 4, "record_hash": SHA256_B},
               preceding_success_ref={"journal_seq": 4, "record_hash": SHA256_A})
    parsed = JournalRecordV2.from_dict(raw)
    assert parsed.preceding_success_ref.journal_seq == 4


@pytest.mark.parametrize("kind,attempt", [("RESOURCE_AFTER", None), ("RESOURCE_BEFORE", None),
                                        ("SESSION_BASELINE", SHA256_B), ("RESOURCE_AFTER", "bad")])
def test_v2_checkpoint_rejects_attempt_semantics(kind, attempt):
    raw = _checkpoint_v2(kind)
    raw["attempt_id"] = attempt
    with pytest.raises(SchemaError):
        CheckpointManifestV2.from_dict(raw)


def test_v2_checkpoint_requires_attempt_key():
    raw = _checkpoint_v2()
    del raw["attempt_id"]
    with pytest.raises(SchemaError):
        CheckpointManifestV2.from_dict(raw)


@pytest.mark.parametrize("model,fixture,key,value", [
    (JournalRecordV2, _action_v2, "session_id", "bad"),
    (JournalRecordV2, _action_v2, "input", {}),
    (JournalRecordV2, _action_v2, "action_seq", True),
    (CheckpointManifestV2, _checkpoint_v2, "resources", []),
    (CheckpointManifestV2, _checkpoint_v2, "head_sha", "bad"),
    (CheckpointManifestV2, _checkpoint_v2, "action_id", None),
])
def test_v2_common_fields_reuse_strict_v1_rules(model, fixture, key, value):
    raw = fixture()
    raw[key] = value
    with pytest.raises(SchemaError):
        model.from_dict(raw)


def test_v1_models_and_global_version_remain_unchanged():
    assert SCHEMA_VERSION == 1
    for model, raw in [(JournalRecord, _journal()), (CheckpointManifest, _checkpoint())]:
        assert model.from_dict(raw).to_dict() == raw
    with pytest.raises(SchemaError):
        JournalRecord.from_dict(_action_v2())
    with pytest.raises(SchemaError):
        CheckpointManifest.from_dict(_checkpoint_v2())


def test_v2_canonical_hashes_include_references_and_attempt():
    action = JournalRecordV2.from_dict(_action_v2("SUCCEEDED")).to_dict()
    assert canonical_json_bytes(action) == canonical_json_bytes(dict(reversed(list(action.items()))))
    original = canonical_sha256({k: v for k, v in action.items() if k != "record_hash"})
    assert original == action["record_hash"]
    assert original == "87f52e35121b869f79c5ced3c63d0c849ce9ac450bbbda730b8aeca5f50b37eb"
    action["workspace_verification"]["manifest_sha256"] = SHA256_A
    assert original != canonical_sha256({k: v for k, v in action.items() if k != "record_hash"})
    checkpoint = CheckpointManifestV2.from_dict(_checkpoint_v2()).to_dict()
    original = checkpoint_manifest_sha256(checkpoint)
    assert original == "7944cc051064210ef4a3b3c9ab0ca7ab05492d4f73f6c80c18e806cfff7b22b4"
    checkpoint["attempt_id"] = SHA256_A
    assert checkpoint_manifest_sha256(checkpoint) != original


def test_v1_storage_rejects_v2_checkpoint_before_any_write(tmp_path):
    _, lease, store = _store(tmp_path)
    try:
        raw = _checkpoint_v2("SESSION_BASELINE")
        raw["session_id"] = store.session_id
        raw["manifest_sha256"] = checkpoint_manifest_sha256(raw)
        manifest = CheckpointManifestV2.from_dict(raw)
        before = {str(p): p.read_bytes() for p in store.session_dir.rglob("*") if p.is_file()}
        with pytest.raises(CheckpointIntegrityError):
            store.write_checkpoint(manifest, {})
        assert not store.checkpoints_dir.exists()
        assert before == {str(p): p.read_bytes() for p in store.session_dir.rglob("*") if p.is_file()}
    finally:
        lease.release()
