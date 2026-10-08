"""Deterministic action identity and transition contract for project-safe v1."""
from __future__ import annotations

import math
from collections.abc import Iterable
from typing import Any, Mapping

from .action_models import JournalRecord
from .canonical import canonical_sha256
from .schema_types import ActionState, EffectStatus
from .schema_utils import (
    integer_value,
    sha256_value,
    string_value,
    uuid4_value,
)


class ActionContractError(ValueError):
    """An action record violates deterministic identity or lifecycle semantics."""


_ALLOWED_SAME_ATTEMPT: dict[ActionState, frozenset[ActionState]] = {
    ActionState.PREPARED: frozenset(
        {ActionState.EXECUTING, ActionState.FAILED, ActionState.ABANDONED}
    ),
    ActionState.EXECUTING: frozenset(
        {ActionState.VERIFYING, ActionState.FAILED, ActionState.INTERRUPTED}
    ),
    ActionState.VERIFYING: frozenset(
        {ActionState.SUCCEEDED, ActionState.FAILED, ActionState.INTERRUPTED}
    ),
}


def _validate_canonical_arg_value(value: Any, label: str = "args") -> None:
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ActionContractError(f"{label} contains a non-finite float")
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _validate_canonical_arg_value(item, f"{label}[{index}]")
        return
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ActionContractError(f"{label} object keys must be strings")
            _validate_canonical_arg_value(item, f"{label}.{key}")
        return
    raise ActionContractError(f"{label} contains unsupported JSON value {type(value).__name__}")


def compute_args_hash(canonical_args: Mapping[str, Any]) -> str:
    """Hash schema-normalized semantic action arguments."""
    if not isinstance(canonical_args, Mapping):
        raise ActionContractError("action args must be a JSON object")
    _validate_canonical_arg_value(canonical_args)
    return canonical_sha256(dict(canonical_args))


def compute_action_id(
    *,
    session_id: str,
    proposal_id: str,
    proposal_digest: str,
    action_type: str,
    effect_target_fingerprint: str,
    args_hash: str,
) -> str:
    checked_session = uuid4_value(session_id, "session_id")
    checked_proposal_digest = sha256_value(proposal_digest, "proposal_digest")
    checked_target = sha256_value(effect_target_fingerprint, "effect_target_fingerprint")
    checked_args = sha256_value(args_hash, "args_hash")
    assert checked_session and checked_proposal_digest and checked_target and checked_args
    return canonical_sha256(
        {
            "version": "action-v1",
            "session_id": checked_session,
            "proposal_id": string_value(proposal_id, "proposal_id"),
            "proposal_digest": checked_proposal_digest,
            "action_type": string_value(action_type, "action_type"),
            "effect_target_fingerprint": checked_target,
            "args_hash": checked_args,
        }
    )


def compute_attempt_id(action_id: str, attempt_seq: int) -> str:
    checked_action = sha256_value(action_id, "action_id")
    checked_seq = integer_value(attempt_seq, "attempt_seq", minimum=1)
    assert checked_action is not None
    return canonical_sha256(
        {
            "version": "attempt-v1",
            "action_id": checked_action,
            "attempt_seq": checked_seq,
        }
    )


def _identity_projection(record: JournalRecord) -> dict[str, Any]:
    return {
        "session_id": record.session_id,
        "flow_id": record.flow_id,
        "proposal_id": record.proposal_id,
        "proposal_digest": record.proposal_digest,
        "action_type": record.action_type,
        "effect_target_fingerprint": record.effect_target_fingerprint,
        "risk": record.risk.value,
        "input": {
            "args_hash": record.input.args_hash,
            "payload_ref": record.input.payload_ref,
            "payload_sha256": record.input.payload_sha256,
            "summary": record.input.summary,
        },
        "workspace_digest_context": record.workspace_digest_context,
    }


def _validate_record_identity(record: JournalRecord) -> None:
    if record.flow_id is None:
        raise ActionContractError("project-safe action must belong to one flow_id")
    expected_action = compute_action_id(
        session_id=record.session_id,
        proposal_id=record.proposal_id,
        proposal_digest=record.proposal_digest,
        action_type=record.action_type,
        effect_target_fingerprint=record.effect_target_fingerprint,
        args_hash=record.input.args_hash,
    )
    if record.action_id != expected_action:
        raise ActionContractError(
            f"action_id does not match deterministic action-v1 identity: "
            f"expected {expected_action}, got {record.action_id}"
        )
    expected_attempt = compute_attempt_id(record.action_id, record.attempt_seq)
    if record.attempt_id != expected_attempt:
        raise ActionContractError(
            f"attempt_id does not match attempt-v1 identity for attempt {record.attempt_seq}"
        )


def _validate_effect_semantics(record: JournalRecord) -> None:
    state = record.state
    effect = record.effect.status

    if state is ActionState.PREPARED and effect is not EffectStatus.NONE:
        raise ActionContractError("PREPARED requires effect=NONE")
    if state is ActionState.EXECUTING and effect is not EffectStatus.NONE:
        raise ActionContractError("EXECUTING durable marker requires pre-effect status NONE")
    if state is ActionState.SUCCEEDED and effect is not EffectStatus.EXPECTED:
        raise ActionContractError("SUCCEEDED requires effect=EXPECTED")
    if state is ActionState.FAILED:
        if effect is EffectStatus.UNKNOWN:
            raise ActionContractError("FAILED cannot have UNKNOWN effect")
        if not record.reason:
            raise ActionContractError("FAILED requires a reason")
    if state is ActionState.INTERRUPTED:
        if effect is EffectStatus.EXPECTED:
            raise ActionContractError("INTERRUPTED cannot claim EXPECTED effect")
        if not record.reason:
            raise ActionContractError("INTERRUPTED requires a reason")
    if state is ActionState.ABANDONED:
        if effect is not EffectStatus.NONE:
            raise ActionContractError("ABANDONED requires effect=NONE")
        if record.reason not in {
            "WORKSPACE_DRIFT",
            "SUPERSEDED",
            "USER_CANCELLED",
            "GOAL_REFINED",
        }:
            raise ActionContractError("ABANDONED requires a supported abandonment reason")


def validate_action_record(
    record: JournalRecord,
    previous_records: Iterable[JournalRecord] = (),
) -> None:
    """Validate one record against prior records for the same action."""
    _validate_record_identity(record)
    _validate_effect_semantics(record)
    previous = tuple(previous_records)

    if not previous:
        if record.state is not ActionState.PREPARED:
            raise ActionContractError("first record for an action must be PREPARED")
        if record.attempt_seq != 1 or record.transition_seq != 1:
            raise ActionContractError(
                "first action record requires attempt_seq=1 and transition_seq=1"
            )
        return

    first = previous[0]
    latest = previous[-1]
    if any(item.action_id != record.action_id for item in previous):
        raise ActionContractError("previous_records contains another action_id")
    if _identity_projection(record) != _identity_projection(first):
        raise ActionContractError("immutable action identity/provenance fields changed")
    if record.transition_seq != latest.transition_seq + 1:
        raise ActionContractError("transition_seq must advance exactly by one")

    if latest.state in {ActionState.SUCCEEDED, ActionState.ABANDONED}:
        raise ActionContractError(f"{latest.state.value} is terminal for the logical action")

    if record.attempt_seq == latest.attempt_seq:
        if record.attempt_id != latest.attempt_id:
            raise ActionContractError("attempt_id changed inside one attempt")
        allowed = _ALLOWED_SAME_ATTEMPT.get(latest.state, frozenset())
        if record.state not in allowed:
            raise ActionContractError(
                f"invalid action transition {latest.state.value} -> {record.state.value}"
            )
        return

    if record.attempt_seq != latest.attempt_seq + 1:
        raise ActionContractError("attempt_seq must stay the same or advance exactly by one")
    if latest.state is not ActionState.FAILED or latest.effect.status is not EffectStatus.NONE:
        raise ActionContractError(
            "new attempt is allowed only after FAILED with effect=NONE"
        )
    if record.state is not ActionState.PREPARED:
        raise ActionContractError("new attempt must begin with PREPARED")


def validate_action_history(records: Iterable[JournalRecord]) -> None:
    """Validate cross-record action_seq ownership and every action transition."""
    by_action: dict[str, list[JournalRecord]] = {}
    seq_owner: dict[int, str] = {}
    next_new_action_seq = 1

    for record in records:
        owner = seq_owner.get(record.action_seq)
        history = by_action.setdefault(record.action_id, [])

        if not history:
            if owner is not None and owner != record.action_id:
                raise ActionContractError("action_seq is shared by different action_ids")
            if record.action_seq != next_new_action_seq:
                raise ActionContractError(
                    f"new action_seq must be {next_new_action_seq}, got {record.action_seq}"
                )
            seq_owner[record.action_seq] = record.action_id
            next_new_action_seq += 1
        elif owner != record.action_id:
            raise ActionContractError("action_seq owner changed")

        validate_action_record(record, history)
        history.append(record)


def action_identity_from_draft(
    *,
    session_id: str,
    draft: Mapping[str, Any],
) -> str:
    input_obj = draft.get("input")
    if not isinstance(input_obj, Mapping):
        raise ActionContractError("action draft input must be an object")
    return compute_action_id(
        session_id=session_id,
        proposal_id=draft.get("proposal_id"),
        proposal_digest=draft.get("proposal_digest"),
        action_type=draft.get("action_type"),
        effect_target_fingerprint=draft.get("effect_target_fingerprint"),
        args_hash=input_obj.get("args_hash"),
    )


__all__ = [
    "ActionContractError", "action_identity_from_draft", "compute_action_id",
    "compute_args_hash", "compute_attempt_id", "validate_action_history",
    "validate_action_record",
]
