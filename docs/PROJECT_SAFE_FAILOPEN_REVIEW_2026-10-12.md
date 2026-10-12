# T100 — project-safe fail-open candidate review

Scope: review five structural matches in `scripts/failopen_ratchet.py` against
actual model and evidence contracts. No runtime validator, detector algorithm,
numeric baseline, extension permission or workflow is changed.

The detector deliberately reports shapes rather than security verdicts. These
conditions depend on file existence or the presence of records; none depends on
whether an expected credential/hash was supplied. The existing line-specific
allowlist is used only after checking both the valid absence case and refusal
when required integrity evidence is absent or inconsistent. Moving a line
requires review again.

| Candidate | Why absence is valid | Refusal retained | Executable evidence |
| --- | --- | --- | --- |
| `action_models.py:52` | A nonexistent file has no content digest. | Strict boolean and SHA-256 parsing precede the branch; present files require a hash, absent files forbid one. Workspace CAS independently checks actual file existence. | `test_file_resource_before_distinguishes_absent_from_existing_empty`; workspace absent-resource CAS tests |
| `checkpoint_contract.py:44` | An absent file has no backup bytes. | Existing files require bytes matching their content hash; absent resources reject even `b""`. Empty existing files have a real SHA-256 and backup. | New direct checkpoint-builder tests for missing/changed bytes, empty files and absent-resource payloads |
| `checkpoint_models.py:53` | An absent checkpoint resource has no backup fields. | Existing resources require all integrity fields; both hashes are parsed and compared; absent resources require null fields and zero size. | `test_checkpoint_existing_backup_must_match_content_digest`; `test_checkpoint_absent_resource_has_no_backup_payload` |
| `phase_recovery_evidence.py:21` | No recovery event is normal outside recovery. | The `elif` rejects a recovery snapshot without its event; present events require a valid action ID and matching snapshot/action/flow evidence. | Rehashed detection, snapshot disagreement and unlinked snapshot tests |
| `v2_snapshot_projection.py:104` | No nonterminal action means there is no pending action context to compare. | Pending ID consistency and flow expected-current equality run independently; present pending actions must match the expected digest. | Pending-context disagreement, multiple pending actions and no-action workspace drift tests |

Git test fixtures now use `tests._git_budget.git_timeout()` and join the existing
module inventory. This bounds real Git processes without introducing a second
platform budget.

The extension scaffold test reuses `ALLOWED_HOSTS` and `ALLOWED_CHAT_MATCHES`
from the existing project-safe scope contract. Tunnel domains and `<all_urls>`
remain forbidden; the manifest itself is unchanged.

The expanded healthy regression has 239 passing tests. Disabling the five
reviewed validators causes 1, 2, 1, 9 and 1 test failures respectively; removing
the shared budget causes one failure and widening extension permissions causes
two. Each experiment has zero collection errors; runtime files are restored
byte for byte afterwards.

The full local source suite (`--no-cov`) reports 11,950 passed, five failed and
139 skipped. Only those five failing IDs were replayed on the parent commit,
where all five fail too. This is baseline evidence for those IDs, not a full
parent/current parity claim. T102 tracks their diagnosis.

Validation is recorded in T100 in `docs/TASK_BOARD.md`, including five targeted
validator sabotages, permission/budget negative controls, restored healthy
regressions and the security scan. Full mutation sweeps, hosted matrix acceptance
and v2 integration are separate open work; this review does not close them.

Rollback: revert the T100 commit. This restores the three reproducible CI
failures without changing runtime behavior.
