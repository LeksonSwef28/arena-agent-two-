"""T102: unreadable entries must not abort an inventory scan."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

import arena.inventory.probe_agent_ctx as probes


@pytest.mark.parametrize("collector", ["get_python_venvs", "get_git_repos"])
@pytest.mark.parametrize("failure", ["symlink-target", "entry-stat", "marker-stat", "root-stat", "directory-list"])
def test_scan_access_failure_keeps_readable_siblings(tmp_path: Path, monkeypatch, collector, failure):
    good = tmp_path / "good"
    good.mkdir()
    if collector == "get_python_venvs":
        (good / "pyvenv.cfg").write_text("version = 3.12.1\n", encoding="utf-8")
        (good / "bin").mkdir()
        (good / "bin" / "python3").touch()
        key = "venvs"
    else:
        (good / ".git").mkdir()
        monkeypatch.setattr(probes, "_which", lambda _: "git")
        monkeypatch.setattr(probes, "_run", lambda *a, **kw: "main")
        key = "repos"

    def denied(*args, **kwargs):
        raise PermissionError("unreadable test entry")

    followed_symlink = []

    class Entry:
        name = "unreadable"

        def is_symlink(self):
            return failure == "symlink-target"

        def is_dir(self):
            if failure == "symlink-target":
                followed_symlink.append(True)
            if failure in {"symlink-target", "entry-stat"}:
                denied()
            return True

        def resolve(self):
            return tmp_path / self.name

        def iterdir(self):
            denied()

        def __truediv__(self, name):
            return SimpleNamespace(is_file=denied, exists=denied)

        def __str__(self):
            return str(tmp_path / self.name)

    root = SimpleNamespace(
        exists=denied if failure == "root-stat" else lambda: True,
        iterdir=denied if failure == "directory-list" else lambda: iter([Entry(), good]),
    )
    # Replace this module's constructor, never the shared pathlib implementation.
    monkeypatch.setattr(probes, "Path", lambda _: root)
    result = getattr(probes, collector)(scan_root=str(tmp_path))
    assert followed_symlink == [], "symlink target metadata must never be queried"
    if failure in {"root-stat", "directory-list"}:
        assert result["available"] is False
        assert result[key] == []
        if failure == "root-stat":
            assert result["error"] == "$HOME not accessible"
    else:
        assert result["available"] is True
        assert [item["path"] for item in result[key]] == [str(good)]
