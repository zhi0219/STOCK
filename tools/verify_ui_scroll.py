from __future__ import annotations

import sys
import tkinter as tk
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.ui_scroll import VerticalScrolledFrame


def _display_available() -> tuple[bool, str]:
    try:
        root = tk.Tk()
        root.withdraw()
        root.update()
        root.destroy()
        return True, ""
    except Exception as exc:  # pragma: no cover - headless
        return False, str(exc)


def main() -> int:
    print("UI_SCROLL_START")
    display_ok, detail = _display_available()
    if not display_ok:
        summary = "|".join(
            [
                "UI_SCROLL_SUMMARY",
                "status=SKIP",
                "degraded=1",
                "reason=ui_display_unavailable",
                f"detail={detail or 'unknown'}",
            ]
        )
        print(summary)
        print("UI_SCROLL_END")
        print(summary)
        return 0

    root = tk.Tk()
    try:
        root.withdraw()
        container = VerticalScrolledFrame(root)
        container.pack(fill=tk.BOTH, expand=True)
        tk.Label(container.interior, text="Scroll check").pack()
        root.update_idletasks()
    finally:
        root.destroy()

    summary = "|".join(
        [
            "UI_SCROLL_SUMMARY",
            "status=PASS",
            "degraded=0",
            "reason=rendered",
            "detail=ok",
        ]
    )
    print(summary)
    print("UI_SCROLL_END")
    print(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
