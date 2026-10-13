"""Shared local v2 action-history invariants, without reference resolution."""
from __future__ import annotations

from collections.abc import Sequence

from .action_contract import validate_action_history
from .action_models import JournalRecord
from .schema_types import ActionState
from .schema_utils import SchemaError
from .v2_models import JournalRecordV2


def validate_v2_action_history(actions: Sequence[JournalRecord]) -> None:
    validate_action_history(actions)
    first_actions: dict[str, JournalRecordV2] = {}
    attempts: dict[str, JournalRecordV2] = {}
    last_prepared_event_seq = 0
    for action in actions:
        if not isinstance(action, JournalRecordV2):
            raise SchemaError("v2 action history requires v2 records")
        first = first_actions.setdefault(action.action_id, action)
        if (action.flow_creation_ref != first.flow_creation_ref
                or action.preceding_success_ref != first.preceding_success_ref):
            raise SchemaError("immutable logical reference anchors changed")
        prepared = attempts.setdefault(action.attempt_id, action)
        if action.prepared_event_ref != prepared.prepared_event_ref:
            raise SchemaError("prepared event reference changed within attempt")
        if action.state is ActionState.PREPARED:
            if action.prepared_event_ref.event_seq < last_prepared_event_seq:
                raise SchemaError("prepared event head regresses in journal order")
            last_prepared_event_seq = action.prepared_event_ref.event_seq
