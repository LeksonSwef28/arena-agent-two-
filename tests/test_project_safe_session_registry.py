"""P1-B0 project registry single-active-session contract tests."""
from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from arena.project_safe_session import (
    ActiveSessionConflictError,
    ProjectLease,
    ProjectRegistry,
    ProjectRegistryStore,
    RegistryError,
    RegistryRevisionError,
    SchemaError,
)

NOW1 = "2026-10-07T03:10:00Z"
NOW2 = "2026-10-07T03:11:00Z"
NOW3 = "2026-10-07T03:12:00Z"


def _uuid() -> str:
    return str(uuid.uuid4())


def _store(tmp_path: Path):
    project = tmp_path / "project"
    project.mkdir()
    state = tmp_path / "state"
    lease = ProjectLease(project, state).acquire()
    return lease, ProjectRegistryStore(lease)


def test_registry_requires_live_project_lease(tmp_path: Path):
    project = tmp_path / "project"
    project.mkdir()
    lease = ProjectLease(project, tmp_path / "state")

    with pytest.raises(RegistryError, match="live ProjectLease"):
        ProjectRegistryStore(lease)


def test_registry_registers_multiple_sessions_but_only_one_active(tmp_path: Path):
    lease, store = _store(tmp_path)
    first = _uuid()
    second = _uuid()
    try:
        r1 = store.register_session(first, at=NOW1)
        assert r1.registry_revision == 1
        assert r1.session_ids == (first,)
        assert r1.active_session_id is None

        r2 = store.register_session(second, at=NOW2)
        assert r2.registry_revision == 2
        assert r2.session_ids == (first, second)

        active = store.activate_session(first, at=NOW3)
        assert active.registry_revision == 3
        assert active.active_session_id == first

        with pytest.raises(ActiveSessionConflictError, match="already has active"):
            store.activate_session(second, at=NOW3)

        reread = store.read()
        assert reread == active
        assert reread is not None and reread.active_session_id == first
    finally:
        lease.release()


def test_registry_activate_is_idempotent_for_same_session(tmp_path: Path):
    lease, store = _store(tmp_path)
    session = _uuid()
    try:
        store.register_session(session, at=NOW1)
        first = store.activate_session(session, at=NOW2)
        second = store.activate_session(session, at=NOW3)

        assert second == first
        assert second.registry_revision == 2
    finally:
        lease.release()


def test_clear_active_session_allows_explicit_switch(tmp_path: Path):
    lease, store = _store(tmp_path)
    first = _uuid()
    second = _uuid()
    try:
        store.register_session(first, at=NOW1)
        store.register_session(second, at=NOW1)
        active = store.activate_session(first, at=NOW2)

        cleared = store.clear_active_session(first, at=NOW2)
        assert cleared.active_session_id is None
        assert cleared.registry_revision == active.registry_revision + 1

        switched = store.activate_session(second, at=NOW3)
        assert switched.active_session_id == second
    finally:
        lease.release()


def test_wrong_session_cannot_clear_another_active_session(tmp_path: Path):
    lease, store = _store(tmp_path)
    first = _uuid()
    second = _uuid()
    try:
        store.register_session(first, at=NOW1)
        store.register_session(second, at=NOW1)
        store.activate_session(first, at=NOW2)

        with pytest.raises(ActiveSessionConflictError, match="owned by"):
            store.clear_active_session(second, at=NOW3)
    finally:
        lease.release()


def test_registry_revision_compare_and_advance(tmp_path: Path):
    lease, store = _store(tmp_path)
    session = _uuid()
    try:
        current = store.register_session(session, at=NOW1)
        invalid = ProjectRegistry.from_dict(
            {
                **current.to_dict(),
                "registry_revision": current.registry_revision + 2,
                "updated_at": NOW2,
            }
        )

        with pytest.raises(RegistryRevisionError, match="next registry_revision"):
            store.write(invalid, expected_current_revision=current.registry_revision)

        next_valid = ProjectRegistry.from_dict(
            {
                **current.to_dict(),
                "registry_revision": current.registry_revision + 1,
                "updated_at": NOW2,
            }
        )
        with pytest.raises(RegistryRevisionError, match="revision changed"):
            store.write(next_valid, expected_current_revision=0)
    finally:
        lease.release()


def test_registry_schema_rejects_active_session_not_registered():
    active = _uuid()

    with pytest.raises(SchemaError, match="must be present"):
        ProjectRegistry.from_dict(
            {
                "schema_version": 1,
                "registry_revision": 1,
                "project_fingerprint": "a" * 64,
                "active_session_id": active,
                "session_ids": [],
                "created_at": NOW1,
                "updated_at": NOW1,
            }
        )


def test_registry_schema_rejects_duplicate_session_ids():
    session = _uuid()

    with pytest.raises(SchemaError, match="duplicates"):
        ProjectRegistry.from_dict(
            {
                "schema_version": 1,
                "registry_revision": 1,
                "project_fingerprint": "a" * 64,
                "active_session_id": None,
                "session_ids": [session, session],
                "created_at": NOW1,
                "updated_at": NOW1,
            }
        )


def test_registry_reads_fail_after_lease_release(tmp_path: Path):
    lease, store = _store(tmp_path)
    store.register_session(_uuid(), at=NOW1)
    lease.release()

    with pytest.raises(RegistryError, match="lease is no longer held"):
        store.read()
