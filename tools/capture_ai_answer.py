from __future__ import annotations

import argparse
import importlib
import importlib.util
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.stdio_utf8 import configure_stdio_utf8

_yaml_spec = importlib.util.find_spec("yaml")
yaml = importlib.import_module("yaml") if _yaml_spec else None

CONFIG_PATH = ROOT / "config.yaml"
DEFAULT_OUT_DIR = ROOT / "qa_answers"

try:  # pragma: no cover - timezone fallback
    from zoneinfo import ZoneInfo

    ET_TZ = ZoneInfo("America/New_York")
except Exception:  # pragma: no cover - fallback
    ET_TZ = timezone(timedelta(hours=-5))


def load_config() -> Dict[str, Any]:
    if yaml is None:
        print("[WARN] PyYAML not available; using empty config", file=sys.stderr)
        return {}
    if not CONFIG_PATH.exists():
        return {}
    with CONFIG_PATH.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def get_logs_dir(cfg: Dict[str, Any]) -> Path:
    logging_cfg = cfg.get("logging", {}) or {}
    return ROOT / str(logging_cfg.get("log_dir", "./Logs"))


def _safe_mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except FileNotFoundError:
        return 0.0


def find_events_file(logs_dir: Path) -> Path:
    candidates = list(logs_dir.glob("events_*.jsonl"))
    if candidates:
        candidates.sort(key=lambda p: (_safe_mtime(p), p.name))
        return candidates[-1]
    legacy = logs_dir / "events.jsonl"
    if legacy.exists():
        return legacy
    today_name = datetime.now(timezone.utc).strftime("events_%Y-%m-%d.jsonl")
    return logs_dir / today_name


def _et_isoformat(dt: datetime) -> str:
    try:
        return dt.astimezone(ET_TZ).isoformat()
    except Exception:
        return dt.isoformat()


def _quality_checks(answer_text: str) -> Dict[str, Any]:
    citations = re.findall(r"\[evidence:\s*[^\]]+\]", answer_text, flags=re.IGNORECASE)
    has_citations = len(citations) >= 2

    trade_keywords = [
        "buy",
        "sell",
        "加仓",
        "买入",
        "卖出",
        "目标价",
        "target price",
        "仓位",
    ]
    lowered = answer_text.lower()
    has_trade_advice = any(keyword.lower() in lowered for keyword in trade_keywords)

    section_keywords = {
        "结论要点": "结论要点",
        "硬事实": "硬事实",
        "主流一句": "主流一句",
        "反方一句": "反方一句",
        "风险提醒": "风险提醒",
    }
    sections_present: List[str] = []
    for label, keyword in section_keywords.items():
        if keyword in answer_text:
            sections_present.append(label)

    return {
        "has_citations": has_citations,
        "has_trade_advice": has_trade_advice,
        "sections_present": sections_present,
    }


def _save_answer(answer_text: str, out_dir: Path) -> Path:
    today_dir = out_dir
    today_dir.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc).astimezone()
    timestamp = now.strftime("%Y%m%dT%H%M%S")
    answer_path = today_dir / f"{timestamp}_answer.md"
    answer_path.write_text(answer_text.rstrip() + "\n", encoding="utf-8")
    return answer_path


def _append_event(events_path: Path, event: Dict[str, Any]) -> None:
    events_path.parent.mkdir(parents=True, exist_ok=True)
    with events_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")


def build_event(
    *,
    packet_path: Path,
    answer_path: Path,
    answer_text: str,
    quality: Dict[str, Any],
) -> Dict[str, Any]:
    now = datetime.now(timezone.utc)
    message = answer_text.replace("\n", " ").strip()
    if len(message) > 120:
        message = message[:120] + "..."

    return {
        "event_type": "AI_ANSWER",
        "symbol": "__GLOBAL__",
        "severity": "low",
        "message": message,
        "metrics": {
            "packet_path": str(packet_path),
            "answer_path": str(answer_path),
            "has_citations": bool(quality["has_citations"]),
            "has_trade_advice": bool(quality["has_trade_advice"]),
            "sections_present": list(quality.get("sections_present", [])),
        },
        "source": "CHATGPT_MANUAL",
        "ts_utc": now.isoformat(),
        "ts_et": _et_isoformat(now),
    }


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Capture ChatGPT answer into logs")
    parser.add_argument("--packet", required=True, type=Path, help="Path to QA packet markdown")
    parser.add_argument("--answer-file", type=Path, help="Path to answer text file")
    parser.add_argument("--answer-text", type=str, help="Answer text content")
    parser.add_argument("--out-dir", type=Path, help="Output directory for saved answers")
    parser.add_argument("--strict", action="store_true", help="Fail on trade advice or missing citations")
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    configure_stdio_utf8()

    args = parse_args(argv)

    if not args.packet.exists():
        print(f"[ERROR] Packet not found: {args.packet}", file=sys.stderr)
        return 1

    if bool(args.answer_file) == bool(args.answer_text):
        print("[ERROR] Provide exactly one of --answer-file or --answer-text", file=sys.stderr)
        return 1

    if args.answer_file:
        if not args.answer_file.exists():
            print(f"[ERROR] Answer file not found: {args.answer_file}", file=sys.stderr)
            return 1
        answer_text = args.answer_file.read_text(encoding="utf-8")
    else:
        answer_text = str(args.answer_text)

    out_dir = args.out_dir or (DEFAULT_OUT_DIR / datetime.now(timezone.utc).strftime("%Y-%m-%d"))
    answer_path = _save_answer(answer_text, out_dir)

    quality = _quality_checks(answer_text)

    cfg = load_config()
    logs_dir = get_logs_dir(cfg)
    events_path = find_events_file(logs_dir)

    event = build_event(packet_path=args.packet, answer_path=answer_path, answer_text=answer_text, quality=quality)
    _append_event(events_path, event)

    quality_status = []
    exit_code = 0

    if quality["has_trade_advice"]:
        quality_status.append("FAIL-QUALITY: trade advice detected")
    if not quality["has_citations"]:
        quality_status.append("WARN: missing citations (need >=2 evidence markers)")

    if args.strict and (quality["has_trade_advice"] or not quality["has_citations"]):
        exit_code = 2

    sections = ", ".join(quality["sections_present"]) if quality["sections_present"] else "none"
    print(f"Saved answer to: {answer_path}")
    print(f"Appended event to: {events_path}")
    print(f"Quality: citations={quality['has_citations']} trade_advice={quality['has_trade_advice']} sections={sections}")
    if quality_status:
        for msg in quality_status:
            print(msg, file=sys.stderr)
    else:
        print("Quality checks passed", file=sys.stderr)

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
