"""P1-A4 workspace_digest_v1 and file-resource CAS regression tests."""
from __future__ import annotations

import os
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

import arena.project_safe_session.workspace as workspace_module
from arena.project_safe_session import (
    ResourceBoundaryError,
    ResourceDriftError,
    ResourceTooLargeError,
    UnsupportedRepoLayoutError,
    WorkspaceDriftError,
    WorkspaceManifest,
    WorkspaceUnstableError,
    assert_flow_workspace_guard,
    capture_file_resource_before,
    compute_workspace_digest_v1,
    file_effect_target_fingerprint,
    validate_file_resource_cas,
)


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
    (repo / ".gitignore").write_text("ignored.log\n", encoding="utf-8")
    (repo / "foo.py").write_text("foo = 1\n", encoding="utf-8")
    (repo / "bar.py").write_text("bar = 1\n", encoding="utf-8")
    _git(repo, "add", ".gitignore", "foo.py", "bar.py")
    _git(repo, "commit", "-m", "baseline")
    return repo


def test_clean_workspace_digest_is_stable_and_git_visible(tmp_path: Path):
    repo = _repo(tmp_path)

    manifest, digest = compute_workspace_digest_v1(repo)
    manifest2, digest2 = compute_workspace_digest_v1(repo)

    assert digest == digest2
    assert manifest == manifest2
    assert manifest.worktree == ()
    assert manifest.untracked == ()
    assert {entry.path for entry in manifest.index} == {".gitignore", "bar.py", "foo.py"}


def test_workspace_digest_tracks_unstaged_and_untracked_but_not_ignored(tmp_path: Path):
    repo = _repo(tmp_path)
    _, baseline = compute_workspace_digest_v1(repo)

    (repo / "bar.py").write_text("bar = 2\n", encoding="utf-8")
    (repo / "note.txt").write_text("untracked\n", encoding="utf-8")
    (repo / "ignored.log").write_text("ignored\n", encoding="utf-8")

    manifest, changed = compute_workspace_digest_v1(repo)

    assert changed != baseline
    assert [item.path for item in manifest.worktree] == ["bar.py"]
    assert [item.path for item in manifest.untracked] == ["note.txt"]
    assert all(item.path != "ignored.log" for item in manifest.untracked)


def test_workspace_digest_tracks_staged_index_state(tmp_path: Path):
    repo = _repo(tmp_path)
    manifest_before, digest_before = compute_workspace_digest_v1(repo)
    before = {item.path: item.object_id for item in manifest_before.index}

    (repo / "foo.py").write_text("foo = 2\n", encoding="utf-8")
    _git(repo, "add", "foo.py")

    manifest_after, digest_after = compute_workspace_digest_v1(repo)
    after = {item.path: item.object_id for item in manifest_after.index}

    assert digest_after != digest_before
    assert after["foo.py"] != before["foo.py"]
    assert all(item.path != "foo.py" for item in manifest_after.worktree)


def test_gitlink_submodule_index_entry_fails_closed(tmp_path: Path):
    repo = _repo(tmp_path)
    head = _git(repo, "rev-parse", "HEAD")
    _git(repo, "update-index", "--add", "--cacheinfo", f"160000,{head},vendor/sub")

    with pytest.raises(UnsupportedRepoLayoutError, match="submodules"):
        compute_workspace_digest_v1(repo)


def test_project_root_must_equal_git_toplevel(tmp_path: Path):
    repo = _repo(tmp_path)
    subdir = repo / "src"
    subdir.mkdir()

    with pytest.raises(UnsupportedRepoLayoutError, match="worktree root"):
        compute_workspace_digest_v1(subdir)


def test_double_read_detects_workspace_instability(tmp_path: Path, monkeypatch):
    repo = _repo(tmp_path)
    stable, _ = compute_workspace_digest_v1(repo)
    changed = replace(stable, head_sha="b" * len(stable.head_sha))
    sequence = iter([stable, changed])

    monkeypatch.setattr(
        workspace_module,
        "compute_workspace_manifest_once",
        lambda _root: next(sequence),
    )

    with pytest.raises(WorkspaceUnstableError):
        workspace_module.compute_workspace_digest_v1(repo)


def test_flow_guard_denies_independent_workspace_drift(tmp_path: Path):
    repo = _repo(tmp_path)
    _, baseline = compute_workspace_digest_v1(repo)

    (repo / "bar.py").write_text("bar = 99\n", encoding="utf-8")

    with pytest.raises(WorkspaceDriftError):
        assert_flow_workspace_guard(repo, baseline)


def test_resource_cas_ignores_unrelated_file_but_rejects_target_drift(tmp_path: Path, monkeypatch):
    repo = _repo(tmp_path)
    monkeypatch.setenv("ARENA_PROJECT_SAFE", "1")

    before = capture_file_resource_before(repo, "foo.py")
    fingerprint = file_effect_target_fingerprint(before)

    (repo / "bar.py").write_text("bar = 2\n", encoding="utf-8")
    assert validate_file_resource_cas(repo, before) == (repo / "foo.py").resolve()
    assert file_effect_target_fingerprint(before) == fingerprint

    (repo / "foo.py").write_text("foo = 999\n", encoding="utf-8")
    with pytest.raises(ResourceDriftError, match="content changed"):
        validate_file_resource_cas(repo, before)


def test_absent_resource_cas_rejects_file_that_appeared(tmp_path: Path, monkeypatch):
    repo = _repo(tmp_path)
    monkeypatch.setenv("ARENA_PROJECT_SAFE", "1")

    before = capture_file_resource_before(repo, "new.txt")
    assert before.exists is False

    (repo / "new.txt").write_text("someone else created me", encoding="utf-8")
    with pytest.raises(ResourceDriftError, match="existence changed"):
        validate_file_resource_cas(repo, before)


def test_ignored_resource_still_gets_resource_level_cas(tmp_path: Path, monkeypatch):
    repo = _repo(tmp_path)
    monkeypatch.setenv("ARENA_PROJECT_SAFE", "1")
    ignored = repo / "ignored.log"
    ignored.write_text("v1", encoding="utf-8")

    before = capture_file_resource_before(repo, "ignored.log")
    assert before.exists is True
    assert validate_file_resource_cas(repo, before) == ignored.resolve()

    ignored.write_text("v2", encoding="utf-8")
    with pytest.raises(ResourceDriftError):
        validate_file_resource_cas(repo, before)


def test_resource_checkpoint_size_policy_fails_closed(tmp_path: Path, monkeypatch):
    repo = _repo(tmp_path)
    monkeypatch.setenv("ARENA_PROJECT_SAFE", "1")
    target = repo / "large.bin"
    target.write_bytes(b"12345")

    with pytest.raises(ResourceTooLargeError):
        capture_file_resource_before(repo, "large.bin", size_limit=4)


def test_resource_cas_reuses_p0_hardlink_boundary(tmp_path: Path, monkeypatch):
    repo = _repo(tmp_path)
    monkeypatch.setenv("ARENA_PROJECT_SAFE", "1")
    alias = repo / "foo-hardlink.py"
    os.link(repo / "foo.py", alias)

    with pytest.raises(ResourceBoundaryError, match="multiply-linked"):
        capture_file_resource_before(repo, "foo.py")
