"""Runtime contracts retained while clearing static-analysis debt."""
from __future__ import annotations

import ctypes
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from arena.project_safe_session import lease
from arena.project_safe_session.action_contract import action_identity_from_draft, compute_action_id
from arena.project_safe_session.schema_utils import SchemaError

SESSION = "11111111-1111-4111-8111-111111111111"
FIELDS = ("proposal_id", "proposal_digest", "action_type", "effect_target_fingerprint", "args_hash")


def _identity_fields():
    return dict(proposal_id="proposal-1", proposal_digest="a" * 64,
                action_type="fs.edit", effect_target_fingerprint="b" * 64, args_hash="c" * 64)


@pytest.mark.parametrize("field", FIELDS)
@pytest.mark.parametrize("invalid", [None, 17, ""])
def test_untrusted_draft_preserves_identity_validation(field, invalid):
    fields = _identity_fields()
    fields[field] = invalid
    draft = {key: value for key, value in fields.items() if key != "args_hash"}
    draft["input"] = {"args_hash": fields["args_hash"]}
    with pytest.raises(SchemaError) as direct:
        compute_action_id(session_id=SESSION, **fields)
    with pytest.raises(SchemaError) as from_draft:
        action_identity_from_draft(session_id=SESSION, draft=draft)
    assert str(from_draft.value) == str(direct.value)
    assert field in str(from_draft.value)


def test_valid_draft_identity_matches_direct_hash():
    fields = _identity_fields()
    draft = {key: value for key, value in fields.items() if key != "args_hash"}
    draft["input"] = {"args_hash": fields["args_hash"]}
    assert action_identity_from_draft(session_id=SESSION, draft=draft) == compute_action_id(
        session_id=SESSION, **fields
    )


@pytest.fixture
def win32_api(monkeypatch):
    kernel = SimpleNamespace(CreateFileW=Mock(return_value=123), CloseHandle=Mock(return_value=1))
    loader = Mock(return_value=kernel)
    error = Mock(return_value=5)
    monkeypatch.setattr(ctypes, "WinDLL", loader, raising=False)
    monkeypatch.setattr(ctypes, "get_last_error", error, raising=False)
    return kernel, loader, error


def test_win32_lease_retains_exclusive_handle_and_close(win32_api, tmp_path: Path):
    kernel, loader, error = win32_api
    target = tmp_path / "lease.lock"
    assert lease._acquire_windows(target) == 123
    loader.assert_called_once_with("kernel32", use_last_error=True)
    args = kernel.CreateFileW.call_args.args
    assert args == (lease._win32_api_path(target), 0xC0000000, 0, None, 4, 0x80, None)
    assert kernel.CreateFileW.restype is ctypes.c_void_p
    lease._release_windows(123)
    assert kernel.CloseHandle.call_args.args[0].value == 123
    error.assert_not_called()


@pytest.mark.parametrize("code", [32, 33, 5])
def test_win32_acquire_retains_native_errors(win32_api, tmp_path: Path, code):
    kernel, _, error = win32_api
    kernel.CreateFileW.return_value = ctypes.c_void_p(-1).value
    error.return_value = code
    expected = lease.ProjectLeaseBusyError if code in {32, 33} else OSError
    with pytest.raises(expected) as caught:
        lease._acquire_windows(tmp_path / "lease.lock")
    if code in {32, 33}:
        assert caught.value.native_error == code
    else:
        assert caught.value.errno == code
    kernel.CloseHandle.assert_not_called()


def test_win32_close_retains_native_error(win32_api):
    kernel, _, error = win32_api
    kernel.CloseHandle.return_value = 0
    with pytest.raises(OSError) as caught:
        lease._release_windows(123)
    assert caught.value.errno == error.return_value
