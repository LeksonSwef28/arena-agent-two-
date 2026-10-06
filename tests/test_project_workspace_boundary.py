from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from arena.mcp.project_boundary import resolve_workspace_path


def _ctx(root: Path):
    def under_root(path: Path, parent: Path) -> bool:
        try:
            path.resolve().relative_to(parent.resolve())
            return True
        except ValueError:
            return False

    return SimpleNamespace(app_config=lambda: {"root": str(root)}, under_root=under_root)


def test_relative_and_absolute_paths_stay_inside_workspace(tmp_path):
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


def test_parent_escape_and_git_internals_are_refused(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    ctx = _ctx(root)

    _resolved, err = resolve_workspace_path("../outside.txt", ctx)
    assert err and "outside configured workspace root" in err

    (root / ".git").mkdir()
    _resolved, err = resolve_workspace_path(".git/config", ctx)
    assert err and ".git" in err


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
