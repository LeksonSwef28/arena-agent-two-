from __future__ import annotations

from arena.project_safe import (
    filter_project_safe_tools,
    project_safe_block_reason,
)


def test_policy_is_transparent_when_disabled(monkeypatch):
    monkeypatch.delenv("ARENA_PROJECT_SAFE", raising=False)
    tools = [{"name": "exec.exec"}]
    assert project_safe_block_reason("exec.exec") is None
    assert filter_project_safe_tools(tools) is tools


def test_project_safe_defaults_to_read_only(monkeypatch):
    monkeypatch.setenv("ARENA_PROJECT_SAFE", "1")
    monkeypatch.delenv("ARENA_PROJECT_SAFE_WRITES", raising=False)
    monkeypatch.delenv("ARENA_PROJECT_SAFE_CODE", raising=False)

    assert project_safe_block_reason("fs.read") is None
    assert project_safe_block_reason("git.status") is None
    assert project_safe_block_reason("fs.write") is not None
    assert project_safe_block_reason("code.run") is not None
    assert project_safe_block_reason("exec.exec") is not None
    assert project_safe_block_reason("runtime.install") is not None


def test_project_safe_write_and_code_are_separate_opt_ins(monkeypatch):
    monkeypatch.setenv("ARENA_PROJECT_SAFE", "1")
    monkeypatch.setenv("ARENA_PROJECT_SAFE_WRITES", "1")
    monkeypatch.delenv("ARENA_PROJECT_SAFE_CODE", raising=False)
    assert project_safe_block_reason("fs.edit") is None
    assert project_safe_block_reason("code.run") is not None

    monkeypatch.setenv("ARENA_PROJECT_SAFE_CODE", "1")
    assert project_safe_block_reason("code.run") is None


def test_catalog_hides_blocked_tools(monkeypatch):
    monkeypatch.setenv("ARENA_PROJECT_SAFE", "1")
    monkeypatch.delenv("ARENA_PROJECT_SAFE_WRITES", raising=False)
    tools = [{"name": "fs.read"}, {"name": "fs.write"}, {"name": "exec.exec"}]
    assert [row["name"] for row in filter_project_safe_tools(tools)] == ["fs.read"]
