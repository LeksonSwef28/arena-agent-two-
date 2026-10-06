from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import arena.mcp.tool_git as tool_git


def test_project_safe_git_subprocess_strips_ambient_git_execution_controls(monkeypatch, tmp_path):
    monkeypatch.setenv("ARENA_PROJECT_SAFE", "1")
    monkeypatch.setenv("GIT_EXTERNAL_DIFF", "should-not-survive")
    monkeypatch.setenv("GIT_DIR", str(tmp_path / "outside-git-dir"))

    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = list(cmd)
        captured["env"] = dict(kwargs.get("env") or {})
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(tool_git.subprocess, "run", fake_run)

    code, _stdout, _stderr = tool_git._run_git(tmp_path, ["status"])

    assert code == 0
    assert "--no-pager" in captured["cmd"]
    assert f"core.hooksPath={os.devnull}" in captured["cmd"]
    assert "core.fsmonitor=false" in captured["cmd"]
    assert "GIT_EXTERNAL_DIFF" not in captured["env"]
    assert "GIT_DIR" not in captured["env"]
    assert captured["env"]["GIT_TERMINAL_PROMPT"] == "0"
    assert captured["env"]["GIT_OPTIONAL_LOCKS"] == "0"
    assert captured["env"]["GIT_CONFIG_NOSYSTEM"] == "1"
    assert captured["env"]["GIT_CONFIG_GLOBAL"] == os.devnull


def test_project_safe_revision_rejects_option_injection_without_running_git(monkeypatch, tmp_path):
    monkeypatch.setenv("ARENA_PROJECT_SAFE", "1")

    def should_not_run(*_args, **_kwargs):
        raise AssertionError("unsafe option-like revision reached git")

    monkeypatch.setattr(tool_git, "_run_git", should_not_run)

    sha, error = tool_git._project_safe_commit_sha(
        tmp_path,
        "--output=C:/outside.txt",
    )

    assert sha is None
    assert error and "unsafe git revision syntax" in error


def test_project_safe_diff_disables_external_helpers(monkeypatch, tmp_path):
    monkeypatch.setenv("ARENA_PROJECT_SAFE", "1")
    captured = {}

    def fake_run(_repo_path, args, timeout=15):
        captured["args"] = list(args)
        captured["timeout"] = timeout
        return 0, "", ""

    monkeypatch.setattr(tool_git, "_run_git", fake_run)

    result = tool_git._handle_git_diff(tmp_path, {"staged": False})

    assert result is not None
    assert "--no-ext-diff" in captured["args"]
    assert "--no-textconv" in captured["args"]
    assert "--ignore-submodules=all" in captured["args"]
    assert captured["timeout"] == 30


def test_git_commit_defense_in_depth_blocks_project_safe(monkeypatch, tmp_path):
    monkeypatch.setenv("ARENA_PROJECT_SAFE", "1")
    result = tool_git._handle_git_commit(tmp_path, {"message": "should not run"})
    assert result.get("isError") is True
    assert "disabled in project-safe mode" in result["content"][0]["text"]
