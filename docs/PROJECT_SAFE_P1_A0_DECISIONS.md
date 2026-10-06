# Project-Safe P1-A0 Foundation Decisions

Date: 2026-10-06  
Branch: `hardening/project-safe-v0`  
Status: **CONTRACT FROZEN FOR P1-A1**

P0 is closed. P1 starts with a deterministic persistence/lease foundation
before any browser or orchestrator execution is connected.

## Zero security invariant

The project-safe state root is security state, not workspace data.

> The canonical project-safe state root MUST NOT equal the canonical workspace
> root and MUST NOT be a descendant of it.

The check is fail-closed and happens before the state directory is created.
The path is checked again after creation/resolution to catch existing parent
links/reparse resolution. State-root configuration never grants workspace
authority.

Default future location:

- Windows: `%LOCALAPPDATA%\Arena\project-safe`
- POSIX: `${XDG_STATE_HOME:-~/.local/state}/arena/project-safe`

`ARENA_PROJECT_SAFE_STATE_ROOT` may override the default, but must be an
absolute, unambiguous path. User-supplied UNC/device/extended namespace forms
remain outside project-safe v1. Internal Win32 calls may use an extended-length
path form after validation so the lease itself is not limited by MAX_PATH.

## P1-A0 implementation choices

### Language and dependencies

The new foundation is **stdlib-only**.

The package's guaranteed core runtime dependency is aiohttp only. P1 state,
lease, schema validation and persistence must not require pydantic, attrs,
portalocker, filelock or another optional package.

Later P1-A2 models therefore use dataclasses/enums plus strict explicit
validators unless a separately reviewed change revises this decision.

### Canonical JSON v1

Canonical bytes are UTF-8 encoded from stdlib `json.dumps` with:

- `sort_keys=True`
- `separators=(",", ":")`
- `ensure_ascii=False`
- `allow_nan=False`

Exact file/message payload bytes are not newline-normalized.

### Windows project lease

Windows ownership is represented by a live kernel HANDLE, not by lock-file
existence.

The lease is opened with Win32 `CreateFileW` using:

- read/write access;
- `dwShareMode = 0` (exclusive sharing);
- `OPEN_ALWAYS`;
- the HANDLE held for the entire lease lifetime.

A stale lock file may remain after a crash. That is not ownership. When the
process dies Windows closes its HANDLE, so a later process can acquire the same
file. A second live owner receives a sharing violation and must fail closed.

POSIX uses a non-blocking exclusive `flock` behind the same interface.

No timeout steals a live kernel lease.

## Action lifecycle amendment

`ABANDONED` is a terminal action state for an action that was prepared but
will never be executed.

Allowed only while the physical effect is provably `NONE`.

Initial reasons:

- `WORKSPACE_DRIFT`
- `SUPERSEDED`
- `USER_CANCELLED`
- `GOAL_REFINED`

If an action has reached an unknown/partial executing effect, it cannot become
ABANDONED; it must go through recovery.

## Resource checkpoint policy v1

Ignored does not mean uncontrolled. An ignored file intentionally targeted by a
mutation still requires the same resource checkpoint, resource-before evidence,
CAS check and P0 path validation as a tracked file.

P1-v1 supports resource-before backup for existing files up to **16 MiB per
resource** for the project-safe `fs.edit` / `fs.write` mutation path.

Larger existing targets fail closed with `RESOURCE_TOO_LARGE`. Large
GeoPackage/PBF/database-style mutation requires a future separately designed
mechanism rather than silently copying multi-gigabyte resources into state.

This is a P1-v1 safe-transaction limit, not a global Arena file-size claim.

## Repository layout policy v1

Git submodules are unsupported in P1-v1.

If workspace manifest construction observes an index entry with mode `160000`,
admission fails closed with `UNSUPPORTED_REPO_LAYOUT`.

Submodule contents are never silently omitted from a supposedly complete
workspace guard.

## Three workspace digest roles

The names are deliberately distinct:

- `flow.workspace_digest_baseline` — admission source for the flow workspace
  guard;
- `session.workspace_digest_last_verified` — last verified session evidence;
- `action.workspace_digest_context` — evidence copied from the relevant flow
  context when the action was prepared.

The action ID does not contain a global workspace digest. Logical identity and
admission are separate concerns.

## Journal tail policy

Strict JSONL is read as bytes.

- A file ending in newline must contain only valid, correctly chained records.
- A final chunk without a newline is accepted only if the complete chunk is
  valid JSON and its sequence/hash-chain checks are valid.
- Such a complete no-newline tail is a valid record; before the next append the
  writer durably repairs the missing newline.
- An incomplete/invalid final chunk is `TRUNCATED_LAST_RECORD`.
- Any malformed non-final record or broken sequence/hash chain is
  `CORRUPT_JOURNAL`.

Both failure cases require recovery; readers never silently skip malformed
records.

## Digest / resource identity contract

`workspace_digest_v1` is Git-visible context:

- HEAD SHA;
- Git index state;
- tracked working-tree differences;
- non-ignored untracked files.

Ignored files are omitted from the global digest but never from resource-level
protection when explicitly targeted.

For file actions, structured `resource_before` is authoritative:

- canonical relative path;
- resource type;
- exists flag;
- content SHA-256 when it exists.

`effect_target_fingerprint` hashes that structured identity. Runtime CAS
checks the same structured `resource_before`; the hash itself is not used as
an opaque substitute for explainable preconditions.

## Checkpoint retention

P1-v1 performs **no automatic checkpoint GC**.

Checkpoints remain until an explicit user/operator deletion mechanism is
designed and approved. No age-, size- or session-completion-based automatic
deletion is introduced in P1.

## P1-A1 scope

The first physical slice implements only:

1. state-root resolution and the zero security invariant;
2. stable project fingerprinting;
3. a cross-platform `ProjectLease` with the Win32 exclusive-HANDLE backend;
4. targeted tests, including process-death lease takeover on Windows.

No state schema, action journal, checkpoint writer, workspace digest or real
mutation execution is connected in P1-A1.
