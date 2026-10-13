"""Serialize lease-bound operations before instance-local storage locks."""
from __future__ import annotations

from collections.abc import Callable
from functools import wraps
from typing import Concatenate, ParamSpec, Protocol, TypeVar

from .lease import ProjectLease


class _OwnerBound(Protocol):
    lease: ProjectLease

    def _require_lease(self) -> None: ...


_Owner = TypeVar("_Owner", bound=_OwnerBound)
_Args = ParamSpec("_Args")
_Result = TypeVar("_Result")


def owner_serialized(
    method: Callable[Concatenate[_Owner, _Args], _Result],
) -> Callable[Concatenate[_Owner, _Args], _Result]:
    """Hold one owner gate for the entire call, including nested operations."""
    @wraps(method)
    def guarded(self: _Owner, /, *args: _Args.args, **kwargs: _Args.kwargs) -> _Result:
        with self.lease.operation(validate=self._require_lease):
            return method(self, *args, **kwargs)

    return guarded
