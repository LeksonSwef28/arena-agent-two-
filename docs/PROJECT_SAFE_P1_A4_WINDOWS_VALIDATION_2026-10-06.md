# Project-Safe P1-A4 Windows Validation — 2026-10-06

Status: **PASS**

GitHub Actions run: `37534901606`  
Runner: Windows Server 2025 / Python 3.12.10  
Result: **111/111 passed**.

## Validated

- `workspace_digest_v1` captures HEAD, index, unstaged tracked state and
  non-ignored untracked resources;
- ignored resources are omitted from the global digest but still protected by
  resource-level CAS when explicitly targeted;
- staged index changes alter the digest;
- Git submodule index mode `160000` fails closed;
- project root must equal Git worktree top-level;
- double capture rejects an unstable workspace;
- flow workspace guard rejects independent drift without changing action
  identity;
- resource CAS ignores unrelated-file drift but rejects target content drift;
- absent-target CAS rejects a file that appears before execution;
- the 16 MiB v1 resource-before policy fails closed;
- resource CAS reuses the P0 hardlink/path boundary.

## Windows CRLF finding

Earlier A4 runs reported every tracked test file as modified on Windows.

Cause: the hardened Git runner intentionally removes ambient global/system Git
configuration. The test repository had been created under Git for Windows
line-ending policy, while the project-safe inspection then ran without that
ambient `core.autocrlf` setting.

The workspace-v1 status capture now supplies deterministic safe conversion
settings:

- `core.autocrlf=input`
- `core.fileMode=false`

This prevents checkout-only CRLF representation from becoming false workspace
drift while keeping index state and exact resource-level CAS separate.

## Boundary reuse

P1-A4 added a context-free `resolve_project_safe_path()` in the existing P0
boundary module. MCP project-safe filesystem paths and durable-session CAS now
reuse the same Windows syntax, reparse, `.git`, containment and hardlink
rules instead of maintaining a second path-security implementation.

## Acceptance

P1-A4 is **CLOSED / PASS**.

P1-A5 may proceed. The pull request remains draft and unmerged.
