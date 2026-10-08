# Browser Interaction Budget / Pacing Policy

Status: **architecture policy / not yet implemented**  
Scope: browser-facing model interactions in the Agent-Agent development loop.

This policy exists to prevent noisy loops, accidental quota exhaustion,
duplicate submissions and runaway browser automation.

It is **not** an anti-detection or stealth layer. The system must not try to
imitate human biometrics, randomize mouse trajectories, inject fake typing
mistakes, spoof browser fingerprints or otherwise disguise automation.

## 1. Core principle

Browser providers are treated as slow, external advisory services.

The local system may wait indefinitely for a valid response. Slow response is
not an error.

Only one outbound interaction per provider may be in flight at a time.

```text
READY
  -> SUBMITTED
  -> WAITING_MODEL
  -> RESPONSE_COMPLETE
  -> REVIEWED
  -> READY
```

Exceptional states:

```text
LIMIT_REACHED
PAUSED
NEED_USER
STOPPED
```

No state may transition directly from an error/limit condition into repeated
automatic resubmission.

## 2. Provider queue

Each browser provider gets its own queue and budget:

```text
MAIN_GPT
DEEPSEEK_REVIEW
QWEN_WEB_REVIEW
```

A provider queue has:

- at most one in-flight request;
- a monotonic `next_allowed_at` timestamp;
- last successful response timestamp;
- last failure/limit timestamp;
- consecutive failure counter;
- optional provider-reported Retry-After/deadline;
- a session-level interaction counter;
- a session-level forwarded-text budget.

The local Qwen observer may continue local analysis while a browser provider is
waiting, but it may not enqueue additional browser requests that violate the
provider budget.

## 3. Fixed pacing, not human imitation

The system uses deterministic cooldowns for stability.

Recommended initial defaults for PoC (local configuration, not provider limits):

- one in-flight request per provider;
- minimum fixed cooldown between completed browser interactions: 30 seconds;
- after a provider error: no automatic immediate retry;
- after two consecutive unexpected failures: PAUSED;
- after explicit quota/rate-limit indication: LIMIT_REACHED;
- resume after Retry-After only when such information is explicitly provided;
- otherwise resume requires operator action.

These values are conservative local defaults. They must not be interpreted as
service-authorized quotas and should be configurable per provider.

Do not add random delays for the purpose of appearing human.

## 4. Text transfer budget

Before forwarding text to a browser reviewer:

1. prefer a structured compact review packet;
2. strip terminal noise and duplicate context;
3. include only files/diffs/log excerpts needed for the review;
4. enforce a configurable maximum payload size;
5. if the packet exceeds the limit, summarize locally first;
6. never split one logical request into a burst of many browser messages unless
   the operator explicitly approves that workflow.

Recommended PoC fields:

```text
GOAL
FACTS
CHANGED_FILES
DIFF_SUMMARY
TEST_RESULT
OPEN_QUESTION
```

The browser model should not receive routine shell chatter such as every
`git status`, directory listing or repeated traceback line.

## 5. Copy / paste / forwarding

No global clipboard watcher and no global keyboard hook.

Only interactions inside registered project browser bindings are eligible for
semantic events.

Preferred event:

```json
{
  "event": "MESSAGE_FORWARDED",
  "from_role": "MAIN_GPT",
  "to_role": "DEEPSEEK_REVIEW",
  "source_message_id": "M-...",
  "payload_hash": "...",
  "payload_size": 1234
}
```

If the active browser tab is not one of the registered project bindings, Arena
must neither forward nor record its content.

## 6. Response completion

A response must be considered complete using deterministic UI/adapter evidence,
not an arbitrary sleep.

Examples:

- provider-specific adapter indicates generation stopped;
- Stop button disappears and message content stabilizes;
- accessibility state shows response controls have returned.

A timeout does not automatically cause a resend. It produces
`WAITING_MODEL` / `NEED_USER` according to policy.

## 7. Limit / quota handling

When the adapter detects an explicit provider message such as quota exhausted,
too many requests, cooldown, temporary unavailable or equivalent:

```text
current action -> LIMIT_REACHED
provider queue -> blocked
new requests to that provider -> DENY
```

The remaining local project state is preserved.

The system may continue work that does not require that provider, but it must
not silently substitute another browser account/chat or create a new
conversation to bypass the limit.

## 8. Context budget

Each bound conversation records approximate context consumption where possible.

Before a large forward:

- estimate payload size locally;
- prefer summaries/diffs over entire files;
- stop before known/observed context exhaustion;
- when context is exhausted, transition to `NEED_USER`;
- creation of a replacement conversation is an operator action.

The local Qwen observer should use structured events and summaries rather than
full browser transcripts.

## 9. Manual-confirm baseline

Until the browser policy is proven stable:

- auto-submit: OFF;
- auto-execute: OFF;
- automatic creation of new chats: OFF;
- automatic provider switching: OFF;
- automatic quota bypass/retry: OFF.

P2 may introduce carefully scoped automatic submission only if it is compatible
with the provider's permitted usage and the operator explicitly enables it.

## 10. Interaction audit fields

Each browser interaction should eventually record:

```text
interaction_id
session_id
provider
browser_role
conversation_fingerprint
submitted_at
response_started_at
response_completed_at
payload_hash
payload_size
result_hash
status
limit_reason
retry_after
derived_from_proposal_id
```

This allows pacing bugs and duplicate submissions to be diagnosed without
recording unrelated browsing activity.

## 11. Security / compliance boundary

Pacing is for:

- resource control;
- avoiding accidental request bursts;
- preserving context;
- protecting project state;
- respecting explicit provider limits.

Pacing is **not** for evading anti-bot systems or disguising automated behavior.

If a provider's terms do not permit automated browser interaction/extraction,
slowing the automation down does not make that use permitted. The architecture
must support a manual-confirm/manual-transfer mode for such providers.

## 12. Implementation order

This policy is documented now but implemented later.

```text
P0 project-safe core
P1 session/action persistence
P2 browser role binding + interaction budget enforcement
P3 event/provenance collector
P4 local Qwen OBSERVE
...
```

No browser pacing/humanization code belongs in P0.
