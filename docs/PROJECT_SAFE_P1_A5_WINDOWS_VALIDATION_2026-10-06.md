# Project-Safe P1-A5 Windows Validation — 2026-10-06

Status: **PASS**

GitHub Actions run: `37535922713`  
Runner: Windows Server 2025 / Python 3.12.10  
Result: **123/123 passed**.

## Validated

- deterministic `action_id = SHA256(canonical action-v1 identity)`;
- global workspace digest is not part of logical action identity;
- deterministic attempt IDs;
- storage rejects caller-supplied fake action IDs;
- one logical action cannot change immutable proposal/flow/input/provenance fields;
- invalid lifecycle jumps are rejected;
- retry is allowed only after `FAILED + effect=NONE`;
- partial-effect failures cannot be retried automatically;
- `SUCCEEDED` is terminal;
- semantic journal tampering is detected even if the attacker/test recomputes
  the JSONL record hash;
- first record for an action must be `PREPARED`;
- canonical input payload bytes are verified against both
  `payload_sha256` and semantic `args_hash`;
- therefore the payload actually executed cannot silently differ from the
  arguments used to compute/approve the action identity.

## Acceptance

P1-A5 is **CLOSED / PASS**.

The remaining foundational gap from the checkpoint contract is physical durable
checkpoint publication/validation. It will be implemented before the execution
coordinator is connected.

The pull request remains draft and unmerged.
