# P0 Windows Validation — 2026-10-06

Status: **PASS / CLOSED (targeted Windows CI + operator Windows 10 smoke)**

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

## Operator Windows 10 smoke

The same branch was cloned on the operator's Windows 10 machine at commit
`7d132e03d2569f84e36050ac2dc3409171e0771f` and tested with Python 3.12.11.

Result:

- the same targeted seven-file P0 suite completed **55/55 passed**;
- the Windows junction test
  `test_windows_junction_escape_is_refused` executed and passed;
- the isolated junction command later emitted the repository-wide coverage
  threshold failure because that one test covers only a tiny fraction of the
  entire Arena package. The test result itself was `1 passed, 24 deselected`.
  This is a coverage-policy artifact, not a boundary failure. The full targeted
  suite had already been run with `--no-cov` and was green.

## Acceptance meaning

This establishes real Windows execution proof for the P0-A/B/C/D targeted
security suite on both:

1. GitHub-hosted Windows Server 2025 / Python 3.12.10; and
2. the operator's Windows 10 environment / Python 3.12.11.

The P0 Windows validation gate is therefore **closed**.

It does **not** prove every upstream Arena regression test.

The remaining documented residual risk is TOCTOU between the final userspace
path validation and kernel file open. Project-safe narrows that window and
rejects reparse-bearing write paths, but stronger malicious-concurrent-filesystem
threat models still require OS-level/handle-based containment.
