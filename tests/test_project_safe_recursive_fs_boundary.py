from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from arena.mcp.tool_fs_search import handle_fs_search_tool
from arena.mcp.tool_fs_tree_diff import handle_fs_tree_diff_tool


def _ctx(root: Path):
    def under_root(path: Path, parent: Path) -> bool:
        try:
            path.resolve().relative_to(parent.resolve())
            return True
        except ValueError:
            return False

    return SimpleNamespace(app_config=lambda: {"root": str(root)}, under_root=under_root)


def _text(result: dict) -> str:
    return "\n".join(part.get("text", "") for part in result.get("content", []))


def test_search_does_not_follow_file_symlink_outside_workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("ARENA_PROJECT_SAFE", "1")
    root = tmp_path / "project"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (root / "inside.txt").write_text("inside-marker", encoding="utf-8")
    secret = outside / "secret.txt"
    secret.write_text("outside-marker", encoding="utf-8")

    link = root / "leak.txt"
    try:
        link.symlink_to(secret)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symlink creation unavailable: {exc}")

    result = handle_fs_search_tool(
        "fs.search",
        {"path": str(root), "pattern": "outside-marker"},
        ctx=_ctx(root),
    )
    assert result is not None
    text = _text(result)
    assert "outside-marker" not in text
    assert "No matches found" in text


def test_tree_does_not_follow_directory_symlink_outside_workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("ARENA_PROJECT_SAFE", "1")
    root = tmp_path / "project"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (outside / "secret.txt").write_text("outside-marker", encoding="utf-8")

    link = root / "linked-outside"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symlink creation unavailable: {exc}")

    result = handle_fs_tree_diff_tool(
        "fs.tree",
        {"path": str(root), "max_depth": 3},
        ctx=_ctx(root),
    )
    assert result is not None
    text = _text(result)
    assert "secret.txt" not in text
    assert "linked-outside" not in text
