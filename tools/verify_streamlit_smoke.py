from __future__ import annotations

import sys
from pathlib import Path

try:
    import streamlit  # noqa: F401
except Exception as exc:  # pragma: no cover - dependency gate
    print(f"FAIL: streamlit import error: {exc}")
    sys.exit(1)

ROOT = Path(__file__).resolve().parent.parent
LOGS_DIR = ROOT / "Logs"

if not LOGS_DIR.exists():
    print("WARN: Logs directory missing; creating for UI")
    LOGS_DIR.mkdir(parents=True, exist_ok=True)

print("PASS: streamlit import ok and Logs directory ready")
