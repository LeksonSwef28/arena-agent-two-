"""P1-A1 project-safe state-root and kernel lease regression tests."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from arena.project_safe_session import (
    ProjectLease,
    ProjectLeaseBusyError,
    ProjectSafeStateError,
    project_fingerprint,
    resolve_project_safe_state_root,
)


def _project(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    project.mkdir()
    return project


def test_state_root_equal_workspace_fails_closed_before_creation(tmp_path: Path):
    project = _project(tmp_path)

    with pytest.raises(ProjectSafeStateError, match="outside the configured workspace"):
        resolve_project_safe_state_root(project, project, create=True)


def test_state_root_descendant_fails_closed_before_creation(tmp_path: Path):
    project = _project(tmp_path)
    unsafe = project / ".arena-state" / "project-safe"

    with pytest.raises(ProjectSafeStateError, match="outside the configured workspace"):
        resolve_project_safe_state_root(project, unsafe, create=True)

    assert not unsafe.exists()


def test_state_root_sibling_is_created_and_resolved(tmp_path: Path):
    project = _project(tmp_path)
    state = tmp_path / "state"

    resolved = resolve_project_safe_state_root(project, state, create=True)

    assert resolved == state.resolve()
    assert resolved.is_dir()


def test_state_root_must_be_absolute(tmp_path: Path):
    project = _project(tmp_path)

    with pytest.raises(ProjectSafeStateError, match="absolute path"):
        resolve_project_safe_state_root(project, Path("relative-state"), create=False)


def test_project_fingerprint_is_stable_for_same_canonical_root(tmp_path: Path):
    project = _project(tmp_path)

    assert project_fingerprint(project) == project_fingerprint(project / ".")


def test_live_project_lease_blocks_second_owner(tmp_path: Path):
    project = _project(tmp_path)
    state = tmp_path / "state"
    first = ProjectLease(project, state)
    second = ProjectLease(project, state)

    first.acquire()
    try:
        assert first.held
        assert first.lock_path is not None and first.lock_path.exists()
        with pytest.raises(ProjectLeaseBusyError):
            second.acquire()
    finally:
        first.release()

    assert not first.held


def test_lock_file_existence_is_not_lease_ownership(tmp_path: Path):
    project = _project(tmp_path)
    state = tmp_path / "state"

    with ProjectLease(project, state) as first:
        lock_path = first.lock_path
        assert lock_path is not None and lock_path.exists()

    assert lock_path.exists()

    with ProjectLease(project, state) as second:
        assert second.held
        assert second.lock_path == lock_path


def test_process_death_releases_kernel_lease(tmp_path: Path):
    project = _project(tmp_path)
    state = tmp_path / "state"
    child = r"""
import sys
import time
from arena.project_safe_session import ProjectLease

lease = ProjectLease(sys.argv[1], sys.argv[2]).acquire()
print("READY", flush=True)
time.sleep(60)
"""
    proc = subprocess.Popen(
        [sys.executable, "-c", child, str(project), str(state)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert proc.stdout is not None
        ready = proc.stdout.readline().strip()
        if ready != "READY":
            assert proc.stderr is not None
            pytest.fail(f"lease child failed before acquisition: {proc.stderr.read()}")
        with pytest.raises(ProjectLeaseBusyError):
            ProjectLease(project, state).acquire()
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.wait(timeout=10)

    # The lock file may remain, but kernel ownership died with the process.
    with ProjectLease(project, state) as recovered:
        assert recovered.held
