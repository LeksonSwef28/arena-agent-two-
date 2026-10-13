"""T87 synthetic reference histories, not runtime or backup-byte validation."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from uuid import UUID

import pytest

from arena.project_safe_session import (
    CheckpointManifestV2,
    JournalRecordV2,
    SchemaError,
    SessionEventRecord,
    canonical_sha256,
    checkpoint_manifest_sha256,
    compute_action_id,
    compute_attempt_id,
    validate_v2_reference_history,
)
from tests.test_project_safe_session_schemas import SHA256_A, SHA256_B, SHA256_C, _checkpoint, _journal

SESSION = str(UUID(int=1, version=4))
FLOW = str(UUID(int=2, version=4))
NOW = "2026-10-07T01:30:00Z"


def _rehash(records, seq_key):
    previous = None
    for seq, raw in enumerate(records, 1):
        raw[seq_key] = seq
        raw["previous_record_hash"] = previous
        raw["record_hash"] = canonical_sha256({k: v for k, v in raw.items() if k != "record_hash"})
        previous = raw["record_hash"]


class History:
    def __init__(self):
        self.events = []
        self.actions = []
        self.checkpoints = {}
        self.latest = {}
        self.flow = FLOW
        self.creation = self.event("FLOW_CREATED", {"flow_id": FLOW,
            "workspace_digest_baseline": SHA256_A, "workspace_digest_expected_current": SHA256_A,
            "goal_revision": 1, "workspace_clean": True, "head_sha": "a" * 40})

    def event(self, kind, data):
        raw = {"schema_version": 1, "session_id": SESSION, "event_type": kind,
               "event_seq": 1, "recorded_at": NOW, "data": data,
               "previous_record_hash": None, "record_hash": SHA256_A}
        self.events.append(raw)
        _rehash(self.events, "event_seq")
        return {"event_seq": len(self.events), "record_hash": raw["record_hash"]}

    def action(self, final="SUCCEEDED", output=SHA256_B, reuse=None):
        number = len({r["action_id"] for r in self.actions}) + 1
        raw = deepcopy(_journal() if reuse is None else reuse)
        raw.update(schema_version=2, session_id=SESSION, flow_id=self.flow,
                   proposal_id=f"proposal-{number}" if reuse is None else reuse["proposal_id"],
                   action_seq=number if reuse is None else reuse["action_seq"])
        action_id = compute_action_id(session_id=SESSION, proposal_id=raw["proposal_id"],
            proposal_digest=raw["proposal_digest"], action_type=raw["action_type"],
            effect_target_fingerprint=raw["effect_target_fingerprint"], args_hash=raw["input"]["args_hash"])
        previous = self.latest.get(self.flow)
        raw.update(action_id=action_id, attempt_seq=1 if reuse is None else reuse["attempt_seq"] + 1,
                   flow_creation_ref=deepcopy(self.creation), prepared_event_ref={
                       "event_seq": len(self.events), "record_hash": self.events[-1]["record_hash"]},
                   preceding_success_ref=None if previous is None else {
                       "journal_seq": previous["journal_seq"], "record_hash": previous["record_hash"]},
                   workspace_digest_context=SHA256_A if previous is None else
                       self.checkpoints[previous["workspace_verification"]["checkpoint_id"]]["workspace_digest"])
        raw["attempt_id"] = compute_attempt_id(action_id, raw["attempt_seq"])
        raw["input"]["payload_ref"] = f"actions/{action_id}/input.json"
        prior = [r for r in self.actions if r["action_id"] == action_id]
        transition = prior[-1]["transition_seq"] if prior else 0
        states = ["PREPARED", "EXECUTING", "VERIFYING", "SUCCEEDED"] if final == "SUCCEEDED" else (
            ["PREPARED"] if final == "PREPARED" else ["PREPARED", final])
        first = None
        for state in states:
            record = deepcopy(raw)
            transition += 1
            record.update(state=state, transition_seq=transition, workspace_verification=None,
                          reason="USER_CANCELLED" if state == "ABANDONED" else
                              ("synthetic failure" if state == "FAILED" else None))
            record["effect"]["status"] = "EXPECTED" if state == "SUCCEEDED" else "NONE"
            if state == "SUCCEEDED":
                manifest = _checkpoint()
                cp_id = str(UUID(int=100 + len(self.checkpoints), version=4))
                manifest.update(schema_version=2, checkpoint_id=cp_id, session_id=SESSION,
                                action_id=action_id, attempt_id=raw["attempt_id"],
                                workspace_digest=output, kind="RESOURCE_AFTER")
                manifest["manifest_sha256"] = checkpoint_manifest_sha256(manifest)
                self.checkpoints[cp_id] = manifest
                record["workspace_verification"] = {"checkpoint_id": cp_id,
                    "manifest_sha256": manifest["manifest_sha256"]}
                self.latest[self.flow] = record
            self.actions.append(record)
            _rehash(self.actions, "journal_seq")
            if first is None:
                first = record
        return first

    def validate(self):
        return validate_v2_reference_history(SESSION,
            tuple(SessionEventRecord.from_dict(r) for r in self.events),
            tuple(JournalRecordV2.from_dict(r) for r in self.actions),
            {key: CheckpointManifestV2.from_dict(r) for key, r in self.checkpoints.items()})

    def update_manifest(self, change):
        record = self.actions[-1]
        ref = record["workspace_verification"]
        manifest = self.checkpoints[ref["checkpoint_id"]]
        manifest.update(change)
        manifest["manifest_sha256"] = checkpoint_manifest_sha256(manifest)
        ref["manifest_sha256"] = manifest["manifest_sha256"]
        _rehash(self.actions, "journal_seq")


@pytest.mark.parametrize("output", [SHA256_A, SHA256_B])
@pytest.mark.parametrize("tail", ["SUCCEEDED", "FAILED", "ABANDONED", "PREPARED"])
def test_v2_reference_history_accepts_progression_without_mutating_input(output, tail):
    h = History()
    h.action(output=output)
    h.action(final=tail)
    before = deepcopy((h.events, h.actions, h.checkpoints))
    evidence = h.validate()
    assert len(evidence) == (2 if tail == "SUCCEEDED" else 1)
    assert evidence[0].checkpoint.workspace_digest == output
    assert evidence[0].action.effect.after_digest is None
    assert before == (h.events, h.actions, h.checkpoints)


def test_v2_reference_history_accepts_empty_action_history():
    assert History().validate() == ()


def test_v2_reference_history_accepts_new_flow_baseline_reset():
    h = History()
    h.action()
    h.event("FLOW_CLOSED", {"flow_id": FLOW, "reason": "synthetic close"})
    h.flow = str(UUID(int=3, version=4))
    h.creation = h.event("FLOW_CREATED", {"flow_id": h.flow, "workspace_digest_baseline": SHA256_A})
    h.action()
    assert len(h.validate()) == 2


def test_v2_reference_history_accepts_retry_with_refreshed_event_head():
    h = History()
    first = h.action(final="FAILED")
    h.event("STATUS_CHANGED", {"synthetic": True})
    h.action(reuse=first)
    assert len(h.validate()) == 1


def test_v2_reference_history_closure_after_preparation_does_not_prove_execution_order():
    h = History()
    h.action()
    h.event("FLOW_CLOSED", {"flow_id": FLOW, "reason": "synthetic close"})
    assert len(h.validate()) == 1


@pytest.mark.parametrize("case", ["hash", "missing", "wrong_type", "wrong_flow", "closed"])
def test_v2_reference_history_rejects_invalid_event_anchor(case):
    h = History()
    if case == "closed":
        h.event("FLOW_CLOSED", {"flow_id": FLOW, "reason": "closed"})
    h.action()
    if case == "hash":
        for record in h.actions:
            record["prepared_event_ref"]["record_hash"] = SHA256_C
    elif case == "missing":
        for record in h.actions:
            record["prepared_event_ref"] = {"event_seq": 2, "record_hash": SHA256_C}
    elif case == "wrong_type":
        h.events[0]["event_type"] = "STATUS_CHANGED"
        _rehash(h.events, "event_seq")
        for record in h.actions:
            record["flow_creation_ref"]["record_hash"] = h.events[0]["record_hash"]
            record["prepared_event_ref"]["record_hash"] = h.events[0]["record_hash"]
    elif case == "wrong_flow":
        h.events[0]["data"]["flow_id"] = str(UUID(int=9, version=4))
        _rehash(h.events, "event_seq")
        for record in h.actions:
            record["flow_creation_ref"]["record_hash"] = h.events[0]["record_hash"]
            record["prepared_event_ref"]["record_hash"] = h.events[0]["record_hash"]
    _rehash(h.actions, "journal_seq")
    with pytest.raises(SchemaError):
        h.validate()


@pytest.mark.parametrize("tail", ["SUCCEEDED", "FAILED", "ABANDONED", "PREPARED"])
def test_v2_reference_history_rejects_forged_context_after_success(tail):
    h = History()
    h.action()
    first = h.action(final=tail)
    for record in h.actions:
        if record["action_id"] == first["action_id"]:
            record["workspace_digest_context"] = SHA256_C
    _rehash(h.actions, "journal_seq")
    with pytest.raises(SchemaError, match="context mismatch"):
        h.validate()


@pytest.mark.parametrize("case", ["missing", "wrong_hash", "stale"])
def test_v2_reference_history_rejects_inexact_predecessor(case):
    h = History()
    h.action()
    old = deepcopy(h.actions[-1])
    h.action()
    first = h.action(final="FAILED")
    ref = (None if case == "missing" else {"journal_seq": old["journal_seq"],
           "record_hash": SHA256_C if case == "wrong_hash" else old["record_hash"]})
    for record in h.actions:
        if record["action_id"] == first["action_id"]:
            record["preceding_success_ref"] = ref
    _rehash(h.actions, "journal_seq")
    with pytest.raises(SchemaError, match="latest same-flow"):
        h.validate()


@pytest.mark.parametrize("change", [{"session_id": str(UUID(int=8, version=4))},
    {"action_id": SHA256_C}, {"attempt_id": SHA256_C}, {"kind": "RESOURCE_BEFORE"},
    {"checkpoint_id": str(UUID(int=9, version=4))}])
def test_v2_reference_history_rejects_wrong_manifest_owner(change):
    h = History()
    h.action()
    h.update_manifest(change)
    with pytest.raises(SchemaError, match="ownership mismatch"):
        h.validate()


def test_v2_reference_history_rejects_missing_intermediate_checkpoint_despite_final_output():
    h = History()
    h.action()
    cp_id = h.actions[-1]["workspace_verification"]["checkpoint_id"]
    h.action()
    del h.checkpoints[cp_id]
    with pytest.raises(SchemaError, match="checkpoint is missing"):
        h.validate()


def test_v2_reference_history_ignores_unreferenced_manifest():
    h = History()
    h.action()
    h.checkpoints["orphan"] = deepcopy(next(iter(h.checkpoints.values())))
    assert len(h.validate()) == 1


@pytest.mark.parametrize("anchor", ["flow_creation_ref", "prepared_event_ref", "preceding_success_ref"])
def test_v2_reference_history_rejects_anchor_change_inside_attempt(anchor):
    h = History()
    h.event("STATUS_CHANGED", {"synthetic": True})
    h.action()
    record = h.actions[-1]
    if anchor == "preceding_success_ref":
        record[anchor] = {"journal_seq": 1, "record_hash": h.actions[0]["record_hash"]}
    else:
        record[anchor]["record_hash"] = SHA256_C
    _rehash(h.actions, "journal_seq")
    with pytest.raises(SchemaError, match="reference.*changed"):
        h.validate()


@pytest.mark.parametrize("family", ["event", "action"])
@pytest.mark.parametrize("field,value", [("record_hash", SHA256_C), ("session_id", str(UUID(int=9, version=4)))])
def test_v2_reference_history_rejects_chain_corruption_or_other_session(family, field, value):
    h = History()
    h.action()
    records = h.events if family == "event" else h.actions
    records[-1][field] = value
    if field == "session_id":
        _rehash(records, "event_seq" if family == "event" else "journal_seq")
    with pytest.raises(ValueError if field == "session_id" else RuntimeError):
        h.validate()


def test_v2_reference_history_rejects_legacy_manifest_without_downgrade():
    h = History()
    h.action()
    events = tuple(SessionEventRecord.from_dict(r) for r in h.events)
    actions = tuple(JournalRecordV2.from_dict(r) for r in h.actions)
    manifests = {key: replace(CheckpointManifestV2.from_dict(r), schema_version=1)
                 for key, r in h.checkpoints.items()}
    with pytest.raises(SchemaError):
        validate_v2_reference_history(SESSION, events, actions, manifests)


def test_v2_reference_history_rejects_mixed_action_versions():
    h = History()
    h.action()
    events = tuple(SessionEventRecord.from_dict(r) for r in h.events)
    actions = tuple(JournalRecordV2.from_dict(r) for r in h.actions)
    actions = actions[:-1] + (replace(actions[-1], schema_version=1),)
    with pytest.raises(SchemaError):
        validate_v2_reference_history(SESSION, events, actions, {})


@pytest.mark.parametrize("case", ["manifest_digest", "reference_hash", "reuse"])
def test_v2_reference_history_rejects_checkpoint_integrity_or_reuse(case):
    h = History()
    h.action()
    first_ref = deepcopy(h.actions[-1]["workspace_verification"])
    if case == "reuse":
        h.action()
        h.actions[-1]["workspace_verification"] = first_ref
    elif case == "manifest_digest":
        h.checkpoints[first_ref["checkpoint_id"]]["workspace_digest"] = SHA256_C
    else:
        h.actions[-1]["workspace_verification"]["manifest_sha256"] = SHA256_C
    _rehash(h.actions, "journal_seq")
    with pytest.raises(ValueError, match="digest mismatch|ownership mismatch|more than one"):
        h.validate()


def test_v2_reference_history_rejects_forged_initial_context():
    h = History()
    h.action()
    for record in h.actions:
        record["workspace_digest_context"] = SHA256_C
    _rehash(h.actions, "journal_seq")
    with pytest.raises(SchemaError, match="context mismatch"):
        h.validate()


def test_v2_reference_history_requires_a_valid_session_even_for_empty_inputs():
    with pytest.raises(SchemaError):
        validate_v2_reference_history("invalid", (), (), {})


def test_v2_reference_history_performs_no_filesystem_reads(monkeypatch):
    h = History()
    h.action()

    def forbidden(*args, **kwargs):
        pytest.fail("reference validator must perform no filesystem reads")

    with monkeypatch.context() as patch:
        patch.setattr("builtins.open", forbidden)
        patch.setattr(Path, "read_bytes", forbidden)
        assert len(h.validate()) == 1


def test_v2_reference_history_rejects_regressing_prepared_event_head():
    h = History()
    h.action()
    old_head = deepcopy(h.creation)
    h.event("STATUS_CHANGED", {"synthetic": True})
    h.action()
    first = h.action(final="FAILED")
    for record in h.actions:
        if record["action_id"] == first["action_id"]:
            record["prepared_event_ref"] = old_head
    _rehash(h.actions, "journal_seq")
    with pytest.raises(SchemaError, match="regresses"):
        h.validate()


def test_v2_reference_history_reuses_deterministic_action_contract():
    h = History()
    h.action()
    parsed = tuple(JournalRecordV2.from_dict(r) for r in h.actions)
    parsed = tuple(replace(r, action_id=SHA256_C) for r in parsed)
    # Rehash so the shared identity contract, rather than hash-chain, rejects it.
    raw = [r.to_dict() for r in parsed]
    for record in raw:
        record["input"]["payload_ref"] = f"actions/{SHA256_C}/input.json"
        record["attempt_id"] = compute_attempt_id(SHA256_C, 1)
    _rehash(raw, "journal_seq")
    with pytest.raises(ValueError, match="deterministic"):
        validate_v2_reference_history(SESSION, tuple(SessionEventRecord.from_dict(r) for r in h.events),
            tuple(JournalRecordV2.from_dict(r) for r in raw), {})
