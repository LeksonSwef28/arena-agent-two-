"""P1-B1 logical session and flow lifecycle tests."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

import arena.project_safe_session.coordinator as coordinator_module
from arena.project_safe_session import (
    ActiveSessionConflictError,
    ProjectLease,
    ProjectRegistryStore,
    ProjectSafeSessionCoordinator,
    SessionCoordinatorError,
    normalize_goal_v1,
    session_fingerprint_v1,
)
from arena.project_safe_session.workspace import WorkspaceError, WorkspaceUnstableError

NOW1 = "2026-10-07T03:20:00Z"
NOW2 = "2026-10-07T03:21:00Z"
NOW3 = "2026-10-07T03:22:00Z"
NOW4 = "2026-10-07T03:23:00Z"


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        pytest.fail(f"git {' '.join(args)} failed: {result.stderr}")
    return result.stdout.strip()


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.name", "arena-test")
    _git(repo, "config", "user.email", "arena-test@example.invalid")
    (repo / ".gitattributes").write_text("* text eol=lf\n", encoding="utf-8", newline="")
    (repo / "foo.py").write_text("foo = 1\n", encoding="utf-8", newline="")
    _git(repo, "add", ".gitattributes", "foo.py")
    _git(repo, "commit", "-m", "baseline")
    return repo


def _coordinator(tmp_path: Path):
    repo = _repo(tmp_path)
    lease = ProjectLease(repo, tmp_path / "state").acquire()
    return repo, lease, ProjectSafeSessionCoordinator(lease)


def test_create_session_publishes_baseline_state_event_and_registry(tmp_path: Path):
    repo, lease, coordinator = _coordinator(tmp_path)
    try:
        state = coordinator.create_session(
            goal="  Fix   preflight  ",
            requested_mode="write",
            required_roles=["MAIN_GPT"],
            at=NOW1,
        )

        assert state.goal.initial == "Fix preflight"
        assert state.goal.current == "Fix preflight"
        assert state.lifecycle.status.value == "CREATED"
        assert state.lifecycle.phase.value == "IDLE"
        assert state.active_flow is None
        assert state.execution.last_event_seq == 1
        assert state.workspace.session_baseline.clean is True
        assert state.workspace.workspace_digest_last_verified == (
            state.workspace.session_baseline.workspace_digest
        )

        store = coordinator._store(state.session_id)
        checkpoint = store.read_checkpoint(
            state.workspace.session_baseline.checkpoint_id
        )
        assert checkpoint.kind.value == "SESSION_BASELINE"
        assert checkpoint.action_id is None
        assert checkpoint.resources == ()

        events = store.read_events().records
        assert len(events) == 1
        assert events[0].event_type.value == "SESSION_CREATED"

        registry = ProjectRegistryStore(lease).read()
        assert registry is not None
        assert registry.session_ids == (state.session_id,)
        assert registry.active_session_id is None
    finally:
        lease.release()


def test_activate_then_start_clean_write_flow(tmp_path: Path):
    _, lease, coordinator = _coordinator(tmp_path)
    try:
        created = coordinator.create_session(
            goal="Change foo",
            requested_mode="write",
            at=NOW1,
        )
        active = coordinator.activate_session(created.session_id, at=NOW2)
        started = coordinator.start_flow(created.session_id, at=NOW3)

        assert active.lifecycle.status.value == "ACTIVE"
        assert started.lifecycle.status.value == "ACTIVE"
        assert started.lifecycle.phase.value == "PLANNING"
        assert started.active_flow is not None
        assert started.active_flow.goal_revision == started.goal.revision
        assert (
            started.active_flow.workspace_digest_baseline
            == started.active_flow.workspace_digest_expected_current
        )

        registry = ProjectRegistryStore(lease).read()
        assert registry is not None
        assert registry.active_session_id == created.session_id
    finally:
        lease.release()


def test_dirty_write_flow_waits_then_starts_after_user_cleans_workspace(tmp_path: Path):
    repo, lease, coordinator = _coordinator(tmp_path)
    try:
        created = coordinator.create_session(
            goal="Change foo",
            requested_mode="write",
            at=NOW1,
        )
        coordinator.activate_session(created.session_id, at=NOW2)

        dirty = repo / "note.txt"
        dirty.write_text("user change\n", encoding="utf-8")
        waiting = coordinator.start_flow(created.session_id, at=NOW3)

        assert waiting.lifecycle.status.value == "WAITING"
        assert waiting.lifecycle.reason is not None
        assert waiting.lifecycle.reason.value == "WORKSPACE_DIRTY"
        assert waiting.active_flow is None

        dirty.unlink()
        started = coordinator.start_flow(created.session_id, at=NOW4)
        assert started.lifecycle.status.value == "ACTIVE"
        assert started.lifecycle.reason is None
        assert started.active_flow is not None

        event_types = [
            event.event_type.value
            for event in coordinator._store(created.session_id).read_events().records
        ]
        assert event_types[-2:] == ["STATUS_CHANGED", "FLOW_CREATED"]
    finally:
        lease.release()


def test_read_session_can_start_flow_from_dirty_workspace(tmp_path: Path):
    repo, lease, coordinator = _coordinator(tmp_path)
    try:
        (repo / "user-note.txt").write_text("dirty\n", encoding="utf-8")
        created = coordinator.create_session(
            goal="Inspect repository",
            requested_mode="read",
            at=NOW1,
        )
        assert created.workspace.session_baseline.clean is False

        coordinator.activate_session(created.session_id, at=NOW2)
        started = coordinator.start_flow(created.session_id, at=NOW3)

        assert started.active_flow is not None
        assert started.lifecycle.status.value == "ACTIVE"
    finally:
        lease.release()


def test_pause_and_resume_keep_same_active_flow(tmp_path: Path):
    _, lease, coordinator = _coordinator(tmp_path)
    try:
        created = coordinator.create_session(
            goal="Flow",
            requested_mode="write",
            at=NOW1,
        )
        coordinator.activate_session(created.session_id, at=NOW2)
        started = coordinator.start_flow(created.session_id, at=NOW3)
        assert started.active_flow is not None
        flow_id = started.active_flow.flow_id

        paused = coordinator.pause_session(created.session_id, at=NOW4)
        assert paused.lifecycle.status.value == "PAUSED"
        assert paused.active_flow is not None
        assert paused.active_flow.flow_id == flow_id
        registry = ProjectRegistryStore(lease).read()
        assert registry is not None and registry.active_session_id is None

        resumed = coordinator.activate_session(created.session_id, at=NOW4)
        assert resumed.lifecycle.status.value == "ACTIVE"
        assert resumed.active_flow is not None
        assert resumed.active_flow.flow_id == flow_id
    finally:
        lease.release()


def test_refine_goal_explicitly_closes_old_flow_and_increments_revision(tmp_path: Path):
    _, lease, coordinator = _coordinator(tmp_path)
    try:
        created = coordinator.create_session(
            goal="Fix preflight",
            requested_mode="write",
            at=NOW1,
        )
        coordinator.activate_session(created.session_id, at=NOW2)
        started = coordinator.start_flow(created.session_id, at=NOW3)
        assert started.active_flow is not None
        old_flow = started.active_flow.flow_id

        refined = coordinator.refine_goal(
            created.session_id,
            new_goal="Rewrite input validation",
            at=NOW4,
        )

        assert refined.goal.initial == "Fix preflight"
        assert refined.goal.current == "Rewrite input validation"
        assert refined.goal.revision == 2
        assert refined.active_flow is None
        assert refined.lifecycle.phase.value == "IDLE"

        events = coordinator._store(created.session_id).read_events().records
        assert events[-2].event_type.value == "FLOW_CLOSED"
        assert events[-2].data["flow_id"] == old_flow
        assert events[-1].event_type.value == "GOAL_REFINED"
        assert events[-1].data["revision"] == 2
    finally:
        lease.release()


def test_second_session_cannot_activate_until_first_is_paused(tmp_path: Path):
    _, lease, coordinator = _coordinator(tmp_path)
    try:
        first = coordinator.create_session(
            goal="Task one",
            requested_mode="write",
            at=NOW1,
        )
        second = coordinator.create_session(
            goal="Task two",
            requested_mode="write",
            at=NOW1,
        )
        coordinator.activate_session(first.session_id, at=NOW2)

        with pytest.raises(ActiveSessionConflictError):
            coordinator.activate_session(second.session_id, at=NOW3)

        coordinator.pause_session(first.session_id, at=NOW3)
        activated_second = coordinator.activate_session(second.session_id, at=NOW4)
        assert activated_second.lifecycle.status.value == "ACTIVE"
    finally:
        lease.release()


def test_start_flow_requires_project_active_slot(tmp_path: Path):
    _, lease, coordinator = _coordinator(tmp_path)
    try:
        created = coordinator.create_session(
            goal="Task",
            requested_mode="write",
            at=NOW1,
        )

        with pytest.raises(SessionCoordinatorError, match="active project slot"):
            coordinator.start_flow(created.session_id, at=NOW2)
    finally:
        lease.release()


def test_goal_normalization_and_fingerprint_are_stable():
    project = "a" * 64
    assert normalize_goal_v1("  Fix\n\tpreflight ") == "Fix preflight"
    assert session_fingerprint_v1(project, "Fix  preflight") == session_fingerprint_v1(
        project,
        " Fix\npreflight ",
    )


def test_resume_paused_flow_with_workspace_drift_enters_waiting(tmp_path: Path):
    repo, lease, coordinator = _coordinator(tmp_path)
    try:
        created = coordinator.create_session(
            goal="Flow",
            requested_mode="write",
            at=NOW1,
        )
        coordinator.activate_session(created.session_id, at=NOW2)
        started = coordinator.start_flow(created.session_id, at=NOW3)
        assert started.active_flow is not None
        coordinator.pause_session(created.session_id, at=NOW4)

        (repo / "external.txt").write_text("external change\n", encoding="utf-8")

        resumed = coordinator.activate_session(created.session_id, at=NOW4)
        assert resumed.lifecycle.status.value == "WAITING"
        assert resumed.lifecycle.reason is not None
        assert resumed.lifecycle.reason.value == "WORKSPACE_DRIFT"
        assert resumed.active_flow is not None

        registry = ProjectRegistryStore(lease).read()
        assert registry is not None
        assert registry.active_session_id == created.session_id
    finally:
        lease.release()


@pytest.mark.parametrize("error_type", [WorkspaceError, WorkspaceUnstableError, OSError])
def test_failed_resume_evidence_preserves_admission_and_can_retry(
    tmp_path: Path, monkeypatch, error_type,
):
    _, lease, coordinator = _coordinator(tmp_path)
    try:
        created = coordinator.create_session(goal="Flow", requested_mode="write", at=NOW1)
        coordinator.activate_session(created.session_id, at=NOW2)
        started = coordinator.start_flow(created.session_id, at=NOW3)
        paused = coordinator.pause_session(created.session_id, at=NOW4)
        store = coordinator._store(created.session_id)
        registry = coordinator.registry
        before = (
            registry.path.read_bytes(), store.state_path.read_bytes(),
            store.events_path.read_bytes(),
        )

        def fail_evidence(_root):
            raise error_type("injected resume evidence failure")

        with monkeypatch.context() as patch:
            patch.setattr(coordinator_module, "compute_workspace_digest_v1", fail_evidence)
            with pytest.raises(error_type, match="injected resume evidence failure"):
                coordinator.activate_session(created.session_id, at=NOW4)

        assert registry.read().active_session_id is None
        assert coordinator.read_session(created.session_id) == paused
        assert before == (
            registry.path.read_bytes(), store.state_path.read_bytes(),
            store.events_path.read_bytes(),
        )
        assert not store.actions_path.exists()
        resumed = coordinator.activate_session(created.session_id, at=NOW4)
        assert resumed.lifecycle.status.value == "ACTIVE"
        assert resumed.active_flow == started.active_flow
        assert registry.read().active_session_id == created.session_id
        assert resumed.state_revision == paused.state_revision + 1
        assert len(store.read_events().records) == paused.execution.last_event_seq + 1
        assert coordinator.activate_session(created.session_id, at=NOW4) == resumed
    finally:
        lease.release()


def test_failed_resume_does_not_block_another_session(tmp_path: Path, monkeypatch):
    _, lease, coordinator = _coordinator(tmp_path)
    try:
        first = coordinator.create_session(goal="Flow", requested_mode="write", at=NOW1)
        coordinator.activate_session(first.session_id, at=NOW2)
        coordinator.start_flow(first.session_id, at=NOW3)
        paused = coordinator.pause_session(first.session_id, at=NOW4)
        second = coordinator.create_session(goal="Other task", requested_mode="read", at=NOW4)

        def fail_evidence(_root):
            raise WorkspaceError("injected resume evidence failure")

        with monkeypatch.context() as patch:
            patch.setattr(coordinator_module, "compute_workspace_digest_v1", fail_evidence)
            with pytest.raises(WorkspaceError, match="injected resume evidence failure"):
                coordinator.activate_session(first.session_id, at=NOW4)
        active = coordinator.activate_session(second.session_id, at=NOW4)
        assert active.lifecycle.status.value == "ACTIVE"
        assert coordinator.registry.read().active_session_id == second.session_id
        assert coordinator.read_session(first.session_id) == paused
        first_store = coordinator._store(first.session_id)
        before_conflict = (
            coordinator.registry.path.read_bytes(), first_store.state_path.read_bytes(),
            first_store.events_path.read_bytes(),
        )
        with pytest.raises(ActiveSessionConflictError):
            coordinator.activate_session(first.session_id, at=NOW4)
        assert before_conflict == (
            coordinator.registry.path.read_bytes(), first_store.state_path.read_bytes(),
            first_store.events_path.read_bytes(),
        )
    finally:
        lease.release()
