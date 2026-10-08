"""Single-owner project lease for project-safe durable sessions."""
from __future__ import annotations

import errno
import os
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .paths import project_fingerprint, resolve_project_safe_state_root


class ProjectLeaseError(RuntimeError):
    """Project lease could not be acquired or released safely."""


class ProjectLeaseBusyError(ProjectLeaseError):
    """Another live process currently owns the project lease."""

    def __init__(self, message: str, *, native_error: int | None = None) -> None:
        super().__init__(message)
        self.native_error = native_error


def _win32_api_path(path: Path) -> str:
    """Return an internal extended-length path for Win32 APIs.

    User input is validated before this conversion; the extended namespace is
    an implementation detail, never accepted as project-safe user path syntax.
    """
    value = os.path.abspath(os.fspath(path))
    if value.startswith("\\\\?\\"):
        return value
    if value.startswith("\\\\"):
        return "\\\\?\\UNC\\" + value[2:]
    return "\\\\?\\" + value


def _acquire_windows(path: Path) -> int:
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_file = kernel32.CreateFileW
    create_file.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    create_file.restype = wintypes.HANDLE

    generic_read = 0x80000000
    generic_write = 0x40000000
    open_always = 4
    file_attribute_normal = 0x80
    invalid_handle = ctypes.c_void_p(-1).value

    handle = create_file(
        _win32_api_path(path),
        generic_read | generic_write,
        0,  # dwShareMode=0: the live HANDLE is the exclusive lease.
        None,
        open_always,
        file_attribute_normal,
        None,
    )
    if handle == invalid_handle:
        error = ctypes.get_last_error()
        if error in {32, 33}:  # ERROR_SHARING_VIOLATION / ERROR_LOCK_VIOLATION
            raise ProjectLeaseBusyError(
                "project lease is already held by another live process",
                native_error=error,
            )
        raise OSError(error, f"CreateFileW failed for project lease: {path}")
    return int(handle)


def _release_windows(handle: int) -> None:
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = [wintypes.HANDLE]
    close_handle.restype = wintypes.BOOL
    if not close_handle(wintypes.HANDLE(handle)):
        error = ctypes.get_last_error()
        raise OSError(error, "CloseHandle failed for project lease")


def _acquire_posix(path: Path) -> int:
    import fcntl

    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        os.close(fd)
        if exc.errno in {errno.EACCES, errno.EAGAIN}:
            raise ProjectLeaseBusyError(
                "project lease is already held by another live process",
                native_error=exc.errno,
            ) from exc
        raise
    return fd


def _release_posix(fd: int) -> None:
    import fcntl

    try:
        fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


class ProjectLease:
    """Kernel-backed exclusive lease for one canonical project root.

    Lock-file existence is not ownership. Ownership is the live Win32 HANDLE
    (or POSIX flock) held by this object.
    """

    def __init__(
        self,
        project_root: str | os.PathLike[str],
        state_root: str | os.PathLike[str] | None = None,
    ) -> None:
        self.project_root = Path(project_root)
        self.configured_state_root = Path(state_root) if state_root is not None else None
        self.state_root: Path | None = None
        self.lock_path: Path | None = None
        self._native: Any | None = None
        self._owner_lock = threading.RLock()
        self.owner_generation = 0
        self._operation_depth = 0

    @property
    def held(self) -> bool:
        return self._native is not None

    @contextmanager
    def operation(self, *, validate: Callable[[], None] | None = None) -> Iterator[None]:
        """Coherent owner operation; release waits and cannot nest inside it."""
        with self._owner_lock:
            if validate is not None:
                validate()
            if not self.held:
                raise ProjectLeaseError("a live ProjectLease is required")
            self._operation_depth += 1
            try:
                yield
            finally:
                self._operation_depth -= 1

    def acquire(self) -> "ProjectLease":
        with self._owner_lock:
            if self.held:
                raise ProjectLeaseError("project lease object is already held")

            state_root = resolve_project_safe_state_root(
                self.project_root,
                self.configured_state_root,
                create=True,
            )
            project_dir = state_root / "projects" / project_fingerprint(self.project_root)
            try:
                project_dir.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                raise ProjectLeaseError(f"cannot create project lease directory: {exc}") from exc

            lock_path = project_dir / "project.lease"
            try:
                native = _acquire_windows(lock_path) if os.name == "nt" else _acquire_posix(lock_path)
            except ProjectLeaseBusyError:
                raise
            except OSError as exc:
                raise ProjectLeaseError(f"cannot acquire project lease: {exc}") from exc

            self.state_root = state_root
            self.lock_path = lock_path
            self._native = native
            self.owner_generation += 1
            return self

    def release(self) -> None:
        with self._owner_lock:
            if self._operation_depth:
                raise ProjectLeaseError("cannot release lease during an owner operation")
            native = self._native
            if native is None:
                return
            self._native = None
            try:
                if os.name == "nt":
                    _release_windows(int(native))
                else:
                    _release_posix(int(native))
            except OSError as exc:
                raise ProjectLeaseError(f"cannot release project lease: {exc}") from exc

    def __enter__(self) -> "ProjectLease":
        return self.acquire()

    def __exit__(self, exc_type, exc, tb) -> None:
        self.release()


__all__ = ["ProjectLease", "ProjectLeaseBusyError", "ProjectLeaseError"]
