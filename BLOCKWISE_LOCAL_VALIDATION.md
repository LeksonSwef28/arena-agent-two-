# Blockwise development and deferred local verification

Working agreement (process only; no new CI or application behavior).

1. Split development into small, bounded components or cohesive change sets.
2. Prefer existing modules, tests and tooling. Do not rewrite validated,
   independent components without a demonstrated need.
3. A prior test PASS belongs to its **exact code revision, scope and environment**;
   it is not a blanket PASS for later commits or untested features.
4. For each new block, record changes, dependencies, risk, expected tests and
   status: `VERIFIED_AT_REVISION`, `PENDING_LOCAL_TEST`, `STALE`,
   `FAILED`, `BLOCKED` or `NOT_RUN`. Unexecuted work is never PASS.
5. Continue preparing code and regression scenarios while local/hosted test
   capacity is unavailable. Prefer independent changes; shared contract edits
   invalidate dependent historical results.
6. At an agreed local verification session, run affected component tests
   separately, capture their exit codes and logs, fix failures by component,
   then run a complete regression before any qualification/promotion.
7. Do not bypass existing required CI/security checks, real-environment gates,
   code review, approvals or release rules. A deferred local test is **not**
   permission to claim release readiness or merge unverified changes.
8. Preserve the existing project's architecture and domain-specific acceptance
   criteria. This note does **not** import Hiking Explorer code, profiles,
   repository layout, scripts or workflows.

No CI/workflow configuration is changed by this document.

## Project-specific boundary

This project already has security/quality CI gates and risk controls; keep
those gates and human-observed operational validation mandatory.
