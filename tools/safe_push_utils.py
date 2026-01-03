from __future__ import annotations

import re
from typing import Optional

CONSISTENCY_STATUS_RE = re.compile(r"^CONSISTENCY_SUMMARY\|status=([A-Z]+)", re.MULTILINE)


def parse_consistency_status(text: str) -> Optional[str]:
    match = CONSISTENCY_STATUS_RE.search(text)
    if not match:
        return None
    return match.group(1)


def is_consistency_status_ok(status: Optional[str]) -> bool:
    return status in {"PASS", "DEGRADED"}
