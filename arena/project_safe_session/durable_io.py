"""Shared durable byte-write primitives for project-safe security state."""
from __future__ import annotations

import os
from pathlib import Path


def durable_replace(path: Path, data: bytes) -> None:
    """Publish exact bytes with temp-file flush/fsync followed by os.replace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    try:
        with tmp.open("wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def durable_append(path: Path, data: bytes) -> None:
    """Append exact bytes and flush/fsync the file descriptor before returning."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("ab") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())


__all__ = ["durable_append", "durable_replace"]
