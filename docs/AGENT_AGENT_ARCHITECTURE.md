# Agent-Agent Architecture — Project-Safe Development Loop

Status: **architecture checkpoint / implementation contract**  
Branch: `hardening/project-safe-v0`  
Scope: local development workflow built on the Arena fork.

This document freezes the architecture agreed before further implementation. It
is intentionally stricter than upstream Arena defaults. The goal is to preserve
project integrity while allowing browser LLMs, a local Qwen observer, Arena
tools, Git worktrees and VS Code to participate in one controlled workflow.

## 1. System goal

Create a local, resumable development loop in which:

- VS Code is the operator control plane and project workspace;
- one explicitly selected Git worktree is the only mutable project area;
- ChatGPT Web acts as the main architect + executor-facing reasoning surface;
- DeepSeek Web and Qwen Web may later act as reviewers;
- local Qwen acts as an observer/analyst, not as the security authority;
- Arena is the local transport/execution harness;
- deterministic policy code decides ALLOW / DENY / NEED_USER;
- every mutation is attributable to a proposal/review/action chain;
- Pause / Resume / Stop are first-class states;
- quota/context/rate-limit conditions pause instead of causing retry storms.

The system must not depend on VS Code for security. VS Code is the operator UI;
Arena project-safe policy and, where needed, OS isolation form the security
boundary.

## 2. Core architecture

```mermaid
flowchart TB
    U[Operator]

    subgraph VS["VS Code — Control Plane"]
      V1[Project / Worktree]
      V2[Git panel + diff]
      V3[Terminal / tasks.json]
      V4[status.md + session_state.json]
    end

    subgraph WEB["Registered Browser Sessions"]
      G[MAIN_GPT\nChatGPT Web\nArchitect + executor-facing reasoning]
      D[DEEPSEEK_REVIEW\nReviewer]
      W[QWEN_WEB_REVIEW\nReviewer]
    end

    LQ[LOCAL_QWEN_OBSERVER\nRoutine / checkpoint / stuck / off-track\nPattern analysis]

    subgraph ORCH["Session / Process Layer — planned"]
      S[Session State]
      AJ[Action Journal\nAction IDs + idempotency]
      BR[Browser Role Binding]
      EP[Event + Provenance Log]
      SP[Pattern → Skill Promotion]
    end

    subgraph SAFE["Arena Project-Safe Core — P0"]
      HG[Central ingress / capability gate]
      PB[Canonical workspace boundary]
      TP[Tool allowlist]
      KS[Pause / Halt / STOP]
      BG[Project-safe lifecycle\nlatent executors OFF]
    end

    EX[Executor]
    WT[Selected Git Worktree]
    TEST[Tests / preflight / read-only diagnostics]

    U --> VS
    U --> BR
    VS --> ORCH
    WEB <--> ORCH
    ORCH --> LQ
    LQ --> ORCH

    ORCH --> HG
    HG --> PB
    HG --> TP
    HG --> KS
    HG --> BG
    PB --> EX
    TP --> EX
    EX --> WT
    WT --> TEST
    TEST --> ORCH
```

## 3. Non-negotiable security invariants

### 3.1 Security is core, not proxy

An outer orchestrator is **not** a security boundary.

Every executable ingress exposed by the Arena process must either:

1. pass through the same project-safe capability policy; or
2. be unreachable / denied while project-safe mode is active.

This includes MCP, REST, browser-extension execution, task/mission paths,
gateways, secondary protocol surfaces and any other mutating entry point.

If a new ingress cannot be proven to be gated, P0 remains FAIL.

### 3.2 One canonical workspace boundary

Project-safe mode requires an explicit workspace/worktree root.

Rules:

- whole user home is not a valid project-safe root;
- relative paths resolve against the configured root;
- canonical target must remain under the canonical root;
- direct `.git` internals are not exposed as ordinary file operations;
- path validation must account for Windows path semantics;
- traversal, symlink/junction/reparse escape, UNC/drive-relative edge cases and
  nonexistent write targets must be tested explicitly;
- string-prefix checks such as `startswith(root)` are forbidden as a boundary.

### 3.3 Whitelist tools, do not blacklist shell strings

Project-safe mode exposes only the tools required by the current stage.

Baseline:

- read/search/list workspace: allowed;
- Git status/diff/log: allowed;
- workspace edit/create/write: separately enabled;
- arbitrary host shell: denied;
- package/program installation: denied;
- network/tunnels/webhooks: denied;
- service/autostart/registry/admin changes: denied;
- desktop/mobile control: denied;
- Git push/reset/commit automation: denied in v0;
- agent-authored code execution: denied in v0 until its Windows isolation path
  is validated separately.

The local Qwen observer never overrides this policy.

## 4. Roles

### Operator

The operator owns:

- project/worktree selection;
- browser role binding;
- manual escalation decisions;
- enabling write mode;
- enabling any future AUTO skill;
- creation of a new browser conversation after context exhaustion;
- final merge/push decisions.

### MAIN_GPT

ChatGPT Web is the primary architect and reasoning surface. It may propose
project actions and receive execution/test results. It is not granted host-wide
authority merely because it proposes an action.

### Browser reviewers

DeepSeek Web and Qwen Web are advisory reviewers. They provide criticism,
alternatives and risk observations. They do not form a security boundary and
their agreement is not treated as proof.

### Local Qwen observer

Local Qwen is a project observer and analyst.

Planned responsibilities:

- classify routine / checkpoint / stuck / off-track;
- summarize structured project events;
- detect repeated workflow patterns;
- propose skill candidates;
- optionally act as a browser-adapter fallback by proposing locators from a
  restricted semantic DOM/accessibility snapshot.

Local Qwen must **not** decide filesystem/security permission. Deterministic
code does that.

## 5. Browser role binding

Browser automation may operate only on tabs explicitly registered by the
operator for the current project session.

A future binding record should contain at least:

```text
provider
tab_id
origin
conversation_fingerprint
assigned_role
session_id
```

Before an automatic browser action, the binding is revalidated.

If the tab disappeared, origin changed, or the conversation fingerprint no
longer matches, state becomes `WAITING_USER`. The system must not silently
search for a "similar" tab or bind a different personal conversation.

Global clipboard/key logging is out of scope. Semantic system events such as
`MESSAGE_FORWARDED` are preferred over observing Ctrl+C/Ctrl+V.

## 6. State machine

One action is in flight at a time.

```text
IDLE
  -> RUNNING
  -> WAITING_MODEL
  -> REVIEWING
  -> EXECUTING
  -> VERIFYING
  -> RUNNING

Exceptional / operator states:
  NEED_USER
  LIMIT_REACHED
  PAUSED
  INTERRUPTED
  STOPPED

Recovery rule:
  INTERRUPTED
    -> VERIFYING   when the previous action can be reconstructed and verified
    -> NEED_USER   when safe recovery cannot be proven
```

Rules:

- slow model generation is WAITING, not failure;
- 429/quota/cooldown does not trigger a retry storm;
- explicit Retry-After may be honored; otherwise resume requires policy/operator
  action;
- two unexpected execution failures should pause/escalate;
- context exhaustion produces a full stop; no automatic new browser chat;
- Resume must reconstruct state from persisted records, not model memory.

## 6.1 Browser interaction budget / pacing

Browser-facing models are treated as slow external advisory services.

The detailed policy is defined in:

- `docs/BROWSER_INTERACTION_PACING_POLICY.md`

Core invariants:

- one in-flight interaction per provider;
- no retry storms;
- explicit quota/rate-limit signals move the provider to `LIMIT_REACHED`;
- slow generation is `WAITING_MODEL`, not failure;
- no global clipboard/key logging;
- only registered project browser bindings may be forwarded/logged;
- browser payloads are compact review packets, not routine shell chatter;
- pacing is deterministic resource control, not human-behavior simulation;
- no automatic creation of replacement chats/accounts to bypass limits;
- auto-submit remains OFF until the provider workflow is explicitly approved.

## 7. Session state, action journal and provenance

These are separate concerns.

### Session State

Answers: *where are we now?*

Examples:

- project/session ID;
- goal;
- current stage;
- selected workspace;
- registered browser roles;
- current/pending action;
- pause/limit state.

### Action Journal

Answers: *what did the system do?*

Each action gets a stable `action_id`. The `action_id` is the idempotency
key shared across session state, the action journal, provenance and
verification records. Repeating an already completed `action_id` must return
the recorded result instead of executing again.

Required lifecycle example:

```text
PROPOSED -> REVIEWED -> READY -> EXECUTING -> VERIFIED
                               \-> FAILED
                               \-> INTERRUPTED
                               \-> DENIED
```

Replaying an already completed `action_id` returns the recorded result instead
of executing it a second time.

### Event + Provenance Log

Answers: *why did the system do it and where did the idea come from?*

A mutating action is incomplete unless it can link to its origin. An Action
Journal mutation record without a provenance chain is considered incomplete:

```text
proposal_id
  -> review_id(s)
  -> operator decision when required
  -> action_id
  -> workspace SHA before
  -> workspace SHA / result after
  -> verification/test event
```

Local Qwen receives structured semantic events, not a raw giant terminal/chat
log.

## 8. Learning mode and skills

Autonomy grows only after evidence.

```text
OBSERVE
  -> SUGGEST
  -> ASSIST
  -> AUTO
```

Pattern/skill lifecycle:

```text
PATTERN
  -> SKILL_CANDIDATE
  -> APPROVED
  -> ASSIST_ONLY
  -> AUTO_ELIGIBLE
  -> AUTO   (operator explicitly enables)
```

A skill is a declarative workflow, not a replay of historical shell commands.

Example shape:

```yaml
skill: architecture_review
version: 1
trigger:
  type: ARCHITECTURE_CHANGE
flow:
  - forward: DEEPSEEK_REVIEW
  - wait
  - return_review: MAIN_GPT
limits:
  max_rounds: 2
on_uncertainty: NEED_USER
forbidden:
  - execute_shell
  - install
  - git_push
```

Browser selector/adaptor caches are stored separately from workflow skills.

## 9. Browser-adapter fallback hierarchy

Preferred order:

```text
known deterministic adapter
  -> semantic/accessibility lookup
  -> validated local cache
  -> local Qwen locator candidates
  -> deterministic candidate validation
  -> NEED_USER
```

Qwen is a fallback locator proposer, not the final validator.

## 10. VS Code control plane

V0 uses existing VS Code primitives only:

- project/worktree explorer;
- Git panel and diff;
- terminal;
- `.vscode/tasks.json`;
- future `session_state.json`;
- future `status.md`.

A custom VS Code sidebar/webview is explicitly postponed until the state machine
and required fields stabilize.

## 11. Implementation order

No parallel feature expansion.

```text
P0  Arena project-safe core, fail-closed
    ├─ P0-A HTTP fail-closed allowlist
    ├─ P0-B project-safe lifecycle / latent executors OFF
    ├─ P0-C Windows workspace-boundary hardening
    └─ P0-D standalone / secondary server policy
          ↓
       P0 PASS GATE
          ↓
P1  Session state + action journal + idempotency
P2  One MAIN_GPT browser role binding + interaction budget
P3  Structured event + provenance collector
P4  Local Qwen in OBSERVE mode
P5  Second browser reviewer
P6  Pattern -> skill candidates / promotion
P7  VS Code UI refinement
```

**P0 PASS is a hard prerequisite.** No new orchestrator, local-Qwen observer,
browser-review automation or skill system should be implemented before P0 is
proven by tests on the intended Windows environment.

## 12. P0 definition of done

P0 is PASS only when:

1. all executable ingress paths are inventoried;
2. every mutating ingress is proved to pass project-safe policy or is denied;
3. one canonical path validator is used for relevant project file/Git paths;
4. Windows boundary tests cover traversal, case/path variants, symlink/junction/
   reparse escape, UNC/drive-relative paths and nonexistent write targets;
5. project-safe defaults fail closed when root/policy information is missing;
6. project-safe browser execution remains manual-confirm;
7. arbitrary host shell/system installation/tunnels/autostart are unavailable;
8. targeted tests pass on the intended Windows environment;
9. the draft PR remains unmerged until these checks are evidenced.

## 13. Current implementation status

Current truth as of 2026-10-06:

### Implemented / recorded

- explicit project root launcher;
- cautious profile instead of owner-shell;
- no automatic dependency installation in the safe launcher;
- no automatic killing of the process using port 8765;
- local state/token directory;
- project-safe MCP tool allowlist;
- MCP filesystem/search/diff/Git moved toward configured-root enforcement;
- whole-home fallback rejected in project-safe mode;
- browser-extension scope reduced to ChatGPT / DeepSeek / Qwen + localhost;
- browser safe-auto-run forced off in project-safe policy;
- targeted project-safe tests added;
- architecture checkpoint recorded;
- ingress / entry-point audit completed;
- Windows boundary-test matrix defined;
- browser interaction / pacing policy recorded;
- draft PR #1 remains the review container.

### Proven audit findings

P0 is currently **FAIL**, not because the design is unclear, but because the
current code still has executable surfaces outside the canonical project-safe
boundary. Proven examples include:

- direct v1/v2 exec paths;
- gateway `/run`;
- task submission + background task runner;
- mission/background scheduling;
- standalone MCP with a separate HOME-based jail;
- tunnel/post-update/background startup hooks;
- large direct REST/CDP/desktop/mobile/admin surfaces that are outside v0.

See:

- `docs/PROJECT_SAFE_P0_AUDIT_FINDINGS_2026-10-06.md`
- `docs/PROJECT_SAFE_P0_AUDIT_PLAN.md`

### P0 implementation progress

- **P0-A:** IMPLEMENTED — exact method/path HTTP allowlist middleware is wired
  into the unified aiohttp app; regression + middleware integration tests are
  present. Test execution remains PENDING.
- **P0-B:** IMPLEMENTED — project-safe startup disables task runner, file-watch,
  mission scheduler, ydotoold, post-update smoke and tunnel autostarts; normal
  mode remains unchanged. Test execution remains PENDING.
- **P0-C:** IMPLEMENTED — canonical/lexical containment, Windows path-shape
  rejection, case-insensitive protected names, reparse/hardlink write/read
  policy, recursive traversal validation and project-safe Git hardening are in
  the branch. Windows execution evidence remains PENDING.
- **P0-D:** IMPLEMENTED — standalone MCP HTTP/WS, gRPC-style secondary
  interface, Web Gateway, Input Helper and Serena MCP spawn are refused before
  bind/task/process creation in project-safe mode; standalone dispatchers and
  manually hosted handlers also fail closed.

Validation note: GitHub Actions has not produced workflow runs/statuses for the
fork branch, and the assistant runtime could not clone GitHub because outbound
network/DNS was unavailable. Therefore P0-A/P0-B/P0-C/P0-D are currently
**IMPLEMENTED / NOT RUN**, not PASS. P0-C additionally requires real Windows
execution evidence for junction/reparse behavior.

No P1 implementation begins until the targeted P0 validation run passes on the
intended Windows environment.

## 14. Explicit non-goals / rejected shortcuts

The following ideas are intentionally rejected for the project-safe design:

- **No LLM as security judge.** Local Qwen may observe or propose; deterministic
  code grants permission.
- **No global clipboard/keylogger.** Only semantic events from registered
  project browser bindings are eligible for project history.
- **No external proxy as the only security boundary.** The Arena core itself
  must fail closed.
- **No stealth / human-biometrics simulation.** Randomized mouse paths, fake
  typing mistakes, jitter, fingerprint spoofing and similar anti-detection
  techniques are outside the architecture.
- **No retry storm.** Slow models and provider limits produce WAIT/PAUSE states.
- **No skill auto-promotion.** Approved workflows pass through
  PATTERN -> CANDIDATE -> APPROVED -> ASSIST_ONLY -> AUTO_ELIGIBLE -> AUTO.
- **No VS Code extension yet.** V0 uses tasks, terminal, Git panel, status and
  state files until the state machine stabilizes.
