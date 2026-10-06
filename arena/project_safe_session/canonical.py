"""Canonical JSON primitives for project-safe durable state."""
from __future__ import annotations

import hashlib
import json
from typing import Any


def canonical_json_bytes(value: Any) -> bytes:
    """Return deterministic UTF-8 JSON bytes for hashing/persistence contracts."""
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def strict_json_loads(raw: str | bytes | bytearray) -> Any:
    """Parse JSON while rejecting duplicate keys and non-standard constants."""

    def object_pairs(pairs):
        out = {}
        for key, value in pairs:
            if key in out:
                raise ValueError(f"duplicate JSON key: {key!r}")
            out[key] = value
        return out

    def reject_constant(value: str):
        raise ValueError(f"non-finite JSON constant is not allowed: {value}")

    return json.loads(raw, object_pairs_hook=object_pairs, parse_constant=reject_constant)


__all__ = ["canonical_json_bytes", "canonical_sha256", "strict_json_loads"]
