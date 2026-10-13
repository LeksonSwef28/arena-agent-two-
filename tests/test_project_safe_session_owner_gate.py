"""T90: coherent multi-object operations and lease-owner lifecycle."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Event
from uuid import uuid4

import pytest

from arena.project_safe_session import (
    ProjectLeaseError,
    ProjectRegistryStore,
    ProjectSafeSessionCoordinator,
    ProjectSafeSessionStore,
)
from arena.project_safe_session.recovery import ProjectSafeRecoveryManager
from arena.project_safe_session.registry import RegistryError
from arena.project_safe_session.storage import LeaseRequiredError
from tests.test_project_safe_session_checkpoints import _manifest, _store
from tests.test_project_safe_session_coordinator import NOW1, NOW2, _coordinator


def _blocked_then_completes(lease, call):
    attempted, finished = Event(), Event()

    def worker():
        attempted.set()
        try:
            return call()
        finally:
            finished.set()

    with ThreadPoolExecutor(max_workers=1) as pool:
        with lease.operation():
            future = pool.submit(worker)
            assert attempted.wait(5)
            assert not finished.wait(0.1), "operation escaped shared owner gate"
        return future.result(timeout=5)


@pytest.mark.parametrize("boundary", ["state", "actions", "events", "registry", "coordinator", "recovery"])
def test_owner_gate_blocks_independent_read_objects(tmp_path, boundary):
    _, lease, coordinator = _coordinator(tmp_path)
    try:
        state = coordinator.create_session(goal="gate", requested_mode="write", at=NOW1)
        store = ProjectSafeSessionStore(lease, state.session_id)
        calls = {"state": store.read_state, "actions": store.read_actions,
                 "events": store.read_events, "registry": ProjectRegistryStore(lease).read,
                 "coordinator": lambda: ProjectSafeSessionCoordinator(lease).read_session(state.session_id),
                 "recovery": lambda: ProjectSafeRecoveryManager(lease).assess(state.session_id)}
        _blocked_then_completes(lease, calls[boundary])
    finally:
        lease.release()


def test_owner_gate_serializes_registry_read_modify_write_across_objects(tmp_path, monkeypatch):
    _, lease, _ = _coordinator(tmp_path)
    first, second = ProjectRegistryStore(lease), ProjectRegistryStore(lease)
    reached, proceed, second_attempted, second_done = Event(), Event(), Event(), Event()
    original = first.write

    def paused_write(*args, **kwargs):
        reached.set()
        assert proceed.wait(5)
        return original(*args, **kwargs)

    monkeypatch.setattr(first, "write", paused_write)
    a, b = str(uuid4()), str(uuid4())

    def next_registration():
        second_attempted.set()
        try:
            return second.register_session(b, at=NOW2)
        finally:
            second_done.set()

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            one = pool.submit(first.register_session, a, at=NOW1)
            try:
                assert reached.wait(5)
                two = pool.submit(next_registration)
                assert second_attempted.wait(5)
                assert not second_done.wait(0.1)
            finally:
                proceed.set()
            one.result(timeout=5)
            two.result(timeout=5)
        assert set(first.read().session_ids) == {a, b}
    finally:
        lease.release()


def test_owner_gate_assessment_waits_for_coordinator_event_state_publication(tmp_path, monkeypatch):
    _, lease, coordinator = _coordinator(tmp_path)
    state = coordinator.create_session(goal="gate", requested_mode="write", at=NOW1)
    reached, proceed, attempted, finished = Event(), Event(), Event(), Event()
    original = ProjectSafeSessionStore.write_state

    def paused_state(store, *args, **kwargs):
        reached.set()
        assert proceed.wait(5)
        return original(store, *args, **kwargs)

    monkeypatch.setattr(ProjectSafeSessionStore, "write_state", paused_state)

    def assess():
        attempted.set()
        try:
            return ProjectSafeRecoveryManager(lease).assess(state.session_id)
        finally:
            finished.set()

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            writer = pool.submit(coordinator.activate_session, state.session_id, at=NOW2)
            try:
                assert reached.wait(5)
                reader = pool.submit(assess)
                assert attempted.wait(5)
                assert not finished.wait(0.1)
            finally:
                proceed.set()
            writer.result(timeout=5)
            assert reader.result(timeout=5).required is False
    finally:
        lease.release()


def test_owner_gate_release_waits_for_other_thread_operation(tmp_path):
    _, lease, _ = _coordinator(tmp_path)
    _blocked_then_completes(lease, lease.release)
    assert not lease.held


def test_owner_gate_reentrant_release_is_rejected_and_exception_unwinds(tmp_path):
    _, lease, coordinator = _coordinator(tmp_path)
    try:
        with pytest.raises(RuntimeError, match="test unwind"):
            with lease.operation():
                with lease.operation():
                    coordinator.create_session(goal="nested", requested_mode="write", at=NOW1)
                    with pytest.raises(ProjectLeaseError, match="during an owner operation"):
                        lease.release()
                    assert lease.held
                    raise RuntimeError("test unwind")
        lease.release()
        assert not lease.held
    finally:
        lease.release()


@pytest.mark.parametrize("reacquire", [False, True])
def test_owner_gate_old_bound_objects_fail_after_owner_loss(tmp_path, reacquire):
    _, lease, coordinator = _coordinator(tmp_path)
    state = coordinator.create_session(goal="generation", requested_mode="write", at=NOW1)
    store = ProjectSafeSessionStore(lease, state.session_id)
    registry, recovery = ProjectRegistryStore(lease), ProjectSafeRecoveryManager(lease)
    lease.release()
    try:
        if reacquire:
            lease.acquire()
        for call, error in [(store.read_state, LeaseRequiredError), (registry.read, RegistryError),
                            (lambda: coordinator.read_session(state.session_id), LeaseRequiredError),
                            (lambda: recovery.assess(state.session_id), LeaseRequiredError)]:
            with pytest.raises(error):
                call()
        if reacquire:
            assert ProjectSafeSessionStore(lease, state.session_id).read_state() == state
    finally:
        lease.release()


def test_owner_gate_checkpoint_writer_from_another_store_waits(tmp_path):
    project, lease, first = _store(tmp_path)
    try:
        second = ProjectSafeSessionStore(lease, first.session_id)
        manifest, backups = _manifest(first, project, b"evidence")
        _blocked_then_completes(lease, lambda: second.write_checkpoint(manifest, backups))
        assert first.read_checkpoint(manifest.checkpoint_id) == manifest
    finally:
        lease.release()


def test_owner_gate_unheld_operation_fails_and_distinct_projects_do_not_block(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    from arena.project_safe_session import ProjectLease
    first, second = ProjectLease(a, tmp_path / "state-a"), ProjectLease(b, tmp_path / "state-b")
    with pytest.raises(ProjectLeaseError):
        with first.operation():
            pytest.fail("unheld lease entered operation")
    with pytest.raises(ProjectLeaseError):
        with first.operation(validate=lambda: None):
            pytest.fail("validator bypassed live lease check")
    first.acquire()
    second.acquire()
    try:
        with first.operation():
            with ThreadPoolExecutor(max_workers=1) as pool:
                assert pool.submit(ProjectRegistryStore(second).read).result(timeout=5) is None
    finally:
        first.release()
        second.release()


def test_owner_gate_activation_covers_registry_before_event_transaction(tmp_path, monkeypatch):
    _, lease, coordinator = _coordinator(tmp_path)
    state = coordinator.create_session(goal="gate", requested_mode="write", at=NOW1)
    reached, proceed, attempted, finished = Event(), Event(), Event(), Event()
    original = coordinator.registry.activate_session

    def paused_activation(*args, **kwargs):
        result = original(*args, **kwargs)
        reached.set()
        assert proceed.wait(5)
        return result

    monkeypatch.setattr(coordinator.registry, "activate_session", paused_activation)

    def assess():
        attempted.set()
        try:
            return ProjectSafeRecoveryManager(lease).assess(state.session_id)
        finally:
            finished.set()

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            writer = pool.submit(coordinator.activate_session, state.session_id, at=NOW2)
            try:
                assert reached.wait(5)
                reader = pool.submit(assess)
                assert attempted.wait(5)
                assert not finished.wait(0.1)
            finally:
                proceed.set()
            writer.result(timeout=5)
            assert reader.result(timeout=5).required is False
    finally:
        lease.release()


def test_owner_gate_assessment_holds_coherent_read_across_store_calls(tmp_path, monkeypatch):
    _, lease, coordinator = _coordinator(tmp_path)
    state = coordinator.create_session(goal="gate", requested_mode="write", at=NOW1)
    reached, proceed, attempted, finished = Event(), Event(), Event(), Event()
    original = ProjectSafeSessionStore.read_state

    def paused_read(store):
        result = original(store)
        reached.set()
        assert proceed.wait(5)
        return result

    monkeypatch.setattr(ProjectSafeSessionStore, "read_state", paused_read)

    def mutate():
        attempted.set()
        try:
            return ProjectRegistryStore(lease).register_session(str(uuid4()), at=NOW2)
        finally:
            finished.set()

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            reader = pool.submit(ProjectSafeRecoveryManager(lease).assess, state.session_id)
            try:
                assert reached.wait(5)
                writer = pool.submit(mutate)
                assert attempted.wait(5)
                assert not finished.wait(0.1)
            finally:
                proceed.set()
            assert reader.result(timeout=5).required is False
            writer.result(timeout=5)
    finally:
        lease.release()
