# Project-safe v2 integration plan

Date: 2026-10-08. Task: T89. Status: accepted implementation sequence;
T90-T95 are planned, not executed. Contract: [accepted T84 ADR](PROJECT_SAFE_P1_ACTION_CHECKPOINT_ADR.md).
Source baseline: `9484fa9429fd893f1e0c4d8d0c9fc28c6e04a754` (T88).

## Objective, constraints and completion boundary

Close the intermediate-success evidence P1 in the actual serial file-action
path: each SUCCEEDED owns an explicitly referenced, byte-validated checkpoint;
each next PREPARED uses its actual same-flow predecessor's output. Recovery is
read-only; a valid journal suffix still blocks admission until authorized
recovery projects it. No browser/generic action may bypass file verification.

Accepting T84 selects the contract, not a release or runtime switch. Keep
v1 parsers/bytes and T82/T83 diagnostics; never auto-upgrade, mix journals or
change global SCHEMA_VERSION. Runtime modules stay below 600 lines; prefer
existing validators/durable I/O and thin version adapters. Each task is split
into small reviewable commits if necessary, without enabling unfinished writes.

Second P1 closure requires storage-backed end-to-end evidence, not synthetic
model PASS alone. Full mutmut/preflight/release DoD remains a separate claim.

## FACT / HYPOTHESIS / UNKNOWN

FACT: T85/T86 export strict v2 models; T87 validates references; T88 compares
snapshot fields at explicit action/event prefixes. Production storage still
uses v1 parsers. Its v1 state/checkpoint writers reject v2 before publication.
Store RLocks are instance-local. A lease does not serialize threads sharing
the owner. No v2 file-action transaction/admission integration exists.

HYPOTHESIS: reusing those helpers behind one owner-scoped writer gate and
version dispatch will close the P1 without another journal. The tasks below
must demonstrate it with actual files and injected publication failures.

UNKNOWN: stronger power-loss durability (A08), multiple-state-root ownership
(A07), real executor integration (A09). Current fsync/replace primitives do not
establish parent-directory durability. Do not broaden process-crash results
into power-loss guarantees. A09 must be validated before product readiness.

## Source map and reuse

Paths below are under `arena/project_safe_session/`; target responsibilities
are design requirements, not newly available API names.

| Existing boundary | Reuse and required integration |
| --- | --- |
| `lease.py`, `registry.py`, store `_lock` | One gate per live project lease owner, shared by every store/coordinator/recovery/registry participant; registry has no instance RLock today, revision checks remain. |
| `storage.py`: `read_state`, `write_state`, `read_actions`, `append_action` | Explicit envelope/session-format dispatch, unchanged v1 branch, typed v2 branch; reuse `_read_journal`, hash/identity/transition/input checks and durable primitives. |
| `storage.py`: `read_checkpoint`, `write_checkpoint` | Reuse UUID/path/session binding, manifest digest, backup size/hash and immutable ID retry; dispatch parser and preserve v2 attempt ownership. |
| `storage.py`: `read_events`, `append_event`, payload/input methods | Events remain v1; join gate, keep tail and payload integrity semantics. Reads never repair newline tails. |
| `coordinator.py`: `create_session`, `_write_event_state`, activate/pause/start/close/refine methods | Add explicit fresh-v2 creation and v2 state projection; all event/state/registry operations participate in the gate. Do not let the v1 parser strip v2 envelope fields. |
| `recovery.py`: `ProjectSafeRecoveryManager.assess`, `enter_interrupted_recovery` | Version branch; reuse semantic validators, T87/T88 and byte-validating reads; separate assessment from authorized recovery writes. |
| `flow_evidence.py`, `lifecycle_evidence.py`, `phase_recovery_evidence.py`; recovery creation/goal helpers | Validate whole event history, then snapshot semantics at its prefix; audit assumptions before sharing private helpers. No duplicate replay implementation. |
| `v2_reference_history.py`, `v2_snapshot_projection.py` | Keep pure. Storage adapter resolves only explicit IDs; no checkpoint directory enumeration or shared stale cache. |
| `checkpointing.py`, `checkpoint_contract.py`, `action_contract.py`, `workspace.py` | Reuse resource capture/CAS/limits and workspace digest; generic after_digest keeps its existing meaning. |
| `__init__.py`, `registry.activate_session`, coordinator activation | Audit exports/call sites; current coordinator activation calls registry directly, without recovery assessment. Add denial before active-slot claim and PREPARED publication. |

## Ordered implementation blocks

| Task | Depends on | Scope and admission state | Acceptance evidence |
| --- | --- | --- | --- |
| T90: shared owner gate | T89 | Establish reentrant gate and acquisition order: live owner -> shared gate -> store/registry RLock. Include existing readers/mutators; no v2 enablement. | Two store/coordinator objects under one lease serialize; coherent assessment; lifecycle cleanup, lease loss, reentrancy and lock-order tests. |
| T91: versioned storage | T90 | Strict dispatch for state/actions/checkpoints. Add explicit v2 storage capability for tests/internal orchestration; external v2 writes remain denied. | Real disk round trips; exact v1 bytes; unknown/mixed/bool versions rejected before writes; wrong attempt/backup/hash and immutable retry; tail behavior preserved. |
| T92: read-only v2 recovery | T91 | Load coherent evidence, validate full history and snapshot prefix, classify gaps; no repair or admission enablement. | Missing intermediate checkpoint despite valid final checkpoint fails; corrupt backup/payload fails; prefix/event gaps and byte-exact nonmutation including registry. |
| T93: serial v2 writers and recovery projection | T92 | Fresh v2 session + serial file-action orchestration and explicit authorized projection repair, behind internal/test admission. No automatic migration or effect retry. | Fault injection at every publication boundary; ambiguous append reconciliation; cross-object concurrency; next PREPARED/flow close blocked until terminal and snapshot agree. |
| T94: restricted admission integration | T93 | One shared fail-closed policy at activation/resume and PREPARED/execution. Enable only explicit supported fresh-v2 serial file actions after all prerequisites. | Denial before slot/PREPARED/effect; drift/CAS, pending, partial effects, legacy and unknown formats; actual approved file operation transcript. |
| T95: P1 integration acceptance | T94 | Run full matrix on installed artifact and exact-head Windows; document closure scope and executor boundary. | Parent reproduction, sabotage + healthy suite, disk-backed multi-success/crash/read-only/concurrency cases, security, preflight comparison and explicit DoD status. |

Do not activate T94 merely because T91 can write a v2 record. T92 must not
declare clean solely because T88 returns an empty suffix. T93 must not claim
successful verification from a manifest existing without a SUCCEEDED marker.

## Data flow and coherent assessment

Under the shared gate, bind lease/project/session and supported formats; read
state, events and actions with hash/identity/payload validation. Resolve session
baseline plus every explicit success checkpoint through `read_checkpoint`.
Validate backup bytes once per distinct referenced ID using an assessment-local
cache only. Include unapplied successes; otherwise a corrupt tail masquerades
as a recoverable projection gap.

Reuse T87, then T88. Separately validate complete event semantics and snapshot
creation/goal/lifecycle/phase/recovery/timestamp fields at the applied event
prefix. Existing helpers that assume full state/history agreement must be
adapted carefully, not called against the full suffix and allowed to falsely
reject a legitimate gap. A valid suffix is recovery-required; invalid evidence
is integrity failure. Only after no unresolved gaps/effects and current
workspace/resource checks may admission consider the session consistent.

| Condition | Existing public reason / policy |
| --- | --- |
| Invalid state/version tuple | `STATE_CORRUPT`; deny writes. |
| Invalid event/action chain or truncated tail | Existing `EVENT_JOURNAL_CORRUPT`, `JOURNAL_CORRUPT`, `TRUNCATED_LAST_RECORD`; preserve deterministic precedence. |
| Invalid input/payload or explicit checkpoint/backups | `PAYLOAD_INTEGRITY_FAILURE` / `CHECKPOINT_INTEGRITY_FAILURE`; never substitute an orphan. |
| Valid snapshot-behind suffix, or impossible snapshot projection | `STATE_JOURNAL_MISMATCH` with a distinct diagnostic message; required=True. No new enum member assumed. |
| Unresolved physical attempt | `INTERRUPTED_ACTION`; no implicit success/retry. |
| Registry ownership / lease / current drift | Existing `REGISTRY_MISMATCH`, `LEASE_AMBIGUOUS`, `WORKSPACE_DRIFT` where applicable. |

T92 must pin precedence with tests when a gap and an interrupted attempt coexist.
Messages distinguish recoverable projection from integrity failure; a recovery
write must re-read/revalidate evidence under the gate, not trust a stale prior
assessment. Unsupported action types are admission denials, not invented
RecoveryReason aliases.

## Writer transaction and crash safety

Hold the shared gate through admission, capture, execution, verification and
snapshot publication for the initial serial file-action path. Revalidate
external resource/workspace drift immediately before physical mutation; the
gate does not lock out external editors. Publish in this order:

1. Input + PREPARED (event head / flow creation / predecessor refs), pending snapshot.
2. RESOURCE_BEFORE for exact attempt; EXECUTING before physical mutation.
3. Execute; VERIFYING; stable AFTER backup bytes then manifest; validating re-read.
4. SUCCEEDED with exact checkpoint ID/hash as verification commit marker.
5. One snapshot replacement with watermark, verified workspace, terminal/pending
   and active-flow expected-current; only then release the next-action barrier.

Ambiguous append success requires reading the chain before deciding whether to
append again. Never repeat effects automatically. Same checkpoint retry requires
identical bytes; a new attempt uses a new binding. FAILED/partial effects block
further admission; FAILED/NONE retries preserve logical anchors/context and
refresh prepared event head only. Advanced context requires a new proposal.

Authorized recovery projection handles only fully validated evidence, preserves
pending/effect semantics and monotonic revision, and changes no physical files.
It does not turn PREPARED/EXECUTING/VERIFYING or orphan AFTER into SUCCEEDED.
Interrupted marking remains a separate explicit recovery operation.

## Compatibility, rollout and rollback

Keep explicit v1 diagnostics with T83. Legacy sessions are not proof of the
v2 invariant and do not enter the new v2 file-action admission path. Preserve
existing unrelated v1 APIs/tests; an explicit v2 capability must not silently
change their defaults. Never append across formats, rewrite old hashes or GC
orphans on assessment. New v2 sessions start from a stabilized workspace after
resolving old pending/recovery state; no legacy success claims are imported.

Until T94, v2 storage/writer support is internal and admission disabled. Before
any production activation, verify the actual executor/entry point (A09) calls
the policy; model tests and exports alone do not establish that connection.
Public configuration/API naming is decided in its implementation task, with
negative call-site tests; this plan introduces no fictional flag.

Before v2 data exists, revert the isolated implementation commit. Once v2 data
exists, disable v2 write admission and retain data; do not run v1 writers on
it. Resume only with a v2-capable validated reader or an explicitly approved
future migration. Reverting T89 changes documentation only.

## Required test matrix and reporting

Use disk-backed two/three-success histories: changed workspace and no-op,
next FAILED/ABANDONED/pending, retry and flow reset. Sabotage explicit ownership,
predecessor/context, backup validation, watermark and gate/admission barriers.
Assert the mutation actually changes source, restore it and verify healthy code.

Inject failures after every durable append/replace/checkpoint publication,
including successful append with ambiguous response, complete no-newline tail,
truncated tail, orphan AFTER, snapshot behind/ahead and partial effects. Reads
must leave session and registry bytes unchanged. Compare fresh reads after
restart, not in-memory objects only. Exercise two objects sharing a lease,
unknown/mixed formats, downgrade denial, ignored target CAS, 16 MiB before
resource limit and external drift.

Every runtime block records WHAT/WHY, parent reproduction, sabotage/healthy
results, targeted regression, security, parent/current preflight differences,
exact checkout SHA for Windows and rollback. Do not report NOT RUN as PASS.
T89 is docs-only: runtime tests/mutmut are not rerun or claimed. Prior T88
Windows evidence is 612 PASS on its own SHA, not validation of future integration.

Second P1 may be declared closed only after the actual integrated path rejects
missing intermediate evidence and passes the complete scoped matrix. Release
still requires full green preflight, mutation ratchet/0 survivors and the
repository's remaining DoD; current existing debt is not waived by this plan.
