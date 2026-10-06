"""Project-level registry for project-safe logical sessions."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .canonical import canonical_json_bytes, strict_json_loads
from .durable_io import durable_replace
from .lease import ProjectLease
from .paths import project_fingerprint
from .schema_types import SCHEMA_VERSION
from .schema_utils import (
    SchemaError,
    exact_keys,
    integer_value,
    object_value,
    sha256_value,
    to_data,
    utc_value,
    uuid4_value,
)


class RegistryError(RuntimeError):
    """Project registry is invalid or cannot be updated safely."""


class RegistryRevisionError(RegistryError):
    """registry_revision did not match/advance as required."""


class ActiveSessionConflictError(RegistryError):
    """Another session is already the unique active session for this project."""


@dataclass(frozen=True)
class ProjectRegistry:
    schema_version: int
    registry_revision: int
    project_fingerprint: str
    active_session_id: str | None
    session_ids: tuple[str, ...]
    created_at: str
    updated_at: str

    @classmethod
    def from_dict(cls, value: Any) -> "ProjectRegistry":
        obj = object_value(value, "registry")
        exact_keys(
            obj,
            {
                "schema_version", "registry_revision", "project_fingerprint",
                "active_session_id", "session_ids", "created_at", "updated_at",
            },
            "registry",
        )
        if integer_value(obj["schema_version"], "registry.schema_version", minimum=1) != SCHEMA_VERSION:
            raise SchemaError("unsupported registry.schema_version")
        raw_ids = obj["session_ids"]
        if not isinstance(raw_ids, list):
            raise SchemaError("registry.session_ids must be an array")
        session_ids = tuple(
            uuid4_value(item, "registry.session_ids[]") or "" for item in raw_ids
        )
        if len(session_ids) != len(set(session_ids)):
            raise SchemaError("registry.session_ids contains duplicates")
        active = uuid4_value(
            obj["active_session_id"],
            "registry.active_session_id",
            optional=True,
        )
        if active is not None and active not in session_ids:
            raise SchemaError("registry.active_session_id must be present in session_ids")
        return cls(
            schema_version=SCHEMA_VERSION,
            registry_revision=integer_value(
                obj["registry_revision"], "registry.registry_revision", minimum=1
            ),
            project_fingerprint=sha256_value(
                obj["project_fingerprint"], "registry.project_fingerprint"
            ) or "",
            active_session_id=active,
            session_ids=session_ids,
            created_at=utc_value(obj["created_at"], "registry.created_at"),
            updated_at=utc_value(obj["updated_at"], "registry.updated_at"),
        )

    def to_dict(self) -> dict[str, Any]:
        return to_data(self)


class ProjectRegistryStore:
    """Lease-bound atomic registry for all logical sessions of one project."""

    def __init__(self, lease: ProjectLease) -> None:
        if not lease.held or lease.lock_path is None:
            raise RegistryError("a live ProjectLease is required")
        self.lease = lease
        self.project_fingerprint = project_fingerprint(lease.project_root)
        self.path = lease.lock_path.parent / "registry.json"

    def _require_lease(self) -> None:
        if not self.lease.held:
            raise RegistryError("project lease is no longer held")

    def read(self) -> ProjectRegistry | None:
        self._require_lease()
        if not self.path.exists():
            return None
        try:
            raw = strict_json_loads(self.path.read_bytes())
            registry = ProjectRegistry.from_dict(raw)
        except Exception as exc:
            raise RegistryError(f"invalid registry.json: {exc}") from exc
        if registry.project_fingerprint != self.project_fingerprint:
            raise RegistryError("registry project_fingerprint does not match leased project")
        return registry

    def write(
        self,
        registry: ProjectRegistry,
        *,
        expected_current_revision: int | None = None,
    ) -> None:
        self._require_lease()
        if registry.project_fingerprint != self.project_fingerprint:
            raise RegistryError("registry project_fingerprint does not match leased project")
        current = self.read()
        current_revision = 0 if current is None else current.registry_revision
        if expected_current_revision is not None and expected_current_revision != current_revision:
            raise RegistryRevisionError(
                f"registry revision changed: expected {expected_current_revision}, "
                f"found {current_revision}"
            )
        if registry.registry_revision != current_revision + 1:
            raise RegistryRevisionError(
                f"next registry_revision must be {current_revision + 1}, "
                f"got {registry.registry_revision}"
            )
        durable_replace(self.path, canonical_json_bytes(registry.to_dict()) + b"\n")

    def register_session(self, session_id: str, *, at: str) -> ProjectRegistry:
        self._require_lease()
        checked = uuid4_value(session_id, "session_id")
        timestamp = utc_value(at, "at")
        assert checked is not None
        current = self.read()
        if current is not None and checked in current.session_ids:
            return current

        if current is None:
            next_registry = ProjectRegistry.from_dict(
                {
                    "schema_version": SCHEMA_VERSION,
                    "registry_revision": 1,
                    "project_fingerprint": self.project_fingerprint,
                    "active_session_id": None,
                    "session_ids": [checked],
                    "created_at": timestamp,
                    "updated_at": timestamp,
                }
            )
            self.write(next_registry, expected_current_revision=0)
            return next_registry

        next_registry = ProjectRegistry.from_dict(
            {
                **current.to_dict(),
                "registry_revision": current.registry_revision + 1,
                "session_ids": [*current.session_ids, checked],
                "updated_at": timestamp,
            }
        )
        self.write(next_registry, expected_current_revision=current.registry_revision)
        return next_registry

    def activate_session(self, session_id: str, *, at: str) -> ProjectRegistry:
        self._require_lease()
        checked = uuid4_value(session_id, "session_id")
        timestamp = utc_value(at, "at")
        assert checked is not None
        current = self.read()
        if current is None or checked not in current.session_ids:
            raise RegistryError("session must be registered before activation")
        if current.active_session_id == checked:
            return current
        if current.active_session_id is not None:
            raise ActiveSessionConflictError(
                f"project already has active session {current.active_session_id}"
            )
        next_registry = ProjectRegistry.from_dict(
            {
                **current.to_dict(),
                "registry_revision": current.registry_revision + 1,
                "active_session_id": checked,
                "updated_at": timestamp,
            }
        )
        self.write(next_registry, expected_current_revision=current.registry_revision)
        return next_registry

    def clear_active_session(self, session_id: str, *, at: str) -> ProjectRegistry:
        self._require_lease()
        checked = uuid4_value(session_id, "session_id")
        timestamp = utc_value(at, "at")
        assert checked is not None
        current = self.read()
        if current is None:
            raise RegistryError("registry does not exist")
        if current.active_session_id is None:
            return current
        if current.active_session_id != checked:
            raise ActiveSessionConflictError(
                f"cannot clear active session owned by {current.active_session_id}"
            )
        next_registry = ProjectRegistry.from_dict(
            {
                **current.to_dict(),
                "registry_revision": current.registry_revision + 1,
                "active_session_id": None,
                "updated_at": timestamp,
            }
        )
        self.write(next_registry, expected_current_revision=current.registry_revision)
        return next_registry


__all__ = [
    "ActiveSessionConflictError", "ProjectRegistry", "ProjectRegistryStore",
    "RegistryError", "RegistryRevisionError",
]
