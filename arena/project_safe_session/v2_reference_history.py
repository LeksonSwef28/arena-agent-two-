"""Opt-in pure reference validation; no payload, backup or snapshot admission."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from .checkpoint_contract import validate_checkpoint_manifest_digest
from .event_models import SessionEventRecord
from .schema_types import ActionState, CheckpointKind, EventType
from .schema_utils import SchemaError, uuid4_value
from .storage import _validate_hash_record
from .v2_action_history import validate_v2_action_history
from .v2_models import ActionRecordRef, CheckpointManifestV2, JournalRecordV2


@dataclass(frozen=True)
class V2SuccessEvidence:
    action: JournalRecordV2
    checkpoint: CheckpointManifestV2


def validate_v2_reference_history(
    session_id: str,
    events: tuple[SessionEventRecord, ...],
    actions: tuple[JournalRecordV2, ...],
    checkpoints: Mapping[str, CheckpointManifestV2],
) -> tuple[V2SuccessEvidence, ...]:
    """Validate structural chains and reference provenance without filesystem access.

    Callers still owe session/goal/flow event semantics, payload/backup-byte
    validation, resource CAS and snapshot projection. Only explicitly referenced
    manifests are inspected; orphan checkpoints cannot replace missing evidence.
    """
    uuid4_value(session_id, "v2_reference_history.session_id")
    previous_hash = None
    for seq, event in enumerate(events, 1):
        SessionEventRecord.from_dict(event.to_dict())
        _validate_hash_record(event.to_dict(), seq_name="event_seq", expected_seq=seq,
                              previous_hash=previous_hash)
        if event.session_id != session_id:
            raise SchemaError("event belongs to another session")
        previous_hash = event.record_hash
    previous_hash = None
    for seq, action in enumerate(actions, 1):
        JournalRecordV2.from_dict(action.to_dict())
        _validate_hash_record(action.to_dict(), seq_name="journal_seq", expected_seq=seq,
                              previous_hash=previous_hash)
        if action.session_id != session_id:
            raise SchemaError("action belongs to another session")
        previous_hash = action.record_hash
    validate_v2_action_history(actions)

    latest: dict[str, V2SuccessEvidence] = {}
    owners: set[str] = set()
    successes: list[V2SuccessEvidence] = []
    for action in actions:
        creation_ref = action.flow_creation_ref
        prefix_ref = action.prepared_event_ref
        if prefix_ref.event_seq > len(events):
            raise SchemaError("prepared event reference is outside event history")
        creation = events[creation_ref.event_seq - 1]
        prefix_head = events[prefix_ref.event_seq - 1]
        if (creation.record_hash != creation_ref.record_hash
                or prefix_head.record_hash != prefix_ref.record_hash):
            raise SchemaError("event reference hash mismatch")
        if (creation.event_type is not EventType.FLOW_CREATED
                or creation.data.get("flow_id") != action.flow_id):
            raise SchemaError("flow creation reference does not own this action")
        if any(event.event_type is EventType.FLOW_CLOSED
               and event.data.get("flow_id") == action.flow_id
               for event in events[creation_ref.event_seq:prefix_ref.event_seq]):
            raise SchemaError("flow is closed in prepared event prefix")
        # A later closure is not a claim about execution time of terminal records.
        if action.state is ActionState.PREPARED:
            predecessor = latest.get(action.flow_id or "")
            expected_ref = (None if predecessor is None else ActionRecordRef(
                predecessor.action.journal_seq, predecessor.action.record_hash))
            if action.preceding_success_ref != expected_ref:
                raise SchemaError("preceding success reference is not latest same-flow success")
            context = (creation.data.get("workspace_digest_baseline") if predecessor is None
                       else predecessor.checkpoint.workspace_digest)
            if action.workspace_digest_context != context:
                raise SchemaError("PREPARED workspace context mismatch")
        if action.state is not ActionState.SUCCEEDED:
            continue
        verification = action.workspace_verification
        assert verification is not None  # Strict v2 parser checked this above.
        if verification.checkpoint_id in owners:
            raise SchemaError("checkpoint is owned by more than one successful action")
        manifest = checkpoints.get(verification.checkpoint_id)
        if manifest is None:
            raise SchemaError("referenced success checkpoint is missing")
        CheckpointManifestV2.from_dict(manifest.to_dict())
        validate_checkpoint_manifest_digest(manifest)
        if (manifest.checkpoint_id != verification.checkpoint_id
                or manifest.manifest_sha256 != verification.manifest_sha256
                or manifest.session_id != session_id
                or manifest.action_id != action.action_id
                or manifest.attempt_id != action.attempt_id
                or manifest.kind is not CheckpointKind.RESOURCE_AFTER):
            raise SchemaError("success checkpoint reference ownership mismatch")
        owners.add(manifest.checkpoint_id)
        evidence = V2SuccessEvidence(action, manifest)
        successes.append(evidence)
        latest[action.flow_id or ""] = evidence
    return tuple(successes)


__all__ = ["V2SuccessEvidence", "validate_v2_reference_history"]
