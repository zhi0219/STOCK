from __future__ import annotations

import sys
from typing import Optional


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
