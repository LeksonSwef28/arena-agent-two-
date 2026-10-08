"""P1-A6 durable checkpoint publication/integrity regression tests."""
from __future__ import annotations

import hashlib
import json
import shutil
import uuid
from pathlib import Path

import pytest

import arena.project_safe_session.checkpointing as checkpointing_module
import arena.project_safe_session.storage as storage_module
from arena.project_safe_session import (
    CheckpointContractError,
    CheckpointIntegrityError,
    CheckpointManifest,
    FileResourceBefore,
    LeaseRequiredError,
    ProjectLease,
    ProjectSafeSessionStore,
    ResourceDriftError,
    build_checkpoint_manifest,
    checkpoint_manifest_sha256,
    checkpoint_resource_from_file_before,
    create_file_resource_before_checkpoint,
    project_fingerprint,
)

SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64
HEAD = "d" * 40
NOW = "2026-10-07T03:00:00Z"


def _uuid() -> str:
    return str(uuid.uuid4())


def _store(tmp_path: Path):
    project = tmp_path / "project"
    project.mkdir()
    state = tmp_path / "state"
    lease = ProjectLease(project, state).acquire()
    store = ProjectSafeSessionStore(lease, _uuid())
    return project, lease, store


def _manifest(store: ProjectSafeSessionStore, project: Path, data: bytes):
    digest = hashlib.sha256(data).hexdigest()
    resource_before = FileResourceBefore.from_dict(
        {
            "project_fingerprint": project_fingerprint(project),
            "canonical_relative_path": "foo.bin",
            "resource_type": "file",
            "exists": True,
            "content_sha256": digest,
        }
    )
    checkpoint_resource = checkpoint_resource_from_file_before(
        resource_before,
        data,
    )
    manifest = build_checkpoint_manifest(
        checkpoint_id=_uuid(),
        session_id=store.session_id,
        action_id=SHA_A,
        kind="RESOURCE_BEFORE",
        created_at=NOW,
        workspace_digest=SHA_B,
        head_sha=HEAD,
        resources=[checkpoint_resource],
    )
    assert checkpoint_resource.backup_ref is not None
    return manifest, {checkpoint_resource.backup_ref: data}


def test_checkpoint_publishes_backups_before_manifest_and_round_trips(tmp_path: Path):
    project, lease, store = _store(tmp_path)
    try:
        manifest, backups = _manifest(store, project, b"before-bytes")

        store.write_checkpoint(manifest, backups)

        checkpoint_dir = store.checkpoints_dir / manifest.checkpoint_id
        assert (checkpoint_dir / "manifest.json").exists()
        assert store.read_checkpoint(manifest.checkpoint_id) == manifest
        for ref, expected in backups.items():
            assert (checkpoint_dir / ref).read_bytes() == expected
    finally:
        lease.release()


def test_checkpoint_write_is_idempotent_for_identical_manifest(tmp_path: Path):
    project, lease, store = _store(tmp_path)
    try:
        manifest, backups = _manifest(store, project, b"same")
        store.write_checkpoint(manifest, backups)
        store.write_checkpoint(manifest, backups)

        assert store.read_checkpoint(manifest.checkpoint_id) == manifest
    finally:
        lease.release()


def test_checkpoint_manifest_tamper_is_detected(tmp_path: Path):
    project, lease, store = _store(tmp_path)
    try:
        manifest, backups = _manifest(store, project, b"before")
        store.write_checkpoint(manifest, backups)

        path = store.checkpoints_dir / manifest.checkpoint_id / "manifest.json"
        raw = json.loads(path.read_text(encoding="utf-8"))
        raw["workspace_digest"] = SHA_C
        path.write_text(
            json.dumps(raw, sort_keys=True, separators=(",", ":")),
            encoding="utf-8",
        )

        with pytest.raises(CheckpointIntegrityError, match="manifest digest mismatch"):
            store.read_checkpoint(manifest.checkpoint_id)
    finally:
        lease.release()


def test_checkpoint_backup_tamper_and_missing_backup_are_detected(tmp_path: Path):
    project, lease, store = _store(tmp_path)
    try:
        manifest, backups = _manifest(store, project, b"before")
        store.write_checkpoint(manifest, backups)
        ref = next(iter(backups))
        path = store.checkpoints_dir / manifest.checkpoint_id / ref

        path.write_bytes(b"tampered")
        with pytest.raises(CheckpointIntegrityError, match="backup"):
            store.read_checkpoint(manifest.checkpoint_id)

        path.unlink()
        with pytest.raises(CheckpointIntegrityError, match="missing checkpoint backup"):
            store.read_checkpoint(manifest.checkpoint_id)
    finally:
        lease.release()


def test_partial_checkpoint_without_manifest_is_unpublished_and_retryable(tmp_path: Path):
    project, lease, store = _store(tmp_path)
    try:
        manifest, backups = _manifest(store, project, b"before")
        ref, data = next(iter(backups.items()))
        checkpoint_dir = store.checkpoints_dir / manifest.checkpoint_id
        partial = checkpoint_dir / ref
        partial.parent.mkdir(parents=True)
        partial.write_bytes(data)

        with pytest.raises(CheckpointIntegrityError, match="not published"):
            store.read_checkpoint(manifest.checkpoint_id)

        store.write_checkpoint(manifest, backups)
        assert store.read_checkpoint(manifest.checkpoint_id) == manifest
    finally:
        lease.release()


def test_conflicting_partial_backup_fails_closed(tmp_path: Path):
    project, lease, store = _store(tmp_path)
    try:
        manifest, backups = _manifest(store, project, b"before")
        ref = next(iter(backups))
        path = store.checkpoints_dir / manifest.checkpoint_id / ref
        path.parent.mkdir(parents=True)
        path.write_bytes(b"different")

        with pytest.raises(CheckpointIntegrityError, match="conflicts with retry"):
            store.write_checkpoint(manifest, backups)
        assert not (path.parents[1] / "manifest.json").exists()
    finally:
        lease.release()


def test_fault_before_manifest_leaves_unpublished_checkpoint(tmp_path: Path, monkeypatch):
    project, lease, store = _store(tmp_path)
    try:
        manifest, backups = _manifest(store, project, b"before")
        original = storage_module.durable_replace

        def fail_manifest(path: Path, data: bytes):
            if path.name == "manifest.json":
                raise OSError("simulated crash before publication")
            return original(path, data)

        monkeypatch.setattr(storage_module, "durable_replace", fail_manifest)
        with pytest.raises(OSError, match="simulated crash"):
            store.write_checkpoint(manifest, backups)

        checkpoint_dir = store.checkpoints_dir / manifest.checkpoint_id
        assert any((checkpoint_dir / ref).exists() for ref in backups)
        assert not (checkpoint_dir / "manifest.json").exists()

        with pytest.raises(CheckpointIntegrityError, match="not published"):
            store.read_checkpoint(manifest.checkpoint_id)
    finally:
        lease.release()


def test_high_level_resource_before_checkpoint_captures_exact_bytes(tmp_path: Path):
    project, lease, store = _store(tmp_path)
    try:
        target = project / "foo.py"
        target.write_bytes(b"original\r\nbytes\n")

        manifest, resource_before = create_file_resource_before_checkpoint(
            store,
            project,
            action_id=SHA_A,
            canonical_relative_path="foo.py",
            workspace_digest=SHA_B,
            head_sha=HEAD,
            checkpoint_id=_uuid(),
            created_at=NOW,
        )

        assert resource_before.content_sha256 == hashlib.sha256(target.read_bytes()).hexdigest()
        loaded = store.read_checkpoint(manifest.checkpoint_id)
        resource = loaded.resources[0]
        assert resource.backup_ref is not None
        backup = store.checkpoints_dir / manifest.checkpoint_id / resource.backup_ref
        assert backup.read_bytes() == b"original\r\nbytes\n"
    finally:
        lease.release()


def test_high_level_checkpoint_rejects_target_drift_during_backup_capture(
    tmp_path: Path,
    monkeypatch,
):
    project, lease, store = _store(tmp_path)
    try:
        target = project / "foo.py"
        target.write_text("v1", encoding="utf-8")
        original = checkpointing_module.read_file_resource_backup_v1

        def drift_then_read(root, resource_before):
            target.write_text("v2", encoding="utf-8")
            return original(root, resource_before)

        monkeypatch.setattr(
            checkpointing_module,
            "read_file_resource_backup_v1",
            drift_then_read,
        )

        with pytest.raises(ResourceDriftError):
            create_file_resource_before_checkpoint(
                store,
                project,
                action_id=SHA_A,
                canonical_relative_path="foo.py",
                workspace_digest=SHA_B,
                head_sha=HEAD,
                checkpoint_id=_uuid(),
                created_at=NOW,
            )
    finally:
        lease.release()


def test_absent_resource_checkpoint_has_no_backup_payload(tmp_path: Path):
    project, lease, store = _store(tmp_path)
    try:
        manifest, resource_before = create_file_resource_before_checkpoint(
            store,
            project,
            action_id=SHA_A,
            canonical_relative_path="new.txt",
            workspace_digest=SHA_B,
            head_sha=HEAD,
            checkpoint_id=_uuid(),
            created_at=NOW,
        )

        assert resource_before.exists is False
        loaded = store.read_checkpoint(manifest.checkpoint_id)
        assert loaded.resources[0].backup_ref is None
        files_dir = store.checkpoints_dir / manifest.checkpoint_id / "files"
        assert not files_dir.exists()
    finally:
        lease.release()


def test_security_state_reads_fail_after_lease_release(tmp_path: Path):
    project, lease, store = _store(tmp_path)
    manifest, backups = _manifest(store, project, b"before")
    store.write_checkpoint(manifest, backups)
    lease.release()

    with pytest.raises(LeaseRequiredError):
        store.read_checkpoint(manifest.checkpoint_id)
    with pytest.raises(LeaseRequiredError):
        store.read_actions()
    with pytest.raises(LeaseRequiredError):
        store.read_events()
    with pytest.raises(LeaseRequiredError):
        store.read_state()


INVALID_SEMANTICS = [
    ("SESSION_BASELINE", SHA_A, False, "must not have action_id"),
    ("RESOURCE_BEFORE", None, False, "requires action_id"),
    ("RESOURCE_AFTER", None, False, "requires action_id"),
    ("RESOURCE_BEFORE", SHA_A, True, "requires at least one resource"),
    ("RESOURCE_AFTER", SHA_A, True, "requires at least one resource"),
]


def test_checkpoint_copied_to_another_id_is_rejected(tmp_path: Path):
    project, lease, store = _store(tmp_path)
    try:
        manifest, backups = _manifest(store, project, b"before")
        store.write_checkpoint(manifest, backups)
        other_id = _uuid()
        shutil.copytree(
            store.checkpoints_dir / manifest.checkpoint_id,
            store.checkpoints_dir / other_id,
        )
        with pytest.raises(CheckpointIntegrityError, match="checkpoint_id"):
            store.read_checkpoint(other_id)
        assert store.read_checkpoint(manifest.checkpoint_id) == manifest
    finally:
        lease.release()


@pytest.mark.parametrize("kind,action_id,empty_resources,message", INVALID_SEMANTICS)
def test_rehashed_semantically_invalid_checkpoint_is_rejected(
    tmp_path: Path, kind, action_id, empty_resources, message,
):
    project, lease, store = _store(tmp_path)
    try:
        manifest, backups = _manifest(store, project, b"before")
        store.write_checkpoint(manifest, backups)
        raw = manifest.to_dict()
        raw.update(kind=kind, action_id=action_id)
        if empty_resources:
            raw["resources"] = []
        raw["manifest_sha256"] = checkpoint_manifest_sha256(raw)
        path = store.checkpoints_dir / manifest.checkpoint_id / "manifest.json"
        path.write_text(json.dumps(raw), encoding="utf-8")
        with pytest.raises(ValueError, match=message):
            CheckpointManifest.from_dict(raw)
        with pytest.raises(CheckpointIntegrityError, match=message):
            store.read_checkpoint(manifest.checkpoint_id)
    finally:
        lease.release()


@pytest.mark.parametrize("kind,action_id,empty_resources,message", INVALID_SEMANTICS)
def test_builder_preserves_semantic_contract_errors(
    tmp_path: Path, kind, action_id, empty_resources, message,
):
    project, lease, store = _store(tmp_path)
    try:
        manifest, _ = _manifest(store, project, b"before")
        with pytest.raises(CheckpointContractError, match=message):
            build_checkpoint_manifest(
                checkpoint_id=_uuid(), session_id=store.session_id,
                action_id=action_id, kind=kind, created_at=NOW,
                workspace_digest=SHA_B, head_sha=HEAD,
                resources=[] if empty_resources else manifest.resources,
            )
    finally:
        lease.release()


@pytest.mark.parametrize("kind", ["SESSION_BASELINE", "RESOURCE_AFTER"])
def test_other_valid_checkpoint_kinds_round_trip(tmp_path: Path, kind):
    project, lease, store = _store(tmp_path)
    try:
        before, backups = _manifest(store, project, b"before")
        manifest = build_checkpoint_manifest(
            checkpoint_id=_uuid(), session_id=store.session_id,
            action_id=None if kind == "SESSION_BASELINE" else SHA_A,
            kind=kind, created_at=NOW, workspace_digest=SHA_B, head_sha=HEAD,
            resources=[] if kind == "SESSION_BASELINE" else before.resources,
        )
        store.write_checkpoint(manifest, {} if kind == "SESSION_BASELINE" else backups)
        assert store.read_checkpoint(manifest.checkpoint_id) == manifest
    finally:
        lease.release()
