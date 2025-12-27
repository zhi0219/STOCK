from __future__ import annotations

import sys
from datetime import datetime, timezone

if __name__ == "__main__":
    from pathlib import Path

    ROOT = Path(__file__).resolve().parent.parent
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))

from tools.ui_app import ensure_aware_utc, parse_iso_timestamp, utc_now


def _summary(status: str, reason: str) -> str:
    return "|".join(["UI_TIME_MATH_SUMMARY", f"status={status}", f"reason={reason}"])


def main() -> int:
    status = "PASS"
    reason = "ok"

    try:
        naive = datetime(2025, 12, 21, 23, 8, 3, 586952)
        aware = datetime(2025, 12, 21, 23, 8, 3, 586952, tzinfo=timezone.utc)
        normalized_naive = ensure_aware_utc(naive)
        normalized_aware = ensure_aware_utc(aware)
        if normalized_naive is None or normalized_aware is None:
            raise ValueError("normalize_none")
        _ = utc_now() - normalized_naive
        _ = utc_now() - normalized_aware
        parsed = parse_iso_timestamp("2025-12-21T23:08:03.586952+00:00")
        parsed = ensure_aware_utc(parsed)
        if parsed is None:
            raise ValueError("parse_failed")
        _ = utc_now() - parsed
    except Exception as exc:
        status = "FAIL"
        reason = str(exc)

    print("UI_TIME_MATH_START")
    print(_summary(status, reason))
    print("UI_TIME_MATH_END")
    return 0 if status == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
