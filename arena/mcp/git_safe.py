"""Shared hardened Git subprocess for project-safe read operations."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path


def project_safe_git_env() -> dict[str, str]:
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.upper().startswith("GIT_")
    }
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_OPTIONAL_LOCKS"] = "0"
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    env["GIT_CONFIG_GLOBAL"] = os.devnull
    return env


def run_project_safe_git(
    repo_path: Path,
    args: list[str],
    *,
    timeout: int = 15,
) -> tuple[int, bytes, bytes]:
    """Run a Git builtin with ambient execution/config hooks disabled."""
    cmd = [
        "git",
        "--no-pager",
        "-c", f"core.hooksPath={os.devnull}",
        "-c", "core.fsmonitor=false",
        *args,
    ]
    try:
        result = subprocess.run(
            cmd,
            cwd=str(repo_path),
            capture_output=True,
            timeout=timeout,
            env=project_safe_git_env(),
        )
        return result.returncode, result.stdout, result.stderr
    except subprocess.TimeoutExpired:
        return -1, b"", b"git command timed out"
    except Exception as exc:
        return -2, b"", str(exc).encode("utf-8", "replace")


__all__ = ["project_safe_git_env", "run_project_safe_git"]
