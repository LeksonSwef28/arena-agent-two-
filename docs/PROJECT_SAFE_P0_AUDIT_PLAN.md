# P0 Audit Plan — Ingress Map and Windows Workspace Boundary

Status: **audit-only checkpoint**.  
No new orchestration/Qwen/browser-review features are to be implemented while
this audit is open.

## A. Ingress audit objective

Inventory every Arena entry point that can cause a mutation or execution.

For each surface record:

- protocol/surface;
- route/handler;
- whether it can read only or mutate/execute;
- downstream dispatcher/executor;
- whether project-safe policy is guaranteed;
- whether it remains reachable in project-safe mode;
- evidence/test required;
- final status: PASS / FAIL / NOT CHECKED.

P0 remains FAIL if any mutating surface is NOT CHECKED.

Candidate surfaces to verify:

- MCP `/mcp`;
- SSE / messages compatibility path;
- browser extension preview/execute;
- direct REST execution APIs;
- task/background queues;
- mission/scenario/autopilot paths;
- gateway/proxy tool paths;
- WebSocket/MCP compatibility transports;
- standalone MCP servers;
- skills/runtime/admin/update/install surfaces;
- browser/CDP automation;
- desktop/mobile/ADB;
- tunnel/webhook/network actions;
- any direct internal executor call reachable without the canonical tool gate.

## B. Windows boundary test matrix

The canonical path policy must be tested against real Windows semantics.

| Case | Expected |
|---|---|
| file directly inside workspace | ALLOW |
| nested file inside workspace | ALLOW |
| normalized `sub\\..\\file` still inside | ALLOW |
| `..\\outside` escape | DENY |
| sibling prefix such as `project-evil` | DENY |
| absolute path outside root | DENY |
| drive-relative path such as `C:foo` when outside | DENY |
| UNC path outside root | DENY |
| extended/device namespace if accepted by runtime | DENY unless explicitly inside and proven |
| path differing only by Windows case | handled canonically; no escape |
| symlink inside -> inside | policy decision + explicit test |
| symlink inside -> outside | DENY |
| junction/reparse inside -> outside | DENY |
| nested link/junction chain ending outside | DENY |
| nonexistent write target with parent inside | ALLOW only after parent is proved inside |
| nonexistent write target with parent/link resolving outside | DENY |
| direct `.git\\config` | DENY |
| direct `.git\\hooks\\*` | DENY |
| alternate path spelling to `.git` if Windows resolves it | DENY |
| root missing in project-safe mode | DENY |
| root == whole user home | DENY |
| root disappears/replaced during operation | FAIL CLOSED |
| path validated then parent replaced by junction before write (TOCTOU) | must not write outside; mitigation/test required |

## C. Test principles

- Test code must exercise the same boundary helper used by production handlers.
- Do not substitute string-prefix tests for filesystem semantics.
- Where Windows-only behavior cannot be exercised in Linux CI, mark the test as
  Windows-specific and run it in a Windows lane/manual target-machine check.
- Each discovered bypass gets a regression test before the fix is considered
  complete.
- Security acceptance is evidence-based: NOT RUN is not PASS.

## D. Stop condition for this audit block

After the ingress map and matrix are populated with evidence, stop and review
findings before making the next functional security change.
