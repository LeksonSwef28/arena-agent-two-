"""Strict validation helpers shared by project-safe v1 schemas."""
from __future__ import annotations

import re
import uuid
from dataclasses import fields, is_dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Mapping, TypeVar

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_GIT_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$")

TEnum = TypeVar("TEnum", bound=Enum)


class SchemaError(ValueError):
    """Persisted/project-safe data does not match the frozen v1 contract."""


def object_value(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise SchemaError(f"{label} must be an object")
    return dict(value)


def exact_keys(obj: Mapping[str, Any], expected: set[str], label: str) -> None:
    keys = set(obj)
    if keys != expected:
        missing = sorted(expected - keys)
        extra = sorted(keys - expected)
        raise SchemaError(f"{label} keys mismatch; missing={missing}, extra={extra}")


def string_value(value: Any, label: str, *, empty: bool = False) -> str:
    if not isinstance(value, str) or (not empty and not value):
        kind = "string" if empty else "non-empty string"
        raise SchemaError(f"{label} must be a {kind}")
    return value


def optional_string(value: Any, label: str) -> str | None:
    if value is None:
        return None
    return string_value(value, label)


def relative_path_value(value: Any, label: str) -> str:
    text = string_value(value, label)
    normalized = text.replace("\\", "/")
    parts = normalized.split("/")
    if normalized.startswith("/") or not parts or any(part in {"", ".", ".."} for part in parts):
        raise SchemaError(f"{label} must be a safe normalized relative path")
    if ":" in parts[0]:
        raise SchemaError(f"{label} must not contain a drive or URI scheme")
    return normalized


def integer_value(value: Any, label: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise SchemaError(f"{label} must be an integer >= {minimum}")
    return value


def boolean_value(value: Any, label: str) -> bool:
    if not isinstance(value, bool):
        raise SchemaError(f"{label} must be a boolean")
    return value


def enum_value(enum_type: type[TEnum], value: Any, label: str) -> TEnum:
    if not isinstance(value, str):
        raise SchemaError(f"{label} must be a string enum")
    try:
        return enum_type(value)
    except ValueError as exc:
        raise SchemaError(f"unsupported {label}: {value!r}") from exc


def sha256_value(value: Any, label: str, *, optional: bool = False) -> str | None:
    if value is None and optional:
        return None
    text = string_value(value, label)
    if _SHA256_RE.fullmatch(text) is None:
        raise SchemaError(f"{label} must be 64 lowercase hex characters")
    return text


def git_sha_value(value: Any, label: str, *, optional: bool = False) -> str | None:
    if value is None and optional:
        return None
    text = string_value(value, label)
    if _GIT_SHA_RE.fullmatch(text) is None:
        raise SchemaError(f"{label} must be 40 lowercase hex characters")
    return text


def uuid4_value(value: Any, label: str, *, optional: bool = False) -> str | None:
    if value is None and optional:
        return None
    text = string_value(value, label)
    try:
        parsed = uuid.UUID(text)
    except (ValueError, AttributeError) as exc:
        raise SchemaError(f"{label} must be a UUID v4") from exc
    if parsed.version != 4 or str(parsed) != text:
        raise SchemaError(f"{label} must be canonical lowercase UUID v4")
    return text


def utc_value(value: Any, label: str) -> str:
    text = string_value(value, label)
    if _UTC_RE.fullmatch(text) is None:
        raise SchemaError(f"{label} must be UTC ISO-8601 ending in Z")
    try:
        datetime.fromisoformat(text[:-1] + "+00:00")
    except ValueError as exc:
        raise SchemaError(f"{label} is not a valid UTC timestamp") from exc
    return text


def to_data(value: Any) -> Any:
    """Convert validated dataclasses/enums to JSON-ready primitive data."""
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value) and not isinstance(value, type):
        return {field.name: to_data(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, val in value.items():
            rendered_key = key.value if isinstance(key, Enum) else str(key)
            out[rendered_key] = to_data(val)
        return out
    if isinstance(value, (list, tuple)):
        return [to_data(item) for item in value]
    return value


__all__ = [
    "SchemaError", "boolean_value", "enum_value", "exact_keys", "git_sha_value",
    "integer_value", "object_value", "optional_string", "relative_path_value",
    "sha256_value", "string_value", "to_data", "utc_value", "uuid4_value",
]
