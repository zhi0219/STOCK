from __future__ import annotations

import subprocess
import sys
from typing import Optional, Sequence


def configure_stdio_utf8() -> None:
    """Best-effort UTF-8 stdio configuration for Windows console pipes.

    Some Windows environments default to a legacy code page (e.g., cp1252) which
    rejects emoji or CJK characters. Reconfiguring stdio ensures our scripts can
    emit human-friendly output without crashing.
    """

    for stream_name in ("stdout", "stderr"):
        stream: Optional[object] = getattr(sys, stream_name, None)
        if stream is None:
            continue
        reconfigure = getattr(stream, "reconfigure", None)
        if not callable(reconfigure):
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace", newline="\n")
        except Exception:
            continue


def run_cmd_utf8(cmd: Sequence[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
    """Run a subprocess with UTF-8 output handling.

    Ensures text mode with ``encoding="utf-8"`` and ``errors="replace"`` so Windows
    code pages do not raise decoding errors. Callers can override other
    ``subprocess.run`` arguments via ``kwargs``.
    """

    run_kwargs = {
        "capture_output": True,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
    }
    run_kwargs.update(kwargs)
    return subprocess.run(cmd, **run_kwargs)
