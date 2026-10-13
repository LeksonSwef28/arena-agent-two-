"""T91: real disk version selection, immutable evidence and unchanged v1 bytes."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace

import pytest

from arena.project_safe_session import (
    CheckpointIntegrityError,
    JournalCorruptionError,
    PayloadIntegrityError,
    ProjectSafeSessionStore,
    StateRevisionError,
    StorageError,
    TruncatedLastRecordError,
    canonical_json_bytes,
    canonical_sha256,
    checkpoint_manifest_sha256,
)
from arena.project_safe_session.schema_utils import SchemaError
from arena.project_safe_session.v2_models import CheckpointManifestV2, JournalRecordV2, StateSnapshotV2
from arena.project_safe_session.v2_storage import ProjectSafeSessionStoreV2
from tests.test_project_safe_session_checkpoints import _manifest
from tests.test_project_safe_session_storage import ACTION_ARGS, SHA256_A, SHA256_B, _action_id, _draft, _state, _store
from tests.test_project_safe_session_v2_state import _v2


@pytest.fixture
def durable(tmp_path):
    project, lease, legacy = _store(tmp_path)
    store = ProjectSafeSessionStoreV2(lease, legacy.session_id)
    state = StateSnapshotV2.from_dict(_v2(_state(project, store.session_id, revision=1).to_dict()))
    store.write_state(state, expected_current_revision=0)
    try:
        yield project, lease, store
    finally:
        lease.release()


def _prepared(store):
    action_id = _action_id(store.session_id)
    ref, _, digest = store.write_action_input(action_id, ACTION_ARGS)
    draft = _draft(store.session_id, action_id, ref, digest)
    draft.update(flow_creation_ref={"event_seq": 1, "record_hash": SHA256_A},
                 prepared_event_ref={"event_seq": 1, "record_hash": SHA256_A},
                 preceding_success_ref=None, workspace_verification=None)
    return draft


def _checkpoint(project, store):
    manifest, backups = _manifest(store, project, b"verified backup")
    raw = manifest.to_dict()
    raw.update(schema_version=2, attempt_id=SHA256_A)
    raw["manifest_sha256"] = checkpoint_manifest_sha256(raw)
    return CheckpointManifestV2.from_dict(raw), backups


def _bytes(store):
    return {str(p.relative_to(store.session_dir)): p.read_bytes()
            for p in store.session_dir.rglob("*") if p.is_file()}


def _rehash(raw):
    raw["record_hash"] = canonical_sha256({k: v for k, v in raw.items() if k != "record_hash"})


def test_v2_storage_round_trips_state_actions_events_and_checkpoint_bytes(durable):
    project, lease, store = durable
    state = store.read_state()
    assert isinstance(state, StateSnapshotV2)
    assert store.state_path.read_bytes() == canonical_json_bytes(state.to_dict()) + b"\n"
    draft = _prepared(store)
    record = store.append_action(draft)
    assert isinstance(record, JournalRecordV2)
    assert store.read_actions().records == (record,)
    event = store.append_event({"event_type": "FLOW_CLOSED", "recorded_at": state.updated_at,
                                "data": {"flow_id": draft["flow_id"], "reason": "storage fixture"}})
    assert event.schema_version == 1
    assert store.read_events().records == (event,)
    manifest, backups = _checkpoint(project, store)
    store.write_checkpoint(manifest, backups)
    reopened = ProjectSafeSessionStoreV2(lease, store.session_id)
    assert reopened.read_state() == state
    assert reopened.read_actions().records == (record,)
    assert reopened.read_checkpoint(manifest.checkpoint_id) == manifest
    assert (store.checkpoints_dir / manifest.checkpoint_id / "manifest.json").read_bytes() == (
        canonical_json_bytes(manifest.to_dict()) + b"\n")
    before = _bytes(store)
    reopened.read_state()
    reopened.read_actions()
    reopened.read_events()
    reopened.read_checkpoint(manifest.checkpoint_id)
    assert _bytes(store) == before


def test_v2_storage_state_revision_remains_compare_and_advance(durable):
    _, _, store = durable
    state = store.read_state()
    with pytest.raises(StateRevisionError):
        store.write_state(replace(state, state_revision=2), expected_current_revision=0)
    store.write_state(replace(state, state_revision=2), expected_current_revision=1)
    assert store.read_state().state_revision == 2


@pytest.mark.parametrize("operation", ["state", "actions", "events", "checkpoint", "payload"])
def test_v1_adapter_cannot_write_into_v2_session(durable, operation):
    project, lease, store = durable
    legacy = ProjectSafeSessionStore(lease, store.session_id)
    before = _bytes(store)
    calls = {"state": lambda: legacy.write_state(_state(project, store.session_id, revision=2)),
             "actions": lambda: legacy.append_action({}),
             "events": lambda: legacy.append_event({}),
             "checkpoint": lambda: legacy.write_checkpoint(_manifest(legacy, project, b"legacy")[0], {}),
             "payload": lambda: legacy.write_action_payload(SHA256_A, "result.json", b"legacy")}
    with pytest.raises(StorageError, match="session format"):
        calls[operation]()
    assert _bytes(store) == before


def test_v2_adapter_rejects_v1_session_before_any_writes(tmp_path):
    project, lease, legacy = _store(tmp_path)
    try:
        state = _state(project, legacy.session_id, revision=1)
        legacy.write_state(state)
        before = _bytes(legacy)
        with pytest.raises(StorageError, match="session format"):
            ProjectSafeSessionStoreV2(lease, legacy.session_id).write_action_payload(SHA256_A, "input.json", b"x")
        assert _bytes(legacy) == before
        assert legacy.state_path.read_bytes() == canonical_json_bytes(state.to_dict()) + b"\n"
    finally:
        lease.release()


@pytest.mark.parametrize("version", [1, 3, True, "2", None])
def test_v2_storage_rejects_unknown_or_mixed_state_envelope(durable, version):
    _, _, store = durable
    raw = store.read_state().to_dict()
    raw["schema_version"] = version
    store.state_path.write_bytes(canonical_json_bytes(raw) + b"\n")
    before = _bytes(store)
    with pytest.raises(StorageError):
        store.read_state()
    with pytest.raises(StorageError):
        store.write_action_payload(SHA256_A, "result.json", b"no")
    assert _bytes(store) == before


@pytest.mark.parametrize("family", ["state", "actions", "checkpoints", "events", "registry", "canonical_json", "workspace_digest"])
def test_v2_storage_requires_supported_format_tuple_before_payload_write(durable, family):
    _, _, store = durable
    raw = store.read_state().to_dict()
    raw["formats"][family] = 99
    store.state_path.write_bytes(canonical_json_bytes(raw) + b"\n")
    before = _bytes(store)
    with pytest.raises(StorageError):
        store.write_action_payload(SHA256_A, "input.json", b"no")
    assert _bytes(store) == before


@pytest.mark.parametrize("version", [1, 3, True, "2"])
def test_v2_storage_rejects_mixed_action_records_even_with_recomputed_hash(durable, version):
    _, _, store = durable
    record = store.append_action(_prepared(store))
    raw = record.to_dict()
    raw["schema_version"] = version
    _rehash(raw)
    store.actions_path.write_bytes(canonical_json_bytes(raw) + b"\n")
    before = _bytes(store)
    with pytest.raises(JournalCorruptionError):
        store.read_actions()
    assert _bytes(store) == before


@pytest.mark.parametrize("field", ["flow_creation_ref", "prepared_event_ref", "preceding_success_ref"])
def test_v2_storage_rejects_changed_reference_anchors_before_append_or_tail_repair(durable, field):
    _, _, store = durable
    draft = _prepared(store)
    store.append_action(draft)
    store.actions_path.write_bytes(store.actions_path.read_bytes().rstrip(b"\n"))
    bad = deepcopy(draft)
    bad.update(state="FAILED", reason="fixture failure")
    if field == "preceding_success_ref":
        bad[field] = {"journal_seq": 1, "record_hash": SHA256_B}
    else:
        bad[field]["record_hash"] = SHA256_B
        if field == "flow_creation_ref":
            bad["prepared_event_ref"]["record_hash"] = SHA256_B
    before = _bytes(store)
    with pytest.raises((StorageError, SchemaError)):
        store.append_action(bad)
    assert _bytes(store) == before


def test_v2_storage_preserves_complete_and_truncated_tail_behavior(durable):
    _, _, store = durable
    draft = _prepared(store)
    store.append_action(draft)
    store.actions_path.write_bytes(store.actions_path.read_bytes().rstrip(b"\n"))
    before = _bytes(store)
    assert store.read_actions().missing_trailing_newline
    assert _bytes(store) == before
    draft.update(state="FAILED", reason="fixture failure")
    store.append_action(draft)
    assert len(store.read_actions().records) == 2
    assert not store.read_actions().missing_trailing_newline
    store.actions_path.write_bytes(store.actions_path.read_bytes() + b'{"broken":')
    before = _bytes(store)
    with pytest.raises(TruncatedLastRecordError):
        store.read_actions()
    assert _bytes(store) == before


def test_v2_storage_validates_action_input_bytes_on_read(durable):
    _, _, store = durable
    record = store.append_action(_prepared(store))
    (store.session_dir / record.input.payload_ref).write_bytes(b"corrupt")
    with pytest.raises(PayloadIntegrityError):
        store.read_actions()


@pytest.mark.parametrize("corruption", ["backup", "missing_backup", "id", "session", "attempt", "version", "hash"])
def test_v2_storage_reads_validate_manifest_and_backup_bytes(durable, corruption):
    project, _, store = durable
    manifest, backups = _checkpoint(project, store)
    store.write_checkpoint(manifest, backups)
    directory = store.checkpoints_dir / manifest.checkpoint_id
    path = directory / "manifest.json"
    raw = manifest.to_dict()
    if corruption in {"backup", "missing_backup"}:
        backup = directory / next(iter(backups))
        if corruption == "backup":
            backup.write_bytes(b"corrupt")
        else:
            backup.unlink()
    else:
        if corruption == "id":
            raw["checkpoint_id"] = "11111111-1111-4111-8111-111111111111"
        elif corruption == "session":
            raw["session_id"] = "11111111-1111-4111-8111-111111111111"
        elif corruption == "attempt":
            raw["attempt_id"] = None
        elif corruption == "version":
            raw["schema_version"] = 1
        else:
            raw["manifest_sha256"] = SHA256_B
        if corruption != "hash":
            raw["manifest_sha256"] = checkpoint_manifest_sha256(raw)
        path.write_bytes(canonical_json_bytes(raw) + b"\n")
    before = _bytes(store)
    with pytest.raises(CheckpointIntegrityError):
        store.read_checkpoint(manifest.checkpoint_id)
    assert _bytes(store) == before


def test_v2_checkpoint_retry_is_byte_immutable_and_attempt_bound(durable):
    project, _, store = durable
    manifest, backups = _checkpoint(project, store)
    store.write_checkpoint(manifest, backups)
    before = _bytes(store)
    store.write_checkpoint(manifest, backups)
    assert _bytes(store) == before
    raw = manifest.to_dict()
    raw["attempt_id"] = SHA256_B
    raw["manifest_sha256"] = checkpoint_manifest_sha256(raw)
    with pytest.raises(CheckpointIntegrityError, match="different manifest"):
        store.write_checkpoint(CheckpointManifestV2.from_dict(raw), backups)
    assert _bytes(store) == before


@pytest.mark.parametrize("version", [1, 3, True])
def test_v2_storage_rejects_wrong_model_version_before_checkpoint_write(durable, version):
    project, _, store = durable
    manifest, backups = _checkpoint(project, store)
    before = _bytes(store)
    with pytest.raises(CheckpointIntegrityError):
        store.write_checkpoint(replace(manifest, schema_version=version), backups)
    assert _bytes(store) == before


def test_initial_state_cannot_mix_existing_action_journal(durable):
    project, lease, store = durable
    store.append_action(_prepared(store))
    store.state_path.unlink()
    legacy = ProjectSafeSessionStore(lease, store.session_id)
    before = _bytes(store)
    with pytest.raises(JournalCorruptionError):
        legacy.write_state(_state(project, store.session_id, revision=1))
    assert _bytes(store) == before


def test_v2_storage_success_transition_round_trip_has_explicit_checkpoint(durable):
    project, _, store = durable
    draft = _prepared(store)
    store.append_action(draft)
    for state in ["EXECUTING", "VERIFYING"]:
        draft["state"] = state
        store.append_action(draft)
    manifest, backups = _checkpoint(project, store)
    raw = manifest.to_dict()
    raw.update(kind="RESOURCE_AFTER", action_id=draft["action_id"], attempt_id=draft["attempt_id"])
    raw["manifest_sha256"] = checkpoint_manifest_sha256(raw)
    manifest = CheckpointManifestV2.from_dict(raw)
    store.write_checkpoint(manifest, backups)
    draft.update(state="SUCCEEDED", workspace_verification={
        "checkpoint_id": manifest.checkpoint_id, "manifest_sha256": manifest.manifest_sha256})
    draft["effect"]["status"] = "EXPECTED"
    success = store.append_action(draft)
    assert success.workspace_verification.checkpoint_id == manifest.checkpoint_id
    assert store.read_actions().records[-1] == success
    assert len(store.read_actions().records) == 4


def test_v2_storage_rejects_rehashed_reference_change_on_disk(durable):
    _, _, store = durable
    draft = _prepared(store)
    store.append_action(draft)
    draft.update(state="FAILED", reason="failure")
    store.append_action(draft)
    records = [r.to_dict() for r in store.read_actions().records]
    records[1]["prepared_event_ref"]["record_hash"] = SHA256_B
    # Keep equal-sequence local reference shape valid; break logical anchors too.
    records[1]["flow_creation_ref"]["record_hash"] = SHA256_B
    _rehash(records[1])
    store.actions_path.write_bytes(b"".join(canonical_json_bytes(r) + b"\n" for r in records))
    with pytest.raises(JournalCorruptionError, match="anchors"):
        store.read_actions()


@pytest.mark.parametrize("version", [True, 3])
def test_v2_storage_rejects_wrong_state_model_version_before_write(durable, version):
    _, _, store = durable
    state = store.read_state()
    before = _bytes(store)
    with pytest.raises(StorageError):
        store.write_state(replace(state, schema_version=version, state_revision=2))
    assert _bytes(store) == before


@pytest.mark.parametrize("corruption", ["missing", "bytes"])
def test_v2_storage_rejects_invalid_backup_payloads_before_publication(durable, corruption):
    project, _, store = durable
    manifest, backups = _checkpoint(project, store)
    bad = {} if corruption == "missing" else {key: b"bad" for key in backups}
    before = _bytes(store)
    with pytest.raises(CheckpointIntegrityError):
        store.write_checkpoint(manifest, bad)
    assert _bytes(store) == before


def test_v2_storage_inherits_shared_owner_gate(durable):
    from tests.test_project_safe_session_owner_gate import _blocked_then_completes
    _, lease, store = durable
    other = ProjectSafeSessionStoreV2(lease, store.session_id)
    assert _blocked_then_completes(lease, other.read_state) == store.read_state()


def test_v1_storage_rejects_mixed_format_tuple_before_payload_write(tmp_path):
    project, lease, store = _store(tmp_path)
    try:
        raw = _state(project, store.session_id, revision=1).to_dict()
        raw["formats"] = {"state": 2}
        store.session_dir.mkdir(parents=True)
        store.state_path.write_bytes(canonical_json_bytes(raw) + b"\n")
        before = _bytes(store)
        with pytest.raises(StorageError, match="format"):
            store.write_action_payload(SHA256_A, "result.json", b"no")
        assert _bytes(store) == before
    finally:
        lease.release()
