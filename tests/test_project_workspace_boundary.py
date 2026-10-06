from __future__ import annotations

import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from arena.mcp.project_boundary import (
    resolve_workspace_path,
    windows_path_rejection_reason,
)


def _ctx(root: Path):
    def under_root(path: Path, parent: Path) -> bool:
        try:
            path.resolve().relative_to(parent.resolve())
            return True
        except ValueError:
            return False

    return SimpleNamespace(app_config=lambda: {"root": str(root)}, under_root=under_root)


def test_relative_absolute_and_normalized_paths_stay_inside_workspace(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    inside = root / "src" / "a.py"
    inside.parent.mkdir()
    inside.write_text("print('ok')", encoding="utf-8")
    ctx = _ctx(root)

    resolved, err = resolve_workspace_path("src/a.py", ctx)
    assert err is None
    assert resolved == inside.resolve()

    resolved, err = resolve_workspace_path(str(inside), ctx)
    assert err is None
    assert resolved == inside.resolve()

    resolved, err = resolve_workspace_path("src/../src/a.py", ctx)
    assert err is None
    assert resolved == inside.resolve()


def test_parent_escape_sibling_prefix_and_git_case_variants_are_refused(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    sibling = tmp_path / "project-evil"
    sibling.mkdir()
    ctx = _ctx(root)

    _resolved, err = resolve_workspace_path("../outside.txt", ctx)
    assert err and "outside configured workspace root" in err

    _resolved, err = resolve_workspace_path(str(sibling / "file.txt"), ctx)
    assert err and "outside configured workspace root" in err

    (root / ".git").mkdir()
    _resolved, err = resolve_workspace_path(".git/config", ctx)
    assert err and ".git" in err

    _resolved, err = resolve_workspace_path(".GIT/config", ctx)
    assert err and ".git" in err


def test_nonexistent_write_leaf_inside_workspace_is_allowed(tmp_path, monkeypatch):
    monkeypatch.setenv("ARENA_PROJECT_SAFE", "1")
    root = tmp_path / "project"
    root.mkdir()
    ctx = _ctx(root)

    expected = root / "new" / "nested" / "file.txt"
    resolved, err = resolve_workspace_path("new/nested/file.txt", ctx, for_write=True)

    assert err is None
    assert resolved == expected.resolve(strict=False)


def test_symlink_escape_is_refused_and_write_through_link_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setenv("ARENA_PROJECT_SAFE", "1")
    root = tmp_path / "project"
    outside = tmp_path / "outside"
    inside_target = root / "inside"
    root.mkdir()
    outside.mkdir()
    inside_target.mkdir()
    ctx = _ctx(root)

    outside_link = root / "outside-link"
    inside_link = root / "inside-link"
    try:
        outside_link.symlink_to(outside, target_is_directory=True)
        inside_link.symlink_to(inside_target, target_is_directory=True)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symlink creation unavailable: {exc}")

    _resolved, err = resolve_workspace_path("outside-link/x.txt", ctx)
    assert err and "outside configured workspace root" in err

    resolved, err = resolve_workspace_path("inside-link/x.txt", ctx)
    assert err is None
    assert resolved == (inside_target / "x.txt").resolve(strict=False)

    _resolved, err = resolve_workspace_path("inside-link/x.txt", ctx, for_write=True)
    assert err and ("reparse" in err or "symlink" in err)


def test_hardlinked_file_is_readable_but_not_writable_project_safe(tmp_path, monkeypatch):
    monkeypatch.setenv("ARENA_PROJECT_SAFE", "1")
    root = tmp_path / "project"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    source = outside / "shared.txt"
    source.write_text("shared", encoding="utf-8")
    link = root / "shared.txt"
    try:
        os.link(source, link)
    except OSError as exc:
        pytest.skip(f"hardlink creation unavailable: {exc}")

    ctx = _ctx(root)
    resolved, err = resolve_workspace_path(str(link), ctx)
    assert err is None
    assert resolved == link.resolve()

    _resolved, err = resolve_workspace_path(str(link), ctx, for_write=True)
    assert err and "multiply-linked" in err


@pytest.mark.parametrize(
    ("raw", "needle"),
    [
        (r"C:outside.txt", "drive-relative"),
        (r"\\server\share\file.txt", "UNC"),
        (r"\\?\C:\workspace\file.txt", "device or extended"),
        (r"\\.\PIPE\name", "device or extended"),
        (r"file.txt:stream", "alternate data streams"),
        ("folder\\name.", "trailing dots or spaces"),
        ("folder\\name ", "trailing dots or spaces"),
        ("CON", "reserved Windows device"),
        ("NUL.txt", "reserved Windows device"),
        ("COM1.py", "reserved Windows device"),
        ("LPT9.log", "reserved Windows device"),
    ],
)
def test_windows_lexical_hazards_are_rejected_portably(raw, needle):
    reason = windows_path_rejection_reason(raw)
    assert reason and needle in reason


@pytest.mark.parametrize(
    "raw",
    [
        r"C:\workspace\src\main.py",
        r"src\main.py",
        r"src\..\README.md",
        r".gitignore",
    ],
)
def test_normal_windows_path_shapes_pass_lexical_screen(raw):
    assert windows_path_rejection_reason(raw) is None


@pytest.mark.skipif(os.name != "nt", reason="Windows-only junction/reparse behavior")
def test_windows_junction_escape_is_refused(tmp_path, monkeypatch):
    monkeypatch.setenv("ARENA_PROJECT_SAFE", "1")
    root = tmp_path / "project"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    junction = root / "junction"

    result = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(junction), str(outside)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        pytest.skip(f"junction creation unavailable: {result.stderr or result.stdout}")

    ctx = _ctx(root)
    _resolved, err = resolve_workspace_path(str(junction / "x.txt"), ctx)
    assert err and "outside configured workspace root" in err

    _resolved, err = resolve_workspace_path(str(junction / "x.txt"), ctx, for_write=True)
    assert err


def test_project_safe_fails_closed_without_explicit_root(monkeypatch):
    monkeypatch.setenv("ARENA_PROJECT_SAFE", "1")
    ctx = SimpleNamespace(app_config=lambda: {}, under_root=lambda _p, _r: True)
    _resolved, err = resolve_workspace_path("x.txt", ctx)
    assert err and "explicit configured workspace root" in err


def test_project_safe_refuses_entire_home_as_root(monkeypatch):
    monkeypatch.setenv("ARENA_PROJECT_SAFE", "1")
    home = Path.home()
    ctx = _ctx(home)
    _resolved, err = resolve_workspace_path("x.txt", ctx)
    assert err and "refuses the entire user home" in err


def test_project_safe_root_must_exist(monkeypatch, tmp_path):
    monkeypatch.setenv("ARENA_PROJECT_SAFE", "1")
    missing = tmp_path / "missing-root"
    ctx = _ctx(missing)
    _resolved, err = resolve_workspace_path("x.txt", ctx)
    assert err and "must exist and be a directory" in err
