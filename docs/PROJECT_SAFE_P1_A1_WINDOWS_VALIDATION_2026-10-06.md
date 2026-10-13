# Project-Safe P1-A1 Windows Validation — 2026-10-06

Status: **PASS**

## Scope

P1-A1 validates the durable-session foundation only:

- project-safe state root must be outside the canonical workspace;
- stable project fingerprinting;
- cross-process project lease;
- Windows ownership is a live exclusive Win32 HANDLE, not lock-file existence;
- process death releases kernel ownership while the stale lock file may remain.

No state schema, journal, checkpoint persistence, workspace digest or mutation
execution is connected in this slice.

## Windows evidence

GitHub Actions workflow: `Project Safe Windows`  
Run: `37525362315`  
Job: `P0 + P1 foundation / Windows / Python 3.12`  
Runner: Windows Server 2025 / Python 3.12.10  
Commit under test: `be11e4f37d064a4e6f2e6d49f7c600eeb9f72f08`

Result: **64/64 passed**.

The 64 tests are the prior 55 targeted P0 tests plus 9 P1-A1 foundation
tests.

The P1-A1 tests include:

- state-root equal to workspace -> fail closed;
- state-root descendant of workspace -> fail closed before creation;
- valid sibling state root;
- stable project fingerprint;
- second live lease owner rejected;
- Win32 contention reports native `ERROR_SHARING_VIOLATION (32)`;
- lock-file existence does not mean ownership;
- process death releases the kernel lease;
- an independent process can acquire after the prior holder dies.

## First CI attempt

Run `37524532958` failed before pytest collection because the workflow command
contained a literal `\n` token in the test-file list. Pytest reported
`file or directory not found: \n`.

That was a CI command construction error, not a product/test failure. The
workflow was corrected and the subsequent run above is green.

## Acceptance

P1-A1 is **CLOSED / PASS** on real Windows execution evidence.

P1-A2 strict schema work may proceed. The PR remains draft and unmerged.
