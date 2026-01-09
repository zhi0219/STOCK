from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class AtomicWriteResult:
    path: Path
    attempts: int
    bytes_written: int

    @property
    def retries_used(self) -> int:
        return max(self.attempts - 1, 0)


@dataclass(frozen=True)
class AtomicWriteFailure:
    path: Path
    attempts: int
    error_type: str
    error_message: str
    retryable: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path.as_posix(),
            "attempts": self.attempts,
            "error_type": self.error_type,
            "error_message": self.error_message,
            "retryable": self.retryable,
        }


class AtomicWriteError(RuntimeError):
    def __init__(self, failure: AtomicWriteFailure) -> None:
        super().__init__(f"atomic write failed after {failure.attempts} attempts: {failure.error_message}")
        self.failure = failure


def _log_marker(marker: str, **fields: object) -> None:
    parts = [marker]
    for key, value in fields.items():
        parts.append(f"{key}={value}")
    print("|".join(parts), flush=True)


def atomic_write_json(
    path: Path,
    data: dict[str, Any],
    retries: int = 5,
    backoff_ms: int = 50,
    fsync: bool = True,
) -> AtomicWriteResult:
    if retries < 1:
        raise ValueError("retries must be >= 1")
    payload = json.dumps(data, ensure_ascii=False, indent=2)
    path.parent.mkdir(parents=True, exist_ok=True)

    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        tmp_path = path.with_name(f".{path.name}.tmp.{os.getpid()}.{attempt}")
        _log_marker("ATOMIC_WRITE_JSON_ATTEMPT", path=path.as_posix(), attempt=attempt, tmp=tmp_path.name)
        try:
            with tmp_path.open("w", encoding="utf-8", newline="\n") as handle:
                handle.write(payload)
                handle.flush()
                if fsync:
                    os.fsync(handle.fileno())
            os.replace(tmp_path, path)
            result = AtomicWriteResult(path=path, attempts=attempt, bytes_written=len(payload.encode("utf-8")))
            if result.retries_used:
                _log_marker(
                    "ATOMIC_WRITE_JSON_RETRY_SUCCESS",
                    path=path.as_posix(),
                    attempts=result.attempts,
                    retries_used=result.retries_used,
                )
            else:
                _log_marker("ATOMIC_WRITE_JSON_SUCCESS", path=path.as_posix(), attempts=result.attempts)
            return result
        except PermissionError as exc:
            last_error = exc
            _log_marker(
                "ATOMIC_WRITE_JSON_RETRY",
                path=path.as_posix(),
                attempt=attempt,
                error_type=type(exc).__name__,
            )
            if attempt < retries:
                time.sleep((backoff_ms / 1000.0) * attempt)
            else:
                break
        except Exception as exc:
            last_error = exc
            _log_marker(
                "ATOMIC_WRITE_JSON_ERROR",
                path=path.as_posix(),
                attempt=attempt,
                error_type=type(exc).__name__,
            )
            break
        finally:
            try:
                if tmp_path.exists():
                    tmp_path.unlink()
            except Exception:
                pass

    error = last_error or RuntimeError("unknown failure")
    failure = AtomicWriteFailure(
        path=path,
        attempts=retries,
        error_type=type(error).__name__,
        error_message=str(error),
        retryable=isinstance(error, PermissionError),
    )
    _log_marker(
        "ATOMIC_WRITE_JSON_FAILED",
        path=path.as_posix(),
        attempts=failure.attempts,
        error_type=failure.error_type,
    )
    raise AtomicWriteError(failure)


def atomic_write_text(
    path: Path,
    payload: str,
    retries: int = 5,
    backoff_ms: int = 50,
    fsync: bool = True,
) -> AtomicWriteResult:
    """Atomically write UTF-8 text with LF-only newlines.

    Do not replace this with Path.write_text() in this pipeline; it does not
    allow control over newline translation on Windows.
    """
    if retries < 1:
        raise ValueError("retries must be >= 1")
    path.parent.mkdir(parents=True, exist_ok=True)

    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        tmp_path = path.with_name(f".{path.name}.tmp.{os.getpid()}.{attempt}")
        _log_marker("ATOMIC_WRITE_TEXT_ATTEMPT", path=path.as_posix(), attempt=attempt, tmp=tmp_path.name)
        try:
            with tmp_path.open("w", encoding="utf-8", newline="\n") as handle:
                handle.write(payload)
                handle.flush()
                if fsync:
                    os.fsync(handle.fileno())
            os.replace(tmp_path, path)
            result = AtomicWriteResult(path=path, attempts=attempt, bytes_written=len(payload.encode("utf-8")))
            if result.retries_used:
                _log_marker(
                    "ATOMIC_WRITE_TEXT_RETRY_SUCCESS",
                    path=path.as_posix(),
                    attempts=result.attempts,
                    retries_used=result.retries_used,
                )
            else:
                _log_marker("ATOMIC_WRITE_TEXT_SUCCESS", path=path.as_posix(), attempts=result.attempts)
            return result
        except PermissionError as exc:
            last_error = exc
            _log_marker(
                "ATOMIC_WRITE_TEXT_RETRY",
                path=path.as_posix(),
                attempt=attempt,
                error_type=type(exc).__name__,
            )
            if attempt < retries:
                time.sleep((backoff_ms / 1000.0) * attempt)
            else:
                break
        except Exception as exc:
            last_error = exc
            _log_marker(
                "ATOMIC_WRITE_TEXT_ERROR",
                path=path.as_posix(),
                attempt=attempt,
                error_type=type(exc).__name__,
            )
            break
        finally:
            try:
                if tmp_path.exists():
                    tmp_path.unlink()
            except Exception:
                pass

    error = last_error or RuntimeError("unknown failure")
    failure = AtomicWriteFailure(
        path=path,
        attempts=retries,
        error_type=type(error).__name__,
        error_message=str(error),
        retryable=isinstance(error, PermissionError),
    )
    _log_marker(
        "ATOMIC_WRITE_TEXT_FAILED",
        path=path.as_posix(),
        attempts=failure.attempts,
        error_type=failure.error_type,
    )
    raise AtomicWriteError(failure)
