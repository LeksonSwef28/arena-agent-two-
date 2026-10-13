# T102/T103 — local failures versus hosted validation

Baseline: the five local failures from T100 also reproduced on its parent.
On exact head `cea24156`, CI run `38221300911` passed all 15 Python/OS test
jobs (Python 3.10–3.14 on Linux, Windows and macOS). Overall CI remains red:
JS dependency installation and external service/security setup are separate.

| Failure | Evidence and cause | Bounded action |
| --- | --- | --- |
| Two inventory/probe tests | Traversal called `is_dir()` before rejecting symlinks. Stat followed an inaccessible target and raised PermissionError outside the iteration guard. Root/marker stat also lacked narrow I/O handling. | Shared directory enumeration rejects symlinks first, catches per-entry OSError and continues readable siblings; unavailable root/markers degrade via the existing result contract. |
| Missing-tesseract HTTP case | Tesseract is installed locally. The handler proceeds to screenshot acquisition and correctly reports missing screenshot tools instead. | Isolate the OCR module's `which` binding; retain the real HTTP handler and exact 503/missing-tesseract assertions. Shared shutil remains untouched. |
| urllib network-guard case | Inherited HTTP/HTTPS/ALL proxies use localhost. Socket traffic goes to intentionally permitted loopback, so a timeout occurs beyond the direct remote-socket refusal being tested. | Use a private opener with ProxyHandler({}) and retain the NetworkUseInTest assertion. Socket guard unchanged; this is a direct-client contract, not a claim to police external destinations behind an allowed local proxy. |
| MCP orphan native smoke | The controlled child is live to Popen/kernel. psutil first reported NoSuchProcess; later lookups can resolve. `/proc/self` reports a different PID from `os.getpid()`, demonstrating incompatible process/procfs views. | Keep identity and creation-time safeguards. Do not guess host PIDs, relax the guard, or alter/skip the native smoke test. Hosted native tests passed; this local capability remains unresolved. |

Inventory output keys, depth/limit and directory exclusions are retained.
The probes remain best-effort discovery rather than exhaustive filesystem
inventories; unreadable directories do not suppress readable siblings.

Ten synthetic access scenarios cover symlink target metadata, entry stat,
marker stat, root stat and directory enumeration for both venv and Git scans.
The original implementation fails eight; two existing iteration guards already
pass. Four targeted sabotages cause 2, 2, 3 and 1 failures with zero collection
errors. The first sabotage exposed a weakness in the new test: catching a stat
error masked that the symlink had been followed. An explicit observation of
symlink stat now detects that violation.

Validation/status: T102/T103 in `docs/TASK_BOARD.md`. The restored combined
regression is 195 passed / 17 skipped. Full-source rerun after these changes,
full mutation sweeps and exact-head hosted acceptance are not claimed.

Rollback: revert the inventory commit and/or the separate test-input commit.
