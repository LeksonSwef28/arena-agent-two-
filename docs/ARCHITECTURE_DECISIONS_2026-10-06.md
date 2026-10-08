# Architecture Decisions — 2026-10-06

Status: **accepted checkpoint**  
Branch: `hardening/project-safe-v0`

This file records the decisions agreed in the design discussion so future work
does not silently re-open settled questions or violate project integrity.

## Accepted

### Security

1. Arena project-safe core is the security boundary; an outer orchestrator is
   not.
2. Every executable ingress must use canonical policy or be unreachable/denied.
3. P0 uses an allowlist, not a command blacklist.
4. The selected Git worktree is the only mutable project area.
5. Local Qwen, ChatGPT, DeepSeek and Qwen Web never grant permissions.
6. Whole-user-home project roots are rejected in project-safe mode.
7. Direct `.git` manipulation is not exposed as normal file access.
8. Windows-specific path semantics require dedicated tests.
9. Background executors count as ingress/execution paths even when no HTTP
   request is involved.
10. P0 remains FAIL until HTTP ingress, lifecycle/background execution, Windows
    boundary and secondary-server policy are all proven.

### Process / state

1. One action in flight at a time.
2. Every action has a stable `action_id` used as the idempotency key.
3. Session State, Action Journal and Event/Provenance are separate stores.
4. A mutating action must be traceable:
   `proposal_id -> review_id(s) -> approval -> action_id -> verification`.
5. `INTERRUPTED` recovers only to `VERIFYING` when reconstruction is safe;
   otherwise it becomes `NEED_USER`.
6. P0 PASS is a hard gate before P1+.

### Browser / provider interaction

1. Browser tabs are explicitly bound to project roles.
2. Binding identity includes provider, tab ID, origin, conversation fingerprint,
   assigned role and session ID.
3. Binding mismatch yields `WAITING_USER`; the system does not silently bind a
   replacement chat.
4. Global clipboard and keyboard monitoring are excluded.
5. Semantic events such as `MESSAGE_FORWARDED` are preferred.
6. One in-flight browser request per provider.
7. Slow response means WAIT, not failure.
8. Quota/rate-limit signals mean LIMIT_REACHED/PAUSED, not retry storms.
9. Forward compact review packets instead of routine shell chatter.
10. Auto-submit remains off until a later explicitly approved stage.

### Local Qwen

1. Local Qwen begins in OBSERVE mode.
2. It analyzes structured semantic events, not giant raw logs.
3. It may classify routine/checkpoint/stuck/off-track and propose patterns.
4. It may later propose browser locator candidates as a fallback.
5. Deterministic code validates locator candidates.
6. Local Qwen is never a security gate.

### Skills

1. Skills are declarative workflows, not replayed shell macros.
2. Browser adapter cache and workflow skills are separate stores.
3. Promotion lifecycle:
   `PATTERN -> SKILL_CANDIDATE -> APPROVED -> ASSIST_ONLY -> AUTO_ELIGIBLE -> AUTO`.
4. AUTO requires explicit operator enablement.

### VS Code

1. VS Code is the operator control plane, not the security boundary.
2. V0 uses Explorer/Git/Terminal/tasks/status/state files.
3. A custom sidebar/webview is postponed until the state machine stabilizes.

## Explicitly rejected / out of scope

- `startswith(root)` path checks.
- An external Python proxy as the only security wall.
- Treating multi-agent bearer tokens as capability isolation.
- Global clipboard watchers/keyloggers.
- Automatically choosing a replacement browser tab/chat.
- Letting model consensus grant execution permission.
- Human-biometrics / stealth automation: fake typos, randomized mouse
  trajectories, jitter, browser-fingerprint spoofing or other anti-detection
  behavior.
- Using random delays to disguise automation.
- Immediate retry after quota/rate-limit/error.
- Automatically creating a new chat/account to bypass provider limits.
- Writing a new orchestrator before P0 PASS.
- Building a VS Code extension before process/state requirements stabilize.

## Current next implementation slice

No architecture expansion.

```text
P0-A  app-level HTTP fail-closed allowlist
P0-B  project-safe lifecycle / latent executors disabled
review + tests
P0-C  Windows workspace-boundary hardening
P0-D  standalone / secondary-server policy
P0 PASS
```

See also:

- `docs/AGENT_AGENT_ARCHITECTURE.md`
- `docs/PROJECT_SAFE_P0_AUDIT_FINDINGS_2026-10-06.md`
- `docs/BROWSER_INTERACTION_PACING_POLICY.md`
- `docs/agent-agent-architecture.mmd`
