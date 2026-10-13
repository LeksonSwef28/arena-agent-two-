"""T88 synthetic snapshot prefixes/gaps; no production recovery verdict."""
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
    StateSnapshotV2,
    assess_v2_snapshot_projection,
    checkpoint_manifest_sha256,
)
from tests.test_project_safe_session_schemas import SHA256_A, SHA256_B, SHA256_C, _checkpoint
from tests.test_project_safe_session_v2_reference_history import FLOW, SESSION, History
from tests.test_project_safe_session_v2_state import _v2

BASELINE_ID = str(UUID(int=50, version=4))


def _history():
    h = History()
    baseline = _checkpoint()
    baseline.update(schema_version=2, checkpoint_id=BASELINE_ID, session_id=SESSION,
                    action_id=None, attempt_id=None, kind="SESSION_BASELINE", resources=[],
                    workspace_digest=SHA256_A)
    baseline["manifest_sha256"] = checkpoint_manifest_sha256(baseline)
    h.checkpoints[BASELINE_ID] = baseline
    return h


def _snapshot(h, at=None, event_at=None):
    at = len(h.actions) if at is None else at
    event_at = len(h.events) if event_at is None else event_at
    ref = None if at == 0 else {"journal_seq": at, "record_hash": h.actions[at - 1]["record_hash"]}
    raw = _v2(reference=ref)
    raw["session_id"] = SESSION
    raw["execution"]["last_event_seq"] = event_at
    raw["workspace"]["session_baseline"].update(
        checkpoint_id=BASELINE_ID, workspace_digest=SHA256_A,
        head_sha=h.checkpoints[BASELINE_ID]["head_sha"])
    successes = [r for r in h.actions[:at] if r["state"] == "SUCCEEDED"]
    cp_id = BASELINE_ID if not successes else successes[-1]["workspace_verification"]["checkpoint_id"]
    checkpoint = h.checkpoints[cp_id]
    raw["workspace"].update(last_verified_checkpoint_id=cp_id,
        workspace_digest_last_verified=checkpoint["workspace_digest"],
        last_verified_head_sha=checkpoint["head_sha"], last_verified_at=checkpoint["created_at"])
    latest = {r["action_id"]: r for r in h.actions[:at]}
    terminal = [r for r in latest.values() if r["state"] in {"SUCCEEDED", "FAILED", "ABANDONED"}]
    last = max(terminal, key=lambda r: r["action_seq"], default=None)
    raw["execution"].update(last_action_seq=0 if last is None else last["action_seq"],
        last_terminal_action_id=None if last is None else last["action_id"])
    pending = [r for r in latest.values() if r["state"] not in {"SUCCEEDED", "FAILED", "ABANDONED"}]
    raw["execution"]["pending_action_id"] = None if not pending else pending[0]["action_id"]
    flow = None
    for event in h.events[:event_at]:
        if event["event_type"] == "FLOW_CREATED":
            data = event["data"]
            flow = {key: data[key] for key in ("flow_id", "goal_revision",
                "workspace_digest_baseline", "workspace_digest_expected_current")}
            flow["created_at"] = event["recorded_at"]
        elif event["event_type"] == "FLOW_CLOSED":
            flow = None
    if flow is not None:
        flow_success = [r for r in successes if r["flow_id"] == flow["flow_id"]]
        if flow_success:
            cp_id = flow_success[-1]["workspace_verification"]["checkpoint_id"]
            flow["workspace_digest_expected_current"] = h.checkpoints[cp_id]["workspace_digest"]
    raw["active_flow"] = flow
    raw["lifecycle"]["phase"] = "IDLE" if flow is None else "PLANNING"
    return raw


def _assess(h, raw):
    return assess_v2_snapshot_projection(StateSnapshotV2.from_dict(raw),
        tuple(SessionEventRecord.from_dict(r) for r in h.events),
        tuple(JournalRecordV2.from_dict(r) for r in h.actions),
        {key: CheckpointManifestV2.from_dict(r) for key, r in h.checkpoints.items()})


@pytest.mark.parametrize("at", [0, 1, 2, 3, 4])
def test_v2_snapshot_projection_distinguishes_every_success_publication_boundary(at):
    h = _history()
    h.action()
    raw = _snapshot(h, at)
    before = deepcopy((raw, h.actions, h.events, h.checkpoints))
    result = _assess(h, raw)
    assert result.snapshot_behind is (at < 4)
    assert tuple(r.journal_seq for r in result.unapplied_actions) == tuple(range(at + 1, 5))
    assert not result.unapplied_events
    assert before == (raw, h.actions, h.events, h.checkpoints)
    assert raw["execution"]["last_action_seq"] == (1 if at == 4 else 0)


@pytest.mark.parametrize("tail", ["FAILED", "ABANDONED", "PREPARED"])
def test_v2_snapshot_projection_handles_non_success_tail(tail):
    h = _history()
    h.action()
    h.action(final=tail)
    result = _assess(h, _snapshot(h))
    assert result.snapshot_behind is False
    # Fully applied pending is still not an admission/recovery clean verdict.
    assert not hasattr(result, "required")


def test_v2_snapshot_projection_keeps_old_output_when_later_success_is_unapplied():
    h = _history()
    h.action(output=SHA256_B)
    first_cp = h.actions[-1]["workspace_verification"]["checkpoint_id"]
    h.action(output=SHA256_C)
    raw = _snapshot(h, 4)
    assert raw["workspace"]["last_verified_checkpoint_id"] == first_cp
    assert _assess(h, raw).snapshot_behind is True
    raw["workspace"]["workspace_digest_last_verified"] = SHA256_C
    with pytest.raises(SchemaError, match="last verified"):
        _assess(h, raw)


@pytest.mark.parametrize("event_at", [0, 1])
def test_v2_snapshot_projection_reports_event_only_gap(event_at):
    h = _history()
    h.event("FLOW_CLOSED", {"flow_id": FLOW, "reason": "synthetic close"})
    result = _assess(h, _snapshot(h, event_at=event_at))
    assert result.snapshot_behind is True
    assert not result.unapplied_actions
    assert len(result.unapplied_events) == 2 - event_at


def test_v2_snapshot_projection_accepts_baseline_without_actions():
    h = _history()
    assert _assess(h, _snapshot(h)).snapshot_behind is False


def test_v2_snapshot_projection_accepts_closed_flow_after_applied_success():
    h = _history()
    h.action()
    h.event("FLOW_CLOSED", {"flow_id": FLOW, "reason": "synthetic close"})
    assert _assess(h, _snapshot(h)).snapshot_behind is False


def test_v2_snapshot_projection_new_flow_resets_expected_current_not_last_verified():
    h = _history()
    h.action()
    h.event("FLOW_CLOSED", {"flow_id": FLOW, "reason": "synthetic close"})
    new_id = str(UUID(int=3, version=4))
    data = deepcopy(h.events[0]["data"])
    data["flow_id"] = new_id
    h.event("FLOW_CREATED", data)
    raw = _snapshot(h)
    assert raw["active_flow"]["workspace_digest_expected_current"] == SHA256_A
    assert raw["workspace"]["workspace_digest_last_verified"] == SHA256_B
    assert _assess(h, raw).snapshot_behind is False


def test_v2_snapshot_projection_retry_uses_latest_logical_action_terminal_semantics():
    h = _history()
    first = h.action(final="FAILED")
    before_retry = _snapshot(h)
    h.action(final="PREPARED", reuse=first)
    assert _assess(h, before_retry).snapshot_behind is True
    raw = _snapshot(h)
    assert raw["execution"]["last_action_seq"] == 0
    assert raw["execution"]["last_terminal_action_id"] is None
    assert raw["execution"]["pending_action_id"] == first["action_id"]
    assert _assess(h, raw).snapshot_behind is False


@pytest.mark.parametrize("case", ["action_ahead", "action_hash", "event_ahead", "event_behind"])
def test_v2_snapshot_projection_rejects_invalid_watermark(case):
    h = _history()
    h.action()
    raw = _snapshot(h)
    if case == "action_ahead":
        raw["execution"]["last_applied_action_ref"]["journal_seq"] = 5
    elif case == "action_hash":
        raw["execution"]["last_applied_action_ref"]["record_hash"] = SHA256_C
    elif case == "event_ahead":
        raw["execution"]["last_event_seq"] = 2
    else:
        raw["execution"]["last_event_seq"] = 0
    with pytest.raises(SchemaError, match="watermark"):
        _assess(h, raw)


@pytest.mark.parametrize("field,value", [("last_action_seq", 3),
    ("last_terminal_action_id", SHA256_C), ("pending_action_id", SHA256_C)])
def test_v2_snapshot_projection_rejects_forged_execution_fields(field, value):
    h = _history()
    h.action()
    raw = _snapshot(h)
    raw["execution"][field] = value
    with pytest.raises(SchemaError, match="projection mismatch"):
        _assess(h, raw)


@pytest.mark.parametrize("field,value", [("last_verified_checkpoint_id", BASELINE_ID),
    ("workspace_digest_last_verified", SHA256_C), ("last_verified_head_sha", "c" * 40)])
def test_v2_snapshot_projection_rejects_forged_verified_workspace(field, value):
    h = _history()
    h.action()
    raw = _snapshot(h)
    raw["workspace"][field] = value
    with pytest.raises(SchemaError, match="last verified"):
        _assess(h, raw)


@pytest.mark.parametrize("case", ["digest", "head", "missing", "owner", "kind"])
def test_v2_snapshot_projection_rejects_bad_baseline(case):
    h = _history()
    raw = _snapshot(h)
    if case in {"digest", "head"}:
        raw["workspace"]["session_baseline"]["workspace_digest" if case == "digest" else "head_sha"] = (
            SHA256_C if case == "digest" else "c" * 40)
    elif case == "missing":
        del h.checkpoints[BASELINE_ID]
    else:
        cp = h.checkpoints[BASELINE_ID]
        if case == "owner":
            cp["session_id"] = str(UUID(int=9, version=4))
        else:
            cp.update(kind="RESOURCE_AFTER", action_id=SHA256_A, attempt_id=SHA256_B,
                      resources=_checkpoint()["resources"])
        cp["manifest_sha256"] = checkpoint_manifest_sha256(cp)
    with pytest.raises(SchemaError, match="baseline"):
        _assess(h, raw)


def test_v2_snapshot_projection_rejects_wrong_active_expected_current():
    h = _history()
    h.action()
    raw = _snapshot(h)
    raw["active_flow"]["workspace_digest_expected_current"] = SHA256_C
    with pytest.raises(SchemaError, match="expected-current"):
        _assess(h, raw)


def test_v2_snapshot_projection_rejects_snapshot_advanced_without_applied_success():
    h = _history()
    h.action()
    raw = _snapshot(h, 3)
    raw["execution"].update(last_action_seq=1, last_terminal_action_id=h.actions[-1]["action_id"],
                            pending_action_id=None)
    with pytest.raises(SchemaError, match="terminal action"):
        _assess(h, raw)


def test_v2_snapshot_projection_validates_corrupt_unapplied_success_before_gap():
    h = _history()
    h.action()
    raw = _snapshot(h, 0)
    del h.checkpoints[h.actions[-1]["workspace_verification"]["checkpoint_id"]]
    with pytest.raises(SchemaError, match="success checkpoint is missing"):
        _assess(h, raw)


def test_v2_snapshot_projection_rejects_multiple_pending_actions_at_prefix():
    h = _history()
    h.action(final="PREPARED")
    h.action(final="PREPARED")
    with pytest.raises(SchemaError, match="more than one pending"):
        _assess(h, _snapshot(h))


def test_v2_snapshot_projection_rejects_legacy_snapshot():
    h = _history()
    state = replace(StateSnapshotV2.from_dict(_snapshot(h)), schema_version=1)
    with pytest.raises(SchemaError):
        assess_v2_snapshot_projection(state, (), (), {})


def test_v2_snapshot_projection_performs_no_filesystem_reads(monkeypatch):
    h = _history()
    h.action()
    raw = _snapshot(h, 3)

    def forbidden(*args, **kwargs):
        pytest.fail("projection must perform no filesystem reads")

    with monkeypatch.context() as patch:
        patch.setattr("builtins.open", forbidden)
        patch.setattr(Path, "read_bytes", forbidden)
        assert _assess(h, raw).snapshot_behind is True


@pytest.mark.parametrize("field,value", [("flow_id", str(UUID(int=9, version=4))),
    ("workspace_digest_baseline", SHA256_C), ("created_at", "2026-10-07T02:00:00Z")])
def test_v2_snapshot_projection_reuses_immutable_flow_evidence(field, value):
    h = _history()
    raw = _snapshot(h)
    raw["active_flow"][field] = value
    with pytest.raises(SchemaError, match="FLOW_CREATED history"):
        _assess(h, raw)


def test_v2_snapshot_projection_rejects_null_watermark_with_advanced_workspace():
    h = _history()
    h.action()
    raw = _snapshot(h)
    raw["execution"]["last_applied_action_ref"] = None
    with pytest.raises(SchemaError, match="last verified"):
        _assess(h, raw)


def test_v2_snapshot_projection_rejects_pending_action_in_closed_previous_flow():
    h = _history()
    h.action(final="PREPARED")
    h.event("FLOW_CLOSED", {"flow_id": FLOW, "reason": "synthetic close"})
    data = deepcopy(h.events[0]["data"])
    data["flow_id"] = str(UUID(int=3, version=4))
    h.event("FLOW_CREATED", data)
    with pytest.raises(SchemaError, match="pending action does not belong"):
        _assess(h, _snapshot(h))


def test_v2_snapshot_projection_reuses_baseline_manifest_digest_validation():
    h = _history()
    raw = _snapshot(h)
    h.checkpoints[BASELINE_ID]["head_sha"] = "c" * 40
    with pytest.raises(ValueError, match="manifest digest mismatch"):
        _assess(h, raw)


def test_v2_snapshot_projection_rejects_pending_context_superseded_by_other_success():
    h = _history()
    h.action(final="PREPARED")
    h.action()
    with pytest.raises(SchemaError, match="pending context"):
        _assess(h, _snapshot(h))
