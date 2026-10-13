# Project-Safe P1-A2/A3 Windows Validation — 2026-10-06

Status: **PASS**

## P1-A2 — strict schemas/models

GitHub Actions run: `37526900973`  
Runner: Windows Server 2025 / Python 3.12.10  
Result: **88/88 passed**.

Validated:

- stdlib-only strict schemas;
- exact-key validation;
- UUID/SHA/timestamp shape checks;
- lifecycle status/phase/reason consistency;
- stale browser binding rules;
- file resource-before semantics;
- `ABANDONED + effect=NONE`;
- checkpoint resource integrity;
- canonical JSON stability;
- architecture module-size boundary.

The original 854-line model draft was split before validation into focused
schema modules. No architecture-boundary allowlist exemption was added.

## P1-A3 — durable state and strict journals

GitHub Actions run: `37527835478`  
Runner: Windows Server 2025 / Python 3.12.10  
Result: **99/99 passed**.

Validated:

- state writes require a live `ProjectLease`;
- `state_revision` advances exactly by one;
- durable temp-file + flush/fsync + `os.replace` publication;
- strict JSON rejects duplicate keys and non-finite constants;
- action/event journals own their sequence numbers;
- SHA-256 hash chain validation;
- malformed newline-terminated record => corruption;
- invalid unterminated tail => `TRUNCATED_LAST_RECORD`;
- complete valid final record without newline is accepted and repaired before
  the next append;
- immutable action payload storage;
- payload digest mismatch is detected;
- existing P0/P1-A1 gates remain green.

## Acceptance

P1-A2 and P1-A3 are **CLOSED / PASS**.

P1-A4 workspace digest and resource-CAS validation may proceed. The pull
request remains draft and unmerged.
