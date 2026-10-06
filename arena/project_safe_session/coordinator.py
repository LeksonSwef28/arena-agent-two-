"""Deterministic logical-session/flow coordinator for project-safe P1-B1.

No tool side effects are executed here. This layer only mutates durable
session/registry/event state under an already-held ProjectLease.
"""
from __future__ import annotations

import os
import unicodedata
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .canonical import canonical_sha256
from .checkpoint_contract import build_checkpoint_manifest
from .lease import ProjectLease
from .models import StateSnapshot
from .paths import project_fingerprint
from .registry import ProjectRegistryStore, RegistryError
from .schema_types import BrowserRole, LifecycleStatus, RequestedMode
from .storage import ProjectSafeSessionStore
from .workspace import (
    WorkspaceEvidence,
    compute_workspace_digest_v1,
    workspace_is_clean_v1,
)


class SessionCoordinatorError(RuntimeError):
    """Requested lifecycle transition is invalid or unsafe."""


class SessionAdmissionError(SessionCoordinatorError):
    """Workspace/session conditions do not permit the requested transition."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


def _now() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z")
    )


def normalize_goal_v1(goal: str) -> str:
    if not isinstance(goal, str):
        raise SessionCoordinatorError("goal must be a string")
    normalized = " ".join(unicodedata.normalize("NFC", goal).split())
    if not normalized:
        raise SessionCoordinatorError("goal must not be empty")
    return normalized


def session_fingerprint_v1(project_fingerprint_value: str, initial_goal: str) -> str:
    return canonical_sha256(
        {
            "version": "session-v1",
            "project_fingerprint": project_fingerprint_value,
            "normalized_initial_goal": normalize_goal_v1(initial_goal),
        }
    )


def _roles(values: Iterable[BrowserRole | str]) -> tuple[BrowserRole, ...]:
    result: list[BrowserRole] = []
    for value in values:
        try:
            role = value if isinstance(value, BrowserRole) else BrowserRole(str(value))
        except ValueError as exc:
            raise SessionCoordinatorError(f"unsupported browser role: {value!r}") from exc
        if role in result:
            raise SessionCoordinatorError(f"duplicate browser role: {role.value}")
        result.append(role)
    return tuple(result)


class ProjectSafeSessionCoordinator:
    """Own project-safe lifecycle changes under the unique project lease."""

    def __init__(self, lease: ProjectLease) -> None:
        if not lease.held:
            raise SessionCoordinatorError("a live ProjectLease is required")
        self.lease = lease
        self.project_root = Path(lease.project_root).expanduser().resolve(strict=True)
        self.project_fingerprint = project_fingerprint(self.project_root)
        self.registry = ProjectRegistryStore(lease)

    def _store(self, session_id: str) -> ProjectSafeSessionStore:
        return ProjectSafeSessionStore(self.lease, session_id)

    def read_session(self, session_id: str) -> StateSnapshot:
        state = self._store(session_id).read_state()
        if state is None:
            raise SessionCoordinatorError(f"session state not found: {session_id}")
        return state

    def _write_event_state(
        self,
        store: ProjectSafeSessionStore,
        state: StateSnapshot,
        raw: dict[str, Any],
        events: list[dict[str, Any]],
    ) -> StateSnapshot:
        if not events:
            return state
        last_event_seq = state.execution.last_event_seq
        last_time = state.updated_at
        for event in events:
            record = store.append_event(event)
            last_event_seq = record.event_seq
            last_time = record.recorded_at

        raw["state_revision"] = state.state_revision + 1
        raw["updated_at"] = last_time
        raw["execution"]["last_event_seq"] = last_event_seq
        next_state = StateSnapshot.from_dict(raw)
        store.write_state(
            next_state,
            expected_current_revision=state.state_revision,
        )
        return next_state

    def create_session(
        self,
        *,
        goal: str,
        requested_mode: RequestedMode | str,
        required_roles: Iterable[BrowserRole | str] = (),
        session_id: str | None = None,
        parent_session_id: str | None = None,
        at: str | None = None,
    ) -> StateSnapshot:
        normalized_goal = normalize_goal_v1(goal)
        try:
            mode = (
                requested_mode
                if isinstance(requested_mode, RequestedMode)
                else RequestedMode(str(requested_mode))
            )
        except ValueError as exc:
            raise SessionCoordinatorError(
                f"unsupported requested_mode: {requested_mode!r}"
            ) from exc
        roles = _roles(required_roles)
        session = session_id or str(uuid.uuid4())
        timestamp = at or _now()

        manifest, digest = compute_workspace_digest_v1(self.project_root)
        clean = workspace_is_clean_v1(self.project_root)
        checkpoint_id = str(uuid.uuid4())

        store = self._store(session)
        baseline_checkpoint = build_checkpoint_manifest(
            checkpoint_id=checkpoint_id,
            session_id=session,
            action_id=None,
            kind="SESSION_BASELINE",
            created_at=timestamp,
            workspace_digest=digest,
            head_sha=manifest.head_sha,
            resources=[],
        )
        store.write_checkpoint(baseline_checkpoint, {})

        created_event = store.append_event(
            {
                "event_type": "SESSION_CREATED",
                "recorded_at": timestamp,
                "data": {
                    "goal_revision": 1,
                    "requested_mode": mode.value,
                    "workspace_digest": digest,
                    "workspace_clean": clean,
                },
            }
        )

        raw = {
            "schema_version": 1,
            "state_revision": 1,
            "session_id": session,
            "session_fingerprint": session_fingerprint_v1(
                self.project_fingerprint,
                normalized_goal,
            ),
            "parent_session_id": parent_session_id,
            "project_fingerprint": self.project_fingerprint,
            "project": {
                "canonical_root": os.fspath(self.project_root),
                "requested_mode": mode.value,
            },
            "goal": {
                "initial": normalized_goal,
                "current": normalized_goal,
                "revision": 1,
                "last_changed_at": timestamp,
            },
            "lifecycle": {
                "status": "CREATED",
                "phase": "IDLE",
                "reason": None,
                "changed_at": timestamp,
            },
            "workspace": {
                "kind": "git_worktree",
                "session_baseline": {
                    "checkpoint_id": checkpoint_id,
                    "head_sha": manifest.head_sha,
                    "workspace_digest": digest,
                    "clean": clean,
                    "captured_at": timestamp,
                },
                "workspace_digest_last_verified": digest,
                "last_verified_checkpoint_id": checkpoint_id,
                "last_verified_head_sha": manifest.head_sha,
                "last_verified_at": timestamp,
            },
            "browser": {
                "required_roles": [role.value for role in roles],
                "bindings": {role.value: None for role in roles},
            },
            "active_flow": None,
            "execution": {
                "pending_action_id": None,
                "last_terminal_action_id": None,
                "last_action_seq": 0,
                "last_event_seq": created_event.event_seq,
            },
            "recovery": None,
            "limits": {
                "max_actions": 100,
                "max_messages_per_role": 50,
                "max_active_minutes": 120,
            },
            "created_at": timestamp,
            "updated_at": timestamp,
        }
        state = StateSnapshot.from_dict(raw)
        store.write_state(state, expected_current_revision=0)
        self.registry.register_session(session, at=timestamp)
        return state

    def activate_session(self, session_id: str, *, at: str | None = None) -> StateSnapshot:
        timestamp = at or _now()
        store = self._store(session_id)
        state = self.read_session(session_id)
        if state.lifecycle.status is LifecycleStatus.ACTIVE:
            registry = self.registry.read()
            if registry is not None and registry.active_session_id == session_id:
                return state
            raise SessionCoordinatorError("state says ACTIVE but registry disagrees")
        if state.lifecycle.status not in {LifecycleStatus.CREATED, LifecycleStatus.PAUSED}:
            raise SessionCoordinatorError(
                f"cannot activate session from {state.lifecycle.status.value}"
            )

        self.registry.activate_session(session_id, at=timestamp)
        raw = state.to_dict()
        old = state.lifecycle.status.value
        raw["lifecycle"] = {
            **raw["lifecycle"],
            "status": "ACTIVE",
            "reason": None,
            "changed_at": timestamp,
        }
        return self._write_event_state(
            store,
            state,
            raw,
            [
                {
                    "event_type": "STATUS_CHANGED",
                    "recorded_at": timestamp,
                    "data": {"from": old, "to": "ACTIVE", "reason": None},
                }
            ],
        )

    def pause_session(self, session_id: str, *, at: str | None = None) -> StateSnapshot:
        timestamp = at or _now()
        store = self._store(session_id)
        state = self.read_session(session_id)
        if state.lifecycle.status is LifecycleStatus.PAUSED:
            return state
        if state.lifecycle.status is not LifecycleStatus.ACTIVE:
            raise SessionCoordinatorError(
                f"cannot pause session from {state.lifecycle.status.value}"
            )
        if state.execution.pending_action_id is not None:
            raise SessionCoordinatorError("cannot pause while an action is pending")

        raw = state.to_dict()
        raw["lifecycle"] = {
            **raw["lifecycle"],
            "status": "PAUSED",
            "reason": None,
            "changed_at": timestamp,
        }
        next_state = self._write_event_state(
            store,
            state,
            raw,
            [
                {
                    "event_type": "STATUS_CHANGED",
                    "recorded_at": timestamp,
                    "data": {"from": "ACTIVE", "to": "PAUSED", "reason": None},
                }
            ],
        )
        self.registry.clear_active_session(session_id, at=timestamp)
        return next_state

    def start_flow(self, session_id: str, *, at: str | None = None) -> StateSnapshot:
        timestamp = at or _now()
        store = self._store(session_id)
        state = self.read_session(session_id)
        registry = self.registry.read()
        if registry is None or registry.active_session_id != session_id:
            raise SessionCoordinatorError("session must own the active project slot")
        if state.active_flow is not None:
            return state
        if state.execution.pending_action_id is not None:
            raise SessionCoordinatorError("cannot start flow while an action is pending")
        if state.lifecycle.status not in {LifecycleStatus.ACTIVE, LifecycleStatus.WAITING}:
            raise SessionCoordinatorError(
                f"cannot start flow from {state.lifecycle.status.value}"
            )
        if (
            state.lifecycle.status is LifecycleStatus.WAITING
            and state.lifecycle.reason is not None
            and state.lifecycle.reason.value != "WORKSPACE_DIRTY"
        ):
            raise SessionCoordinatorError(
                f"cannot auto-clear waiting reason {state.lifecycle.reason.value}"
            )

        manifest, digest = compute_workspace_digest_v1(self.project_root)
        clean = workspace_is_clean_v1(self.project_root)
        if state.project.requested_mode is RequestedMode.WRITE and not clean:
            if (
                state.lifecycle.status is LifecycleStatus.WAITING
                and state.lifecycle.reason is not None
                and state.lifecycle.reason.value == "WORKSPACE_DIRTY"
            ):
                return state
            raw = state.to_dict()
            raw["lifecycle"] = {
                "status": "WAITING",
                "phase": "IDLE",
                "reason": "WORKSPACE_DIRTY",
                "changed_at": timestamp,
            }
            return self._write_event_state(
                store,
                state,
                raw,
                [
                    {
                        "event_type": "STATUS_CHANGED",
                        "recorded_at": timestamp,
                        "data": {
                            "from": state.lifecycle.status.value,
                            "to": "WAITING",
                            "reason": "WORKSPACE_DIRTY",
                        },
                    }
                ],
            )

        flow_id = str(uuid.uuid4())
        events: list[dict[str, Any]] = []
        if state.lifecycle.status is LifecycleStatus.WAITING:
            events.append(
                {
                    "event_type": "STATUS_CHANGED",
                    "recorded_at": timestamp,
                    "data": {
                        "from": "WAITING",
                        "to": "ACTIVE",
                        "reason": "WORKSPACE_DIRTY_RESOLVED",
                    },
                }
            )
        events.append(
            {
                "event_type": "FLOW_CREATED",
                "recorded_at": timestamp,
                "data": {
                    "flow_id": flow_id,
                    "goal_revision": state.goal.revision,
                    "workspace_digest_baseline": digest,
                    "workspace_digest_expected_current": digest,
                    "workspace_clean": clean,
                    "head_sha": manifest.head_sha,
                },
            }
        )

        raw = state.to_dict()
        raw["lifecycle"] = {
            "status": "ACTIVE",
            "phase": "PLANNING",
            "reason": None,
            "changed_at": timestamp,
        }
        raw["active_flow"] = {
            "flow_id": flow_id,
            "goal_revision": state.goal.revision,
            "workspace_digest_baseline": digest,
            "workspace_digest_expected_current": digest,
            "created_at": timestamp,
        }
        return self._write_event_state(store, state, raw, events)

    def close_flow(
        self,
        session_id: str,
        *,
        reason: str = "COMPLETED",
        at: str | None = None,
    ) -> StateSnapshot:
        timestamp = at or _now()
        store = self._store(session_id)
        state = self.read_session(session_id)
        if state.active_flow is None:
            return state
        if state.execution.pending_action_id is not None:
            raise SessionCoordinatorError("cannot close flow while an action is pending")
        flow_id = state.active_flow.flow_id

        raw = state.to_dict()
        raw["active_flow"] = None
        raw["lifecycle"] = {
            **raw["lifecycle"],
            "phase": "IDLE",
            "changed_at": timestamp,
        }
        return self._write_event_state(
            store,
            state,
            raw,
            [
                {
                    "event_type": "FLOW_CLOSED",
                    "recorded_at": timestamp,
                    "data": {"flow_id": flow_id, "reason": str(reason)},
                }
            ],
        )

    def refine_goal(
        self,
        session_id: str,
        *,
        new_goal: str,
        reason: str = "user_refined",
        at: str | None = None,
    ) -> StateSnapshot:
        timestamp = at or _now()
        normalized = normalize_goal_v1(new_goal)
        store = self._store(session_id)
        state = self.read_session(session_id)
        if state.lifecycle.status not in {
            LifecycleStatus.CREATED,
            LifecycleStatus.ACTIVE,
            LifecycleStatus.PAUSED,
        }:
            raise SessionCoordinatorError(
                f"cannot refine goal from {state.lifecycle.status.value}"
            )
        if state.execution.pending_action_id is not None:
            raise SessionCoordinatorError("cannot refine goal while an action is pending")
        if normalized == state.goal.current:
            return state

        events: list[dict[str, Any]] = []
        if state.active_flow is not None:
            events.append(
                {
                    "event_type": "FLOW_CLOSED",
                    "recorded_at": timestamp,
                    "data": {
                        "flow_id": state.active_flow.flow_id,
                        "reason": "GOAL_REFINED",
                    },
                }
            )
        next_revision = state.goal.revision + 1
        events.append(
            {
                "event_type": "GOAL_REFINED",
                "recorded_at": timestamp,
                "data": {
                    "from": state.goal.current,
                    "to": normalized,
                    "revision": next_revision,
                    "reason": str(reason),
                },
            }
        )

        raw = state.to_dict()
        raw["goal"] = {
            **raw["goal"],
            "current": normalized,
            "revision": next_revision,
            "last_changed_at": timestamp,
        }
        raw["active_flow"] = None
        raw["lifecycle"] = {
            **raw["lifecycle"],
            "phase": "IDLE",
            "changed_at": timestamp,
        }
        return self._write_event_state(store, state, raw, events)


__all__ = [
    "ProjectSafeSessionCoordinator", "SessionAdmissionError",
    "SessionCoordinatorError", "normalize_goal_v1", "session_fingerprint_v1",
]
