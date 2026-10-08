# ADR: causal action-to-checkpoint evidence

Date: 2026-10-08
Task: T84 / audit A06
Status: **ACCEPTED — PARTIALLY IMPLEMENTED; RUNTIME INTEGRATION PENDING**
Accepted: 2026-10-08 by explicit operator instruction (T89).
Acceptance approves the contract; it does not close the second P1, enable v2
admission, or satisfy release/full DoD.
Integration baseline: `9484fa9429fd893f1e0c4d8d0c9fc28c6e04a754` (T88).
Plan: [v2 integration sequence](PROJECT_SAFE_V2_INTEGRATION_PLAN.md).
Source baseline: `29dc546dff91f63c9d005cf989d634a77d3729af` (T83)

## Goal and scope

Make every successful workspace action's output verifiable from explicit
journal evidence, including intermediate successes. A later PREPARED must
use the output of its actual predecessor in the same flow. Assessment remains
read-only. This ADR does not close the second P1 or authorize a release.

The first implementation targets serial project-safe file actions. Browser
effects, generic read actions and partial-effect recovery need separate typed
verification contracts; they must not be admitted as workspace-verified
successes by silently making this evidence optional.

## Facts, hypotheses and unknowns

FACT at the baseline:

- `JournalRecord` v1 has journal/transition/attempt identity and workspace
  context, but no checkpoint reference.
- `CheckpointManifest` v1 identifies a session/action/kind and workspace
  digest, but not a physical attempt. Resource backups are validated.
- `EffectEvidence.after_digest` is nullable generic evidence, not a defined
  workspace output digest.
- `ProjectSafeSessionStore.read_checkpoint(id)` binds the requested directory,
  manifest digest, session and backup bytes. `write_checkpoint` writes backups
  before the manifest and rejects conflicting reuse of a published ID.
- Each store has its own RLock. Coordinator operations have no shared
  transaction lock. A live project lease does not serialize threads that
  already share that owner.
- Actions and events have separate hash chains. Their timestamps do not
  establish a total order. T83 checks only the suffix after the latest success.
- `durable_replace` fsyncs a temporary file and calls os.replace; it does not
  fsync the parent directory. No stronger power-loss guarantee is established.

HYPOTHESIS to validate in implementation: explicit references plus a shared
serial writer gate can close intermediate evidence without a third journal.

UNKNOWN: platform power-loss durability (A08), multiple-state-root lease
ownership (A07), and actual executor integration (A09). These remain separate
tasks and must not be reported as solved by this ADR.

## Alternatives

| Choice | Benefit | Failure mode / decision |
| --- | --- | --- |
| Enumerate checkpoint directories by action ID | No journal change | Missing/duplicate/orphan candidates; no attempt or causal ordering. Rejected. |
| Treat effect.after_digest as workspace digest | Small diff | Changes generic semantics; supplies no exact checkpoint or backups. Rejected. |
| Separate checkpoint-index journal | Keeps action v1 | Adds a third chain and cross-journal crash gaps. Deferred. |
| Typed references in action v2, checkpoint v2 attempt identity | Exact objects and causal order | Requires explicit version dispatch and writer/recovery changes. Accepted. |

## Accepted wire contract (models exist; runtime APIs require integration)

Use strict versioned parsers per model family. Do not simply change the common
`SCHEMA_VERSION` constant: that would accidentally change unrelated wire
contracts. New sessions use a v2 state envelope and supported format versions;
unknown or mixed versions within a journal fail closed. The initial supported
format tuple is state=2, actions=2, checkpoints=2, events=1, registry=1,
canonical JSON=1 and workspace digest=1. Cross-family differences are explicit,
not implicit upgrades. v1 parsers and bytes remain unchanged.

Journal v2 adds the following fields; all are included in canonical record hash:

| Field | Shape / invariant |
| --- | --- |
| `flow_creation_ref` | `{event_seq, record_hash}` identifying this flow's exact FLOW_CREATED record; required and immutable across the logical action. |
| `prepared_event_ref` | `{event_seq, record_hash}` for the event head observed at this attempt's PREPARED; required and immutable within this attempt, refreshed for a new permitted attempt. |
| `preceding_success_ref` | Null for the first action context in a flow; otherwise `{journal_seq, record_hash}` identifying the latest prior SUCCEEDED of this flow at PREPARED. Required key, immutable across the logical action. |
| `workspace_verification` | Null except SUCCEEDED; on SUCCEEDED exactly `{checkpoint_id, manifest_sha256}` identifying the RESOURCE_AFTER checkpoint for this physical attempt. |

Checkpoint v2 adds `attempt_id` (SHA-256): null for SESSION_BASELINE, required
for RESOURCE_BEFORE / RESOURCE_AFTER. Existing action/session/kind fields,
resource validation and manifest hash remain authoritative. No free-form path
or inferred directory lookup is added to journal references.

State v2 adds required `formats` with exact keys `state`, `actions`,
`checkpoints`, `events`, `registry`, `canonical_json`, `workspace_digest` and
the supported tuple above. Missing, unknown or unsupported entries fail closed.

State v2 adds `execution.last_applied_action_ref`: null before action projection,
otherwise `{journal_seq, record_hash}` for the last action record incorporated
into this snapshot. Existing state revision/event watermark checks remain.
This watermark identifies a snapshot-behind-journal gap; it is not permission
to ignore intervening evidence or claim cross-journal total order.

IDs remain deterministic action-v1 and attempt-v1 identities: references are
provenance, not new logical action identity inputs. Add logical flow/predecessor references to the
immutable identity projection and validate prepared_event_ref per attempt,
without changing compute_action_id. Retry is
allowed only under the existing FAILED/NONE contract and with unchanged
context/anchors. If a later success has advanced context, an old logical
action cannot be retried by changing its immutable fields; a new proposal
must receive a new logical action identity.

## Replay invariants

1. Validate journal hashes, sequence, deterministic identities, payloads and
   action transitions first. Reference seq values must be integers (not bool),
   strictly prior and match the referenced record's hash and type.
2. Resolve flow_creation_ref in the validated event journal; compare session,
   flow ID and baseline. For every action record, FLOW_CREATED must occur in
   its event prefix and FLOW_CLOSED must not occur there.
3. To define that event prefix, journal v2 requires
   `prepared_event_ref = {event_seq, record_hash}`: the event head observed
   at each attempt's PREPARED under the shared writer gate, copied unchanged
   to later records of that attempt. Successive PREPARED event-head sequences
   cannot decrease under the shared writer gate. A new attempt refreshes this reference
   while keeping logical flow/predecessor anchors unchanged. The prefix includes
   creation and excludes closure. This closes preparation ownership only;
   it does not prove the time of later execution. Writer flow closure is
   prohibited while an action is pending; terminal-event ordering beyond
   preparation remains outside this contract.
4. Maintain latest successful output per flow while scanning journal_seq order.
   PREPARED's preceding_success_ref must be exactly that predecessor, not just
   any old success. With no predecessor, context equals the flow baseline;
   otherwise context equals its validated checkpoint workspace digest.
5. Every SUCCEEDED has exactly one explicit after-checkpoint reference. Resolve
   only that ID through read_checkpoint; require v2, matching manifest hash,
   session, action, attempt and RESOURCE_AFTER kind. Validate resources/backups.
   A checkpoint ID may be owned by only one successful record/attempt; other
   orphan manifests never supply missing evidence. Same digest/no-op is valid.
6. Update flow output from that manifest. Keep generic effect digests separate.
   File resource verification/CAS must still protect ignored targets that are
   absent from the Git-visible workspace digest.
7. Compare snapshot projections at their explicit action watermark; separately
   classify a validated journal suffix. Latest verified snapshot must refer to
   the latest applied success checkpoint. A new flow resets its admission
   baseline; it does not inherit a previous flow's context anchor.

Replay must not infer successful verification from a current clean workspace,
timestamps, action_seq, directory enumeration or snapshot fields alone. Hashes
detect inconsistency; without a trusted external anchor they do not authenticate
history against an attacker who can rewrite the whole state root.

## Writer ordering and serialization

One project/session writer gate must be shared across store/coordinator objects
for this lease owner, with consistent acquisition order: project owner, shared
gate, store RLock. State/events/flow mutations participate in the same gate.
Assessments take a coherent read under that gate but never write. Do not hold
only one store object's RLock and assume another store cannot interleave.

Use one pending logical action at a time. Hold the shared gate for the initial
file-action transaction, including verification; no next PREPARED or flow
close is admitted until its terminal record and snapshot projection agree.
This is a deliberate serial design, not a concurrent executor scheduler.
FAILED with partial/unexpected effect blocks further admission until explicit
recovery resolves it; it cannot advance expected-current or be treated as a
FAILED/NONE retry. This is a v2 admission requirement, not a claim about v1.

Publication order for a successful file action:

1. Verify flow/context/admission; persist input and PREPARED with event and
   predecessor anchors, then snapshot pending state.
2. Capture and validate RESOURCE_BEFORE for this attempt using existing
   resource/CAS safety. Persist EXECUTING before any physical mutation.
3. Execute, persist VERIFYING, stabilize resource/workspace evidence and
   publish RESOURCE_AFTER backups, then manifest. Re-read and validate it.
4. Append SUCCEEDED with its exact reference; this is the verification commit
   marker. Never publish SUCCEEDED before its referenced checkpoint.
5. Publish snapshot advancement: expected-current, last-verified fields,
   terminal/pending fields, and last_applied_action_ref in one state replacement.
6. Only then admit another action. No automatic retry/rollback after an
   ambiguous append response; read the journal to determine whether the
   terminal transition is already present.

Retries of checkpoint publication reuse the same ID and identical bytes as
supported by write_checkpoint. A new physical attempt gets a new checkpoint
ID/attempt binding. Re-appending an already present terminal marker is rejected;
idempotent orchestration recognizes the existing identical transition.

## Crash-gap outcomes (process crash, not a power-loss claim)

All classifications below are accepted target behavior, not existing RecoveryReason
enum members. Map them explicitly when implementing; do not invent aliases.

| Durable evidence after interruption | Required behavior |
| --- | --- |
| PREPARED ahead of snapshot | Recovery required; no effect assumed; inspect existing transition semantics. |
| EXECUTING / VERIFYING without SUCCEEDED | Interrupted action; no success inferred; no auto retry/rollback. |
| Partial backups or published AFTER, no SUCCEEDED reference | Orphan evidence; pending remains interrupted; never promote checkpoint to success. |
| SUCCEEDED and valid checkpoint, snapshot behind | Recovery required, explicitly classify projection gap; assess must not repair it. An authorized recovery operation may project validated evidence later. |
| SUCCEEDED references missing/corrupt/wrong checkpoint | Integrity failure requiring recovery; do not fall back to another directory. |
| Snapshot advanced without SUCCEEDED evidence | State/journal mismatch. |
| Fully projected success | Clean only after all chain, checkpoint, flow, resource and current-workspace checks pass. |

Missing data must fail closed even if it could be lost in a power failure. This
does not prove the ordering survives power loss. A08 durability research/tests
remain a prerequisite for a stronger guarantee; changing durability primitives
is outside the reference-contract slice.

## Compatibility, migration and rollback

- Continue explicit read-only v1 diagnostics with T82/T83 guards. Report legacy
  evidence as incomplete, not as verified-v2 or proof that the second P1 closed.
- Never append v2 records to v1 journals or rewrite their hash chains on read.
  Historical missing references cannot be reconstructed reliably.
- Initial migration policy: no in-place conversion. Explicitly archive/retain
  the old session and create a fresh v2 session from a stabilized admitted
  workspace after resolving pending/recovery state. Do not copy legacy success
  claims into the new journal. No automatic checkpoint GC.
- Readers fail closed on unsupported versions. Old binaries reject v2 state,
  preventing accidental downgrade writes. Rollback after v2 data exists means
  retaining v2 data and disabling write admission; it cannot resume old writers
  against those files. A future migration tool requires its own ADR/tests.
- Current T84 rollback is only reverting documentation; no v2 data is written.

## Implementation sequence and acceptance

1. Strict versioned models, canonical hash fixtures and negative parser cases;
   update actual exports/call sites, no stale aliases. No admission yet.
2. Pure replay using explicit IDs and existing checkpoint validation; cover
   arbitrary-length multi-success flows, then assessment-local checkpoint-read
   caching (one validated read per referenced ID; no shared stale cache). Replay
   should be linear in event/action records plus referenced backup bytes.
3. Shared gate, writer ordering, state watermark/projection recovery and fault
   injection at every publication boundary. Only then enable v2 admission.

Mandatory test matrix before claiming the second P1 closed:

- two/three successes with changed and no-op digests; next FAILED/ABANDONED and
  pending states; retries of FAILED/NONE and rejection after context advances;
- missing reference/checkpoint, wrong manifest hash/session/action/attempt/kind,
  corrupt backup, duplicate ownership, stale/skipped/forward predecessor;
- new/closed flow, wrong creation/event-prefix anchor and absent referenced event;
- every crash boundary above, ambiguous successful append, complete no-newline
  tail, truncated tail, immutable checkpoint retry and orphan non-adoption and FAILED/partial-effect admission refusal;
- snapshot watermark behind/ahead/wrong hash; byte-exact read-only assessment;
- interleaved calls through two store objects under one lease cannot publish
  a next PREPARED or FLOW_CLOSED in the transaction gap;
- v1/v2/unknown/mixed formats, downgrade rejection, no automatic migration/GC;
- ignored targeted file, resource CAS, 16 MiB before-resource limit and external
  workspace change between admission and execution;
- parent reproduction, bilateral sabotage, targeted suite, security/preflight
  comparison and exact-head Windows execution. Full mutmut/preflight/release
  DoD is tracked separately and must be reported honestly.

## Source cross-check and validation status

Inspected baseline sources: action_models.py, action_contract.py,
checkpoint_models.py, checkpoint_contract.py, storage.py, durable_io.py,
state_models.py, coordinator.py, flow_evidence.py, action_flow_evidence.py,
and PROJECT_SAFE_P1_A0_DECISIONS.md under this repository.

The T84 commit changed documentation only. At T84 no proposed parser, writer,
replay or crash behavior had been executed. The baseline T83 Windows push run
37646224541 passed 385 tests on its exact runtime SHA; that result is not a
Windows validation of a new documentation commit or of this proposed contract.


## T85 implementation progress (2026-10-08)

The first model slice adds EventRecordRef, ActionRecordRef,
WorkspaceVerificationRef, JournalRecordV2 and CheckpointManifestV2 as opt-in
exports. Strict v2 envelopes reuse v1 common-field validation; global
SCHEMA_VERSION remains 1. The existing v1 checkpoint writer now rejects an
unsupported schema before any write. Tests cover local reference shape/order,
SUCCEEDED-only verification, attempt presence/kind, v1 compatibility and pinned
canonical hashes. Storage parsers/admission are not switched to v2.

State v2 format tuple/watermark, actual reference resolution, hash-chain replay,
logical/per-attempt immutability, shared writer gate and crash recovery remain
unimplemented. Structural model acceptance is not verification of history or
checkpoint bytes. The second P1 remains open and at T85 this ADR stayed PROPOSED.


## T86 implementation progress (2026-10-08)

Opt-in FormatVersionsV2, ExecutionStateV2 and StateSnapshotV2 now validate the
format tuple and action watermark shape, reusing the v1 common-field parsers.
The v1 state writer rejects unsupported schema versions before reading or
publishing state. Production readers/admission still use v1; no v2 session is
created and no legacy state is migrated.

The watermark is not compared to last_action_seq: journal transitions and
logical actions use different counters. Its hash, prefix membership and
projection consistency require the next pure replay slice. This model slice
does not prove history, checkpoint ownership or crash-gap behavior. The second
P1 remained open; at T86 the ADR remained PROPOSED.


## T87 implementation progress (2026-10-08)

The opt-in pure validate_v2_reference_history helper reuses storage hash-chain
validation, the action-v1 identity/transition contract and manifest hashing. It
checks event-head/flow references, preparation-prefix closure, logical anchors,
per-attempt prepared-head immutability, exact latest same-flow predecessor and
workspace context. Every SUCCEEDED must resolve its explicit v2 RESOURCE_AFTER
manifest with matching ID/hash/session/action/attempt and unique ownership.
It returns ordered V2SuccessEvidence; it does not enumerate checkpoint storage.

Inputs and outputs are model-level evidence. Session/goal/flow event semantics,
payload/backup bytes, resource CAS, snapshot projection and writer serialization
remain caller obligations. A closure after the prepared prefix does not prove
terminal execution order. There is no storage/admission/recovery integration,
so the second P1 remains open. No authentication of a fully rewritten history
is claimed. Synthetic histories validate this helper, not production readiness.


## T88 implementation progress (2026-10-08)

The opt-in pure assess_v2_snapshot_projection helper validates all references
through T87, then compares action/workspace/active-flow fields at the exact
last_applied_action_ref and last_event_seq prefixes. It binds session baseline
and last-verified checkpoint ID/digest/head, terminal/pending fields and active
expected-current. Applied preparation references must fit the event prefix.

Execution counters preserve existing v1 semantics: among latest records per
logical action, last_action_seq/last_terminal_action_id identify the terminal
action with highest action_seq. A retried FAILED action whose latest marker is
PREPARED is pending, not terminal. journal_seq is never substituted for that
counter. More than one pending action at the snapshot prefix is rejected.

V2SnapshotProjection returns explicit unapplied action/event suffixes and
snapshot_behind. It never repairs state and exposes no clean RecoveryAssessment
or write-admission verdict. Corrupt unapplied success evidence fails before gap
classification. Timestamp projections, complete session/goal/lifecycle semantics,
payload/backup bytes, resource/current-workspace checks, historical global
serialization and recovery integration remain open. The second P1 stays open.


## T89 acceptance and integration plan (2026-10-08)

The operator accepted this contract after T85-T88 model-level validation.
The linked integration plan records actual call sites, sequential dependencies,
read-only recovery, restricted admission and rollback once v2 data exists.
Historical PROPOSED statements above describe their original implementation
blocks, not the current ADR status. No runtime/schema/workflow bytes change
in T89. Second P1, executor integration and full DoD remain open.


## T90 shared owner gate (2026-10-08)

ProjectLease now owns a reentrant operation gate. Store public reads/writes,
registry read-modify-write calls, coordinator lifecycle transactions and full
recovery assessment/operations participate before instance-local RLocks. Lease
acquire/release serialize on the same gate; release cannot nest inside an owner
operation. An owner generation prevents old bound objects from resuming after
release/reacquire. Independent projects remain independent.

This gate only serializes participating calls within one live lease owner.
It does not exclude external editors, solve multiple-state-root ownership A07,
provide power-loss durability, or implement the future file executor transaction.
Existing crash gaps remain recovery-required, not automatically repaired.
Storage versions/writers/admission remain v1; the second P1 is still open.


## T91 versioned storage capability (2026-10-08)

Internal/test ProjectSafeSessionStoreV2 selects strict v2 state/action/checkpoint
parsers through a shared immutable storage policy. Default store/coordinator and
recovery remain v1. Events remain v1. Every operation checks an existing state
envelope against the selected version and supported format tuple before writing;
initial state publication also rejects an incompatible existing action journal.
No automatic version inference, upgrade, migration or admission is introduced.

Existing durable publication, manifest/path/session and backup/input byte checks
are reused. V2 local action-history anchors and preparation ordering are shared
with T87 through one helper; storage does not resolve causal event/checkpoint
references or grant success admission. T92 must do that with real storage reads.
The lower-level capability can persist structurally valid evidence only; T93
still owes ordered orchestration and T94 admission. Second P1 remains open.
