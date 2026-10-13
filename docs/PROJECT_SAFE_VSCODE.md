# Project-Safe VS Code mode

This fork keeps upstream Arena as the transport/executor, but adds a deliberately
small mode for one local development project.

## Goal

VS Code is the operator console and the Git worktree is the source of truth.
A local LLM may inspect and edit that worktree. Browser chats are advisory
surfaces selected by the operator. Nothing gets host-wide authority merely
because a model asked for it.

## Hard boundaries

When `ARENA_PROJECT_SAFE=1`:

- `--root` is mandatory and may not be the whole user home.
- MCP filesystem/search/diff/Git paths resolve relative to that root and reject
  traversal, symlink/junction escape, and direct `.git` access.
- The tool catalog is reduced before it is shown to an agent.
- Arbitrary host shell (`exec.exec`), package/system installation, network
  tools, service/autostart, tunnels, desktop/mobile control, external MCP
  installation, runtime installation and Git commit/push are unavailable.
- Browser execution stays manual-confirm even on a normally trusted chat site.
- Browser extension scope is only ChatGPT, DeepSeek and Qwen plus localhost.

Read-only mode exposes inspection tools. Write mode additionally exposes
`fs.create`, `fs.edit`, and `fs.write`, still only inside the selected root.
`code.run` is disabled by the launcher for now; it can be enabled later only
after its Windows AppContainer path is validated on the target machine.

## VS Code workflow

Recommended layout is a multi-root workspace:

1. Add this Arena fork as one folder.
2. Add the target Git worktree as the second folder.
3. Run **Arena: project-safe read-only** first.
4. Enter the absolute target worktree path when VS Code asks.
5. Only switch to **workspace-write** when you actually want the local agent to
   modify project files.

The launcher does not install packages, does not elevate, does not create a
service, does not expose a tunnel, does not kill a process that owns port 8765,
and does not copy the bearer token into the clipboard.

## Browser control

The extension side panel already has a tab picker. Use it to select the exact
ChatGPT / DeepSeek / Qwen tab. Keep these extension options OFF unless you
explicitly decide otherwise:

- Auto execute safe
- Auto insert result
- Auto submit result
- Generic adapter

Project-safe mode also tells the backend to refuse safe auto-run. The browser is
there for deliberate consultation, not a high-frequency hidden loop.

## Local LLM

The intended next layer is an MCP-capable VS Code agent using a local model
(Qwen via Ollama/LM Studio or another local runtime). Give it the project-safe
Arena endpoint, not owner-shell. Do not give the local agent an unrestricted
terminal extension in parallel, because that would bypass this boundary.

The local model should handle routine inspection/editing. External chats should
receive compact decision/review packets, not raw command churn.

## Control state machine

Use one in-flight action at a time:

```text
IDLE
  -> RUNNING
  -> WAITING_EXTERNAL      (model/site still generating or quota/cooldown)
  -> NEED_APPROVAL         (operator decision required)
  -> PAUSED                (manual pause / limit reached)
  -> RUNNING               (explicit resume)
  -> STOPPED
```

Rules:

- no retry storm; a quota/rate-limit response moves to WAITING/PAUSED;
- respect an explicit Retry-After when a service provides one;
- otherwise resume only on operator action;
- two consecutive unexpected execution failures should pause the loop;
- no automatic installation/elevation to "fix" a missing dependency;
- the existing Arena kill-switch is the emergency STOP for mutating tools.

Delays are for stability and rate limiting, not for disguising automation as a
human.

## Approval matrix

| Action | Project-safe v0 |
|---|---|
| read/search/list project | AUTO |
| git status/diff/log | AUTO |
| edit/create project file | only in workspace-write session |
| arbitrary PowerShell/cmd | DENY |
| run agent-authored code | DENY in launcher v0 |
| install package/program | DENY |
| network/tunnel/webhook | DENY |
| service/autostart/registry/admin | DENY |
| desktop/mobile control | DENY |
| git commit/push/reset | DENY; operator does it in VS Code |
| path outside selected root | DENY |

## Why not rely on `cautious` alone?

Upstream cautious intentionally supports a broad general-purpose workstation:
its first-word allowlist includes package managers and shells. That is useful
upstream but too wide for an unattended project helper. Project-safe therefore
adds a second, smaller, fail-closed capability gate.

Multi-agent bearer tokens are useful for audit/revocation, but are not a
capability sandbox: upstream documentation says they can call every endpoint
except agent-management endpoints. They must not be treated as the project
security boundary.
