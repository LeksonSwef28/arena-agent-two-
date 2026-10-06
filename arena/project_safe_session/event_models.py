"""Strict events.jsonl record model for project-safe v1."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .schema_types import EventType, SCHEMA_VERSION
from .schema_utils import (
    SchemaError,
    enum_value,
    exact_keys,
    integer_value,
    object_value,
    sha256_value,
    to_data,
    utc_value,
    uuid4_value,
)


@dataclass(frozen=True)
class SessionEventRecord:
    schema_version: int
    event_seq: int
    session_id: str
    event_type: EventType
    recorded_at: str
    data: dict[str, Any]
    previous_record_hash: str | None
    record_hash: str

    @classmethod
    def from_dict(cls, value: Any) -> "SessionEventRecord":
        obj = object_value(value, "event_record")
        exact_keys(
            obj,
            {
                "schema_version", "event_seq", "session_id", "event_type",
                "recorded_at", "data", "previous_record_hash", "record_hash",
            },
            "event_record",
        )
        if integer_value(obj["schema_version"], "event_record.schema_version", minimum=1) != SCHEMA_VERSION:
            raise SchemaError("unsupported event_record.schema_version")
        event_seq = integer_value(obj["event_seq"], "event_record.event_seq", minimum=1)
        previous_hash = sha256_value(
            obj["previous_record_hash"], "event_record.previous_record_hash", optional=True
        )
        if event_seq == 1 and previous_hash is not None:
            raise SchemaError("event_seq=1 requires previous_record_hash=null")
        if event_seq > 1 and previous_hash is None:
            raise SchemaError("event_seq>1 requires previous_record_hash")
        return cls(
            schema_version=SCHEMA_VERSION,
            event_seq=event_seq,
            session_id=uuid4_value(obj["session_id"], "event_record.session_id") or "",
            event_type=enum_value(EventType, obj["event_type"], "event_record.event_type"),
            recorded_at=utc_value(obj["recorded_at"], "event_record.recorded_at"),
            data=object_value(obj["data"], "event_record.data"),
            previous_record_hash=previous_hash,
            record_hash=sha256_value(obj["record_hash"], "event_record.record_hash") or "",
        )

    def to_dict(self) -> dict[str, Any]:
        return to_data(self)


__all__ = ["SessionEventRecord"]
