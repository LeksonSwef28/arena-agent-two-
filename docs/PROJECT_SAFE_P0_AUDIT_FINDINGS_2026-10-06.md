# P0 Audit Findings — Ingress / Entry Points and Windows Boundary Matrix

Date: 2026-10-06  
Branch audited: `hardening/project-safe-v0`  
Audit mode: **ingress/lifecycle audit complete; P0-A/P0-B implementation added afterward**

## Executive result

**P0 status: FAIL (P0-A/P0-B implemented, validation incomplete; P0-C/P0-D still open).**

Implementation commits after the audit:

- P0-A HTTP fail-closed gate: `4a37d8025018f24c64088925340407d2e6688a28`
- P0-B project-safe lifecycle restrictions: `8246ac5bb90fae4979fc6a986341678cd78b075f`
- HTTP middleware integration tests added: `d2250f20ffc03fa47475a6e274e1bca86cb2b9dd`

Current test evidence:

- regression tests are present in the branch;
- GitHub Actions produced no workflow run/status for the fork commit;
- the assistant execution environment could not clone GitHub because outbound
  DNS/network access was unavailable;
- therefore test execution is **NOT RUN / PENDING**, not PASS.

The current branch has a useful project-safe MCP/tool gate and a project-root
helper, but it is **not yet a complete security boundary for the Arena process**.

The audit found multiple execution/mutation paths that do not pass through the
new project-safe MCP tool gate. The most important are:

- direct v1/v2 exec endpoints;
- web gateway `/run`;
- background task submission/runner;
- large direct REST mutation surface;
- standalone MCP servers using their own HOME-based jail;
- background task/mission/autostart workers started by application lifecycle.

The correct next implementation direction is therefore an app-level fail-closed
HTTP ingress gate **plus** project-safe lifecycle restrictions, while keeping
the existing MCP tool gate as defense in depth.

No P1 orchestrator/session work should start until these P0 items are fixed and
tested.

---

## 1. Actual unified-bridge route surface

The application registers routes through:

- `arena/route_registry/core.py`
- `arena/route_registry/cdp.py`
- `arena/route_registry/desktop.py`
- `arena/route_registry/domain.py`
- `arena/route_registry/compat.py`

Observed counts:

- non-CDP registered routes: **292**
- non-GET operations among them: **148**
- CDP endpoint definitions: **36**
- CDP is exposed under two prefixes:
  - `/v1/browser/cdp`
  - `/v1/cdp`
- effective CDP routes: **72**
- effective non-GET CDP operations: **42**

Approximate effective unified HTTP surface:

- **364 routes total**
- **190 non-GET/mutating-or-control routes**

This is the key reason handler-by-handler ad hoc hardening is not sufficient.

---

## 2. Ingress map

Legend:

- **PASS** — current path is proven to hit the project-safe tool gate.
- **FAIL** — current path can mutate/execute without that project-safe gate.
- **DENY V0** — surface is not required for the intended project-safe v0 and
  should be denied at HTTP ingress.
- **NOT PROVEN** — may have local protections, but not the canonical policy
  required by this architecture.

| Surface | Representative entry | Current downstream path | P0 status | Decision |
|---|---|---|---|---|
| MCP Streamable HTTP | `POST /mcp` | `handle_rpc -> call_tool -> project_safe_block_reason` | PASS for tool capability gate | ALLOW |
| SSE MCP messages | `POST /messages` | MCP `handle_rpc` | PASS for tool capability gate | ALLOW only if needed |
| MCP WebSocket | `GET /ws` | MCP JSON-RPC / `handle_rpc` | PASS for tool capability gate | ALLOW only if needed |
| Browser extension execute | `POST /v1/extension/execute` | extension runtime -> `ctx.call_tool` -> MCP dispatcher | PASS for tool capability gate | ALLOW |
| Browser extension preview/policy/instructions | extension endpoints | no host mutation by themselves | PASS / low risk | ALLOW |
| Gateway tool | `POST /tool` | builds MCP `tools/call` -> `handle_rpc` | PASS for tool capability gate | Prefer DENY V0 unless needed |
| Gateway command runner | `POST /run` | `gw_run_sync` directly | **FAIL** | DENY V0 |
| v1 exec | `/v1/exec`, `/script`, `/stream` | exec handlers / shell runner | **FAIL** | DENY V0 |
| v2 exec | `POST /v2/exec` | can reach `asyncio.create_subprocess_shell`; caller can disable sandbox | **FAIL** | DENY V0 |
| Task submit | `POST /v1/tasks` | task inbox -> background runner | **FAIL** | DENY V0 |
| Task runner | startup background loop | `create_subprocess_shell`, default cwd can be `Path.home()` | **FAIL** | Disable in project-safe mode |
| Batch | `POST /v1/batch` | authenticated self-HTTP to arbitrary `/v1/*` GET/POST | **FAIL** until HTTP gate exists | DENY V0 |
| Direct REST filesystem | upload/fs edit/create/etc. | direct REST handlers | NOT PROVEN against canonical project boundary | DENY V0; use scoped MCP fs tools |
| Mission run/schedules | `/v1/mission/*` | mission runtime / schedule worker | **FAIL / NOT CANONICALLY GATED** | DENY V0 |
| Mission schedule worker | startup loop | can invoke mission run/rerun/iterate | **FAIL** as latent executor | Disable in project-safe mode |
| Skills install/run/reload | `/v1/skills/*` | skill subsystem | **FAIL / outside capability set** | DENY V0 |
| Admin/update/proposal | `/v1/admin/*` | update/restart/proposal logic | **FAIL / outside project scope** | DENY V0 |
| Tunnels/network | tailscale/cloudflared/ngrok/bore/zerotier/tunnels | direct transport runtime | **FAIL / outside project scope** | DENY V0 |
| Tunnel autostart hooks | lifecycle startup | background autostart hooks run when marker/env enables them | **FAIL as latent capability** | Disable in project-safe mode |
| Desktop | `/v1/desktop/*` | host desktop automation | **FAIL / outside project scope** | DENY V0 |
| Mobile/ADB | `/v1/mobile/*` | ADB/mobile control and install | **FAIL / outside project scope** | DENY V0 |
| CDP direct browser control | `/v1/browser/cdp/*`, `/v1/cdp/*` | browser navigation/eval/click/type/cookies/etc. | **FAIL / bypasses project tool gate** | DENY V0; later expose via separate browser-role policy if needed |
| Custom external MCP management | `/v1/mcp/custom/remove` and MCP ext tools | external server subsystem | outside v0 | DENY V0 |
| Standalone MCP HTTP/WS servers | separate scripts/servers | `arena.mcp.standalone_tools` | **FAIL**: separate dispatcher + HOME jail + `exec.exec` | Do not launch in project-safe mode |
| Control status | `GET /v1/control/status` | control state | safe | ALLOW |
| Emergency halt | `POST /v1/control/halt` | global agent halt | required safety action | ALLOW |
| Unhalt/YOLO | control endpoints | increases authority/auto-approval | unsafe for v0 | DENY V0 |
| Health/version/status | read-only diagnostics | metadata | safe | ALLOW minimal set |

### Important architectural result

The project-safe MCP gate is **correctly placed for MCP tools and browser
extension tool calls**, but upstream comments calling that dispatcher the
"authoritative gate for the agent" do not cover the whole HTTP/service surface.

For our threat model, the authoritative boundary must also cover:

1. the entire unified HTTP ingress surface; and
2. internal/background executors that can run without a new HTTP call.

---

## 3. Direct evidence for high-risk bypasses

### 3.1 Gateway `/run`

`arena/gateway/handlers.py` validates a gateway-specific whitelist and then
calls `gw_run_sync` directly. It does not go through the MCP project-safe tool
dispatcher.

Result: **DENY endpoint in project-safe v0.**

### 3.2 v2 exec

`arena/api_v2/exec_handler.py` contains an unsandboxed path using
`asyncio.create_subprocess_shell`. Request data controls whether sandbox mode
is requested.

Result: **DENY endpoint in project-safe v0.**

### 3.3 Tasks

`POST /v1/tasks` submits a command to the task subsystem. The task runner:

- starts as a background task on application startup;
- uses `asyncio.create_subprocess_shell`;
- defaults `cwd` to `Path.home()` when not specified.

Result: route blocking alone is insufficient; **project-safe lifecycle must not
start the task execution loop** (or it must use a project-safe runner).

### 3.4 Batch

`POST /v1/batch` can issue authenticated loopback requests to arbitrary
`/v1/*` GET/POST endpoints.

A correct app-level middleware would also see those loopback requests, but the
batch endpoint itself provides no value in project-safe v0 and should be denied.

### 3.5 Standalone MCP

`arena/mcp/standalone_rpc.py` uses `arena.mcp.standalone_tools`, not the
unified MCP dispatcher.

The standalone path:

- has its own jail based on `Path.home()`;
- exposes `exec.exec`;
- can run browser/skill/subagent helpers.

Result: **standalone MCP servers are separate trust surfaces and must not be
started in project-safe v0.**

---

## 4. Startup / background execution audit

`arena/lifecycle.py` starts several background activities during normal app
startup:

- task runner;
- log cleanup;
- file watch loop;
- mission schedule loop (if wired);
- watchdog;
- post-update smoke hook;
- tunnel autostart hooks for cloudflared/ngrok/tailscale/bore.

This creates an important invariant:

> Project-safe mode cannot rely only on HTTP route denial.

Even with every dangerous REST endpoint blocked, a pre-existing queued task,
mission schedule or autostart marker/environment could cause work after startup.

### P0 lifecycle decision

In project-safe mode, default startup should be reduced to the minimum required
for the bridge:

**Keep / evaluate:**

- core HTTP server;
- audit/logging;
- emergency halt state;
- required MCP/extension support.

**Disable by default:**

- task runner;
- mission schedule worker;
- tunnel autostart hooks;
- post-update smoke that can mutate/restart;
- optional secondary servers;
- any background worker capable of executing project/system actions.

File-watch/watchdog behavior must be reviewed for side effects before being
left enabled.

---

## 5. Current workspace boundary helper

Current branch helper:

`arena/mcp/project_boundary.py`

Positive properties already present:

- explicit root required in project-safe mode;
- whole user home rejected as root;
- relative paths anchored to root;
- `Path.resolve()` used;
- `relative_to(root)` containment check;
- an additional `ctx.under_root` check;
- direct exact `.git` component denied;
- embedded NUL denied.

This is a useful baseline, but **Windows-specific P0 coverage is incomplete**.

---

## 6. Windows workspace-boundary matrix

Status legend:

- **COVERED** — current targeted test explicitly exercises the case.
- **LIKELY** — implementation may reject/allow it, but no Windows-specific proof.
- **GAP** — test or policy is missing.
- **KNOWN ISSUE** — current logic is likely insufficient.

| Case | Expected | Current evidence/status |
|---|---|---|
| relative file inside root | ALLOW | COVERED |
| absolute file inside root | ALLOW | COVERED |
| nested inside root | ALLOW | COVERED indirectly |
| `sub\\..\\file` resolves inside | ALLOW | GAP explicit test |
| `..\\outside` | DENY | COVERED |
| sibling prefix `project-evil` | DENY | GAP explicit regression |
| absolute `C:\\outside` | DENY | GAP Windows test |
| another drive `D:\\outside` | DENY | GAP Windows test |
| drive-relative `C:foo` | DENY unless proven inside | GAP |
| UNC `\\server\\share\\x` | DENY | GAP |
| extended path `\\?\\C:\\...` | DENY unless canonical inside and explicitly supported | GAP |
| device namespace `\\.\\...` | DENY | GAP |
| path case variant inside root | ALLOW | GAP Windows case test |
| case-variant escape | DENY | GAP |
| symlink inside -> inside | policy: ALLOW if canonical target inside | GAP |
| symlink inside -> outside | DENY | LIKELY via `resolve`, not proven |
| junction inside -> inside | policy decision / likely ALLOW | GAP Windows-only |
| junction inside -> outside | DENY | GAP Windows-only |
| nested reparse chain ending outside | DENY | GAP Windows-only |
| nonexistent leaf, parent inside | ALLOW for create/write | GAP |
| nonexistent leaf, parent through outside reparse target | DENY | GAP |
| exact `.git\\config` | DENY | COVERED |
| exact `.git\\hooks\\*` | DENY | GAP explicit |
| case variant `.GIT\\config` on Windows | DENY | **KNOWN ISSUE**: component comparison is case-sensitive |
| trailing-dot/space alias of protected name, if Win32 aliases it | DENY | GAP |
| 8.3/short-name alias reaching protected path | DENY if applicable | GAP / environment-dependent |
| NTFS alternate data stream `file:stream` | policy should DENY for agent writes unless explicitly needed | GAP |
| reserved DOS device names (`CON`, `NUL`, etc.) | DENY for writes | GAP |
| root missing | DENY | COVERED |
| root == whole user home | DENY | COVERED |
| root path itself replaced/disappears after startup | FAIL CLOSED | GAP |
| validate path then replace parent with junction before operation (TOCTOU) | must not escape | **GAP / not mitigated by current helper alone** |
| pre-existing hardlink inside workspace to an outside file on same volume | must not mutate unintended external content | GAP / requires explicit policy |
| direct access to bridge state/token outside workspace | DENY to agent | architecture currently separates state dir; regression still needed |

### Key boundary findings

1. **`.git` case handling is currently insufficient on Windows.**  
   Current code compares components against the literal string `.git`.
   Windows path semantics are case-insensitive, so `.GIT` needs explicit
   canonical/case-insensitive handling.

2. **TOCTOU is not solved by `Path.resolve()` alone.**  
   A path can be validated and then a parent can be replaced/reparsed before the
   actual file operation. P0 must decide whether to:
   - use OS-level sandboxing as the hard backstop; and/or
   - open/operate through safer handle-based techniques where practical; and/or
   - disallow risky reparse-bearing trees in project-safe mode.

3. **Nonexistent write targets need parent-based validation.**  
   Create operations must validate the nearest existing parent and the final
   canonical location semantics before writing.

4. **Windows-only tests are mandatory.**  
   Linux CI cannot prove junction/reparse/case/drive/UNC behavior.

---

## 7. Required P0 implementation work after this audit

This report intentionally does not apply the fixes. The next code slice should
be reviewed before implementation.

Required work, in priority order:

### P0-A — app-level HTTP fail-closed middleware

**Implementation status: IMPLEMENTED / TEST EXECUTION PENDING.**

The unified aiohttp app now installs `project_safe_http_middleware` before the
existing error middleware. When `ARENA_PROJECT_SAFE=1`, exact method/path pairs
are allowlisted and all unknown/unreviewed routes fail closed with 403.

The current reviewed surface includes health/version/status, control status +
emergency halt, required MCP transports and the browser-extension
status/policies/instructions/preview/execute endpoints. The extension source was
checked against this list; its localhost calls are covered.

When `ARENA_PROJECT_SAFE=1`, allow only a deliberately small route set such as:

- health/version/minimal status;
- MCP endpoint(s) required by the chosen client;
- extension policies/instructions/preview/execute;
- control status;
- emergency halt;
- CORS preflight as needed.

Everything else defaults to 403.

Do **not** enumerate dangerous routes. Enumerate allowed routes.

### P0-B — project-safe lifecycle

**Implementation status: IMPLEMENTED / TEST EXECUTION PENDING.**

In project-safe mode the normal lifecycle no longer starts:

- task runner;
- file-watch loop;
- mission scheduler;
- `ydotoold` desktop automation daemon;
- post-update smoke execution;
- tunnel autostart hooks for cloudflared/ngrok/tailscale/bore.

Log cleanup and the health-only watchdog remain enabled. Normal upstream mode
keeps the original startup behavior.

Do not start latent execution systems in project-safe mode:

- task runner;
- mission scheduler;
- tunnel autostart;
- post-update mutation/restart path;
- optional secondary servers.

Review file watcher/watchdog separately.

### P0-C — Windows canonical boundary hardening

Extend the canonical boundary helper and add Windows-targeted tests for the
matrix above, especially:

- case-insensitive protected components;
- UNC / drive-relative / device/extended paths;
- junction/reparse escape;
- nonexistent write targets;
- TOCTOU strategy.

### P0-D — standalone/secondary server policy

Safe launcher must not start standalone MCP/WS/stream or other secondary servers.
If project-safe support for them is ever desired, they must reuse the canonical
policy rather than maintain a HOME-based parallel jail.

---

## 8. Stop point

P0-A and P0-B have now been implemented. Per the agreed workflow, **stop here
before P0-C/P0-D functional changes** and review/test this slice first.

What is now known:

- the architecture is recorded;
- the actual ingress surface has been mapped;
- concrete bypasses have been identified;
- the Windows test matrix has been defined;
- P0 is demonstrably not complete.

Next action requires review/approval of this audit before implementing P0-A /
P0-B / P0-C.
