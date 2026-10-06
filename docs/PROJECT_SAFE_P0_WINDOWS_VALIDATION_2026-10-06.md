# P0 Windows Validation — 2026-10-06

Status: **PASS (targeted Windows CI)**

Workflow:

- `.github/workflows/project-safe-windows.yml`
- run: `37514295813`
- commit: `bab364714c6013e4832d31a9da1b3355a15e540f`

Environment:

- GitHub-hosted Windows runner
- Windows Server 2025, build 10.0.26100
- Python 3.12.10 x64

Targeted suite:

- `tests/test_project_safe_http_gate.py`
- `tests/test_lifecycle.py`
- `tests/test_project_workspace_boundary.py`
- `tests/test_project_safe_recursive_fs_boundary.py`
- `tests/test_project_safe_git_boundary.py`
- `tests/test_project_safe_secondary_servers.py`
- `tests/test_project_safe_launcher.py`

Result:

- **55/55 tests passed**
- no skips were reported in the progress line;
- therefore the Windows-only junction/reparse test executed rather than being
  skipped.

The first targeted run (`37513988759`) failed because one regression test
asserted that the literal search query text `outside-marker` must not appear
anywhere in the response. The implementation correctly returned
`No matches found for 'outside-marker'...`, so the query string itself appeared
without any outside-file disclosure. The test was corrected to assert that the
outside target path / symlink entry is not exposed. The subsequent Windows run
passed.

## Acceptance meaning

This establishes a real Windows execution proof for the P0-A/B/C/D targeted
security suite.

It does **not** prove every upstream Arena regression test, nor does it replace
an optional smoke test on the operator's exact Windows 10 machine before first
real use.

The remaining documented residual risk is TOCTOU between the final userspace
path validation and kernel file open. Project-safe narrows that window and
rejects reparse-bearing write paths, but stronger malicious-concurrent-filesystem
threat models still require OS-level/handle-based containment.
