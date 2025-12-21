from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import List, Tuple


ROOT = Path(__file__).resolve().parent.parent
TRAIN_RUNS_ROOT = ROOT / "Logs" / "train_runs"
MISSING_FIELD_TEXT = "字段缺失/版本差异"


@dataclass
class SummaryParseResult:
    latest_run_dir: Path | None
    summary_path: Path | None
    stop_reason: str
    net_change: str
    max_drawdown: str
    trades_count: str
    reject_reasons_top3: List[str]
    raw_preview: str
    warning: str | None = None


def find_latest_run_dir(runs_root: Path | str = TRAIN_RUNS_ROOT) -> Path | None:
    runs_root = Path(runs_root)
    if not runs_root.exists():
        return None
    candidates: List[Tuple[float, Path]] = []
    for run_dir in runs_root.glob("*/*"):
        if not run_dir.is_dir():
            continue
        try:
            mtime = run_dir.stat().st_mtime
        except OSError:
            continue
        candidates.append((mtime, run_dir))
    if not candidates:
        return None
    return sorted(candidates, key=lambda item: item[0])[-1][1]


def find_latest_summary_md(runs_root: Path | str = TRAIN_RUNS_ROOT) -> tuple[Path | None, Path | None]:
    runs_root = Path(runs_root)
    if not runs_root.exists():
        return None, None
    candidates: List[Tuple[float, Path]] = []
    for summary in runs_root.glob("**/summary.md"):
        try:
            mtime = summary.stat().st_mtime
        except OSError:
            continue
        candidates.append((mtime, summary))
    if not candidates:
        return None, None
    latest_summary = sorted(candidates, key=lambda item: item[0])[-1][1]
    return latest_summary.parent, latest_summary


def _extract_line_value(lines: List[str], prefix: str) -> str:
    for line in lines:
        if line.startswith(prefix):
            return line.split(prefix, 1)[1].strip()
    return MISSING_FIELD_TEXT


def _extract_rejection_reasons(lines: List[str]) -> List[str]:
    reasons: List[str] = []
    header = "## Rejection reasons"
    try:
        idx = next(i for i, line in enumerate(lines) if line.startswith(header))
    except StopIteration:
        return [MISSING_FIELD_TEXT]
    for line in lines[idx + 1 :]:
        if line.startswith("## ") and not line.startswith(header):
            break
        if line.startswith("- "):
            reasons.append(line[2:].strip())
        if len(reasons) >= 3:
            break
    return reasons or [MISSING_FIELD_TEXT]


def parse_summary_key_fields(summary_path: Path, preview_lines: int = 24) -> SummaryParseResult:
    warning = None
    raw_preview = ""
    lines: List[str] = []
    try:
        text = summary_path.read_text(encoding="utf-8")
        lines = [line.strip() for line in text.splitlines()]
        raw_preview = "\n".join(text.splitlines()[:preview_lines])
    except Exception as exc:
        warning = f"读取 summary 失败: {exc}"
        lines = []
        raw_preview = warning

    stop_reason = _extract_line_value(lines, "Stop reason: ") if lines else MISSING_FIELD_TEXT
    net_change = _extract_line_value(lines, "Net value change: ") if lines else MISSING_FIELD_TEXT
    max_drawdown = _extract_line_value(lines, "Max drawdown: ") if lines else MISSING_FIELD_TEXT
    trades_count = _extract_line_value(lines, "Trades executed: ") if lines else MISSING_FIELD_TEXT
    reject_reasons_top3 = _extract_rejection_reasons(lines) if lines else [MISSING_FIELD_TEXT]

    return SummaryParseResult(
        latest_run_dir=summary_path.parent,
        summary_path=summary_path,
        stop_reason=stop_reason,
        net_change=net_change,
        max_drawdown=max_drawdown,
        trades_count=trades_count,
        reject_reasons_top3=reject_reasons_top3,
        raw_preview=raw_preview,
        warning=warning,
    )

