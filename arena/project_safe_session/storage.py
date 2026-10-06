"""Durable state and strict hash-chained JSONL storage for project-safe v1."""
from __future__ import annotations

import hashlib
import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Generic, Mapping, TypeVar

from .action_contract import (
    ActionContractError,
    action_identity_from_draft,
    compute_attempt_id,
    validate_action_history,
    validate_action_record,
)
from .canonical import canonical_json_bytes, canonical_sha256, strict_json_loads
from .event_models import SessionEventRecord
from .lease import ProjectLease
from .models import JournalRecord, StateSnapshot
from .paths import project_fingerprint
from .schema_types import SCHEMA_VERSION
from .schema_utils import SchemaError, exact_keys, relative_path_value, sha256_value, uuid4_value

TRecord = TypeVar("TRecord")


class StorageError(RuntimeError):
    """Base error for durable project-safe storage."""


class LeaseRequiredError(StorageError):
    """A mutating storage operation was attempted without the project lease."""


class StateRevisionError(StorageError):
    """state_revision did not advance exactly by one."""


class JournalCorruptionError(StorageError):
    """A newline-terminated journal contains malformed or inconsistent data."""


class TruncatedLastRecordError(StorageError):
    """The final non-newline-terminated journal record is incomplete/invalid."""


class PayloadIntegrityError(StorageError):
    """Immutable payload bytes are missing or do not match the recorded digest."""


@dataclass(frozen=True)
class JournalReadResult(Generic[TRecord]):
    records: tuple[TRecord, ...]
    missing_trailing_newline: bool


_ACTION_DRAFT_KEYS = {
    "action_id", "flow_id", "attempt_seq", "attempt_id", "state", "effect",
    "recorded_at", "proposal_id", "proposal_digest", "action_type",
    "effect_target_fingerprint", "risk", "input", "workspace_digest_context",
    "reason",
}
_EVENT_DRAFT_KEYS = {"event_type", "recorded_at", "data"}


def _durable_replace(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    try:
        with tmp.open("wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def _durable_append(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("ab") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())


def _record_hash(raw: Mapping[str, Any]) -> str:
    without_hash = dict(raw)
    without_hash.pop("record_hash", None)
    return canonical_sha256(without_hash)


def _validate_hash_record(
    raw: dict[str, Any],
    *,
    seq_name: str,
    expected_seq: int,
    previous_hash: str | None,
) -> None:
    seq = raw.get(seq_name)
    if isinstance(seq, bool) or seq != expected_seq:
        raise JournalCorruptionError(
            f"{seq_name} mismatch: expected {expected_seq}, got {seq!r}"
        )
    if raw.get("previous_record_hash") != previous_hash:
        raise JournalCorruptionError(
            f"previous_record_hash mismatch at {seq_name}={expected_seq}"
        )
    actual = raw.get("record_hash")
    if not isinstance(actual, str) or actual != _record_hash(raw):
        raise JournalCorruptionError(f"record_hash mismatch at {seq_name}={expected_seq}")


def _read_journal(
    path: Path,
    *,
    parser: Callable[[Any], TRecord],
    seq_name: str,
    session_id: str,
) -> JournalReadResult[TRecord]:
    if not path.exists():
        return JournalReadResult(records=(), missing_trailing_newline=False)
    try:
        payload = path.read_bytes()
    except OSError as exc:
        raise StorageError(f"cannot read journal {path.name}: {exc}") from exc
    if not payload:
        return JournalReadResult(records=(), missing_trailing_newline=False)

    has_newline = payload.endswith(b"\n")
    chunks = payload.split(b"\n")
    if has_newline:
        chunks = chunks[:-1]

    records: list[TRecord] = []
    previous_hash: str | None = None

    for index, chunk in enumerate(chunks, start=1):
        is_last_unterminated = not has_newline and index == len(chunks)
        try:
            if not chunk:
                raise ValueError("blank JSONL record")
            raw_value = strict_json_loads(chunk)
            if not isinstance(raw_value, dict):
                raise SchemaError("journal record root must be an object")
            raw = dict(raw_value)
            _validate_hash_record(
                raw,
                seq_name=seq_name,
                expected_seq=index,
                previous_hash=previous_hash,
            )
            record = parser(raw)
            record_session = getattr(record, "session_id", None)
            if record_session != session_id:
                raise JournalCorruptionError(
                    f"session_id mismatch at {seq_name}={index}"
                )
        except Exception as exc:
            if is_last_unterminated:
                raise TruncatedLastRecordError(
                    f"invalid unterminated final record in {path.name}: {exc}"
                ) from exc
            if isinstance(exc, JournalCorruptionError):
                raise
            raise JournalCorruptionError(
                f"invalid record {index} in {path.name}: {exc}"
            ) from exc

        records.append(record)
        previous_hash = raw["record_hash"]

    return JournalReadResult(
        records=tuple(records),
        missing_trailing_newline=not has_newline,
    )


class ProjectSafeSessionStore:
    """Lease-bound durable store for one logical project-safe session."""

    def __init__(self, lease: ProjectLease, session_id: str) -> None:
        if not lease.held or lease.state_root is None or lease.lock_path is None:
            raise LeaseRequiredError("a live ProjectLease is required")
        checked_session = uuid4_value(session_id, "session_id")
        assert checked_session is not None
        self.lease = lease
        self.session_id = checked_session
        self.project_fingerprint = project_fingerprint(lease.project_root)
        self.project_dir = lease.lock_path.parent
        self.session_dir = self.project_dir / "sessions" / self.session_id
        self.state_path = self.session_dir / "state.json"
        self.actions_path = self.session_dir / "actions.jsonl"
        self.events_path = self.session_dir / "events.jsonl"
        self.actions_dir = self.session_dir / "actions"
        self.checkpoints_dir = self.session_dir / "checkpoints"
        self._lock = threading.RLock()

    def _require_lease(self) -> None:
        if not self.lease.held:
            raise LeaseRequiredError("project lease is no longer held")

    def read_state(self) -> StateSnapshot | None:
        if not self.state_path.exists():
            return None
        try:
            raw = strict_json_loads(self.state_path.read_bytes())
            state = StateSnapshot.from_dict(raw)
        except Exception as exc:
            raise StorageError(f"invalid state.json: {exc}") from exc
        if state.session_id != self.session_id:
            raise StorageError("state.json session_id does not match store")
        if state.project_fingerprint != self.project_fingerprint:
            raise StorageError("state.json project_fingerprint does not match leased project")
        return state

    def write_state(
        self,
        state: StateSnapshot,
        *,
        expected_current_revision: int | None = None,
    ) -> None:
        with self._lock:
            self._require_lease()
            if state.session_id != self.session_id:
                raise StorageError("state session_id does not match store")
            if state.project_fingerprint != self.project_fingerprint:
                raise StorageError("state project_fingerprint does not match leased project")

            current = self.read_state()
            current_revision = 0 if current is None else current.state_revision
            if expected_current_revision is not None and expected_current_revision != current_revision:
                raise StateRevisionError(
                    f"state revision changed: expected {expected_current_revision}, found {current_revision}"
                )
            if state.state_revision != current_revision + 1:
                raise StateRevisionError(
                    f"next state_revision must be {current_revision + 1}, got {state.state_revision}"
                )

            _durable_replace(self.state_path, canonical_json_bytes(state.to_dict()) + b"\n")

    def read_actions(self) -> JournalReadResult[JournalRecord]:
        result = _read_journal(
            self.actions_path,
            parser=JournalRecord.from_dict,
            seq_name="journal_seq",
            session_id=self.session_id,
        )
        try:
            validate_action_history(result.records)
        except ActionContractError as exc:
            raise JournalCorruptionError(f"action contract violation: {exc}") from exc
        return result

    def read_events(self) -> JournalReadResult[SessionEventRecord]:
        return _read_journal(
            self.events_path,
            parser=SessionEventRecord.from_dict,
            seq_name="event_seq",
            session_id=self.session_id,
        )

    def _repair_trailing_newline(self, path: Path) -> None:
        _durable_append(path, b"\n")

    def append_action(self, draft: Mapping[str, Any]) -> JournalRecord:
        with self._lock:
            self._require_lease()
            exact_keys(draft, _ACTION_DRAFT_KEYS, "action draft")
            result = self.read_actions()
            if result.missing_trailing_newline:
                self._repair_trailing_newline(self.actions_path)

            records = result.records
            supplied_action = sha256_value(draft["action_id"], "action draft.action_id")
            assert supplied_action is not None
            expected_action = action_identity_from_draft(
                session_id=self.session_id,
                draft=draft,
            )
            if supplied_action != expected_action:
                raise StorageError(
                    f"action_id does not match deterministic action-v1 identity: "
                    f"expected {expected_action}, got {supplied_action}"
                )

            same_action = [record for record in records if record.action_id == supplied_action]
            if same_action:
                action_seq = same_action[0].action_seq
                transition_seq = same_action[-1].transition_seq + 1
            else:
                action_seq = max((record.action_seq for record in records), default=0) + 1
                transition_seq = 1

            attempt_seq = draft["attempt_seq"]
            expected_attempt = compute_attempt_id(supplied_action, attempt_seq)
            if draft["attempt_id"] != expected_attempt:
                raise StorageError(
                    f"attempt_id does not match attempt-v1 identity for attempt {attempt_seq}"
                )

            raw = dict(draft)
            raw.update(
                {
                    "schema_version": SCHEMA_VERSION,
                    "journal_seq": len(records) + 1,
                    "action_seq": action_seq,
                    "transition_seq": transition_seq,
                    "session_id": self.session_id,
                    "previous_record_hash": records[-1].record_hash if records else None,
                }
            )
            raw["record_hash"] = _record_hash(raw)
            record = JournalRecord.from_dict(raw)

            try:
                validate_action_record(record, same_action)
            except ActionContractError as exc:
                raise StorageError(f"invalid action transition: {exc}") from exc

            _durable_append(
                self.actions_path,
                canonical_json_bytes(record.to_dict()) + b"\n",
            )
            return record

    def append_event(self, draft: Mapping[str, Any]) -> SessionEventRecord:
        with self._lock:
            self._require_lease()
            exact_keys(draft, _EVENT_DRAFT_KEYS, "event draft")
            result = self.read_events()
            if result.missing_trailing_newline:
                self._repair_trailing_newline(self.events_path)

            records = result.records
            raw = dict(draft)
            raw.update(
                {
                    "schema_version": SCHEMA_VERSION,
                    "event_seq": len(records) + 1,
                    "session_id": self.session_id,
                    "previous_record_hash": records[-1].record_hash if records else None,
                }
            )
            raw["record_hash"] = _record_hash(raw)
            record = SessionEventRecord.from_dict(raw)
            _durable_append(
                self.events_path,
                canonical_json_bytes(record.to_dict()) + b"\n",
            )
            return record

    def write_action_payload(self, action_id: str, name: str, data: bytes) -> tuple[str, str]:
        """Persist immutable input/result bytes and return (relative_ref, sha256)."""
        with self._lock:
            self._require_lease()
            checked_action = sha256_value(action_id, "action_id")
            assert checked_action is not None
            if name not in {"input.json", "result.json"}:
                raise StorageError("payload name must be input.json or result.json")
            if not isinstance(data, bytes):
                raise TypeError("payload data must be bytes")

            digest = hashlib.sha256(data).hexdigest()
            relative = f"actions/{checked_action}/{name}"
            path = self.session_dir / relative_path_value(relative, "payload_ref")
            if path.exists():
                existing = path.read_bytes()
                if hashlib.sha256(existing).hexdigest() != digest or existing != data:
                    raise PayloadIntegrityError(
                        f"immutable payload already exists with different bytes: {relative}"
                    )
                return relative, digest

            _durable_replace(path, data)
            return relative, digest

    def read_action_payload(self, relative_ref: str, expected_sha256: str) -> bytes:
        relative = relative_path_value(relative_ref, "payload_ref")
        expected = sha256_value(expected_sha256, "payload_sha256")
        assert expected is not None
        if not relative.startswith("actions/"):
            raise PayloadIntegrityError("payload_ref is outside actions/")
        path = self.session_dir / relative
        try:
            data = path.read_bytes()
        except OSError as exc:
            raise PayloadIntegrityError(f"cannot read payload {relative}: {exc}") from exc
        actual = hashlib.sha256(data).hexdigest()
        if actual != expected:
            raise PayloadIntegrityError(
                f"payload digest mismatch for {relative}: expected {expected}, got {actual}"
            )
        return data


__all__ = [
    "JournalCorruptionError", "JournalReadResult", "LeaseRequiredError",
    "PayloadIntegrityError", "ProjectSafeSessionStore", "StateRevisionError",
    "StorageError", "TruncatedLastRecordError",
]
