from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

import streamlit as st

from tools.dashboard_model import compute_risk_hud

ROOT = Path(__file__).resolve().parent.parent
LOGS_DIR = ROOT / "Logs"
SUPERVISOR_SCRIPT = ROOT / "tools" / "supervisor.py"
QA_FLOW_SCRIPT = ROOT / "tools" / "qa_flow.py"
CAPTURE_ANSWER_SCRIPT = ROOT / "tools" / "capture_ai_answer.py"
VERIFY_SCRIPTS = [
    "verify_smoke.py",
    "verify_e2e_qa_loop.py",
    "verify_ui_actions.py",
]


def _run_command(args: List[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=ROOT, capture_output=True, text=True)


def _latest_events_file() -> Path | None:
    candidates = sorted(LOGS_DIR.glob("events_*.jsonl"))
    return candidates[-1] if candidates else None


def _load_events(path: Path | None) -> List[Dict[str, Any]]:
    if not path or not path.exists():
        return []
    events: List[Dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                try:
                    events.append(json.loads(line))
                except Exception:
                    continue
    except Exception:
        return []
    return events


def _status_indicator(status: Dict[str, Any]) -> str:
    quotes = status.get("quotes_running")
    alerts = status.get("alerts_running")
    running = status.get("running")
    parts: List[str] = []
    if quotes is not None:
        parts.append(f"quotes {'运行中' if quotes else '已停止'}")
    if alerts is not None:
        parts.append(f"alerts {'运行中' if alerts else '已停止'}")
    if running is not None:
        parts.append(f"系统 {'运行中' if running else '未运行'}")
    return "，".join(parts) if parts else "状态未知"


def _load_status() -> Dict[str, Any]:
    path = LOGS_DIR / "status.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _display_command_result(proc: subprocess.CompletedProcess[str]) -> None:
    st.code(
        f"Command: {' '.join(proc.args if isinstance(proc.args, list) else [str(proc.args)])}\n"
        f"Exit code: {proc.returncode}\n--- stdout ---\n{(proc.stdout or '').strip()}\n--- stderr ---\n{(proc.stderr or '').strip()}",
        language="text",
    )


def render_header() -> None:
    st.title("STOCK Streamlit UI (只读)")
    st.caption("严禁交易/下单/资金相关操作。")


render_header()

col1, col2 = st.columns(2)
if col1.button("Start supervisor"):
    proc = _run_command([sys.executable, str(SUPERVISOR_SCRIPT), "start"])
    _display_command_result(proc)
if col2.button("Stop supervisor"):
    proc = _run_command([sys.executable, str(SUPERVISOR_SCRIPT), "stop"])
    _display_command_result(proc)

risk_status = _load_status()
events_path = _latest_events_file()
events = _load_events(events_path)
risk_hud = compute_risk_hud(LOGS_DIR, risk_status, events)

st.subheader("风险 HUD")
st.json(risk_hud)

st.subheader("状态")
st.write(_status_indicator(risk_status))
if risk_status:
    st.json(risk_status)

st.subheader("事件")
if not events:
    st.info("暂无事件文件")
else:
    symbols = sorted({ev.get("symbol") or "-" for ev in events})
    types = sorted({ev.get("event_type") or "-" for ev in events})
    severity_levels = sorted({ev.get("severity") or "-" for ev in events})

    symbol = st.selectbox("筛选符号", options=["(全部)"] + symbols)
    etype = st.selectbox("筛选类型", options=["(全部)"] + types)
    severity = st.selectbox("筛选级别", options=["(全部)"] + severity_levels)

    filtered = []
    for ev in events:
        if symbol != "(全部)" and ev.get("symbol") != symbol:
            continue
        if etype != "(全部)" and ev.get("event_type") != etype:
            continue
        if severity != "(全部)" and ev.get("severity") != severity:
            continue
        filtered.append(ev)

    st.write(f"共 {len(filtered)} 条记录 (源: {events_path.name if events_path else 'N/A'})")
    st.dataframe(filtered)

st.subheader("AI Q&A")
question = st.text_input("输入问题")
if st.button("生成 Packet") and question:
    proc = _run_command([sys.executable, str(QA_FLOW_SCRIPT), "--question", question])
    _display_command_result(proc)

answer_text = st.text_area("粘贴回答")
strict_mode = st.checkbox("Strict 模式 (拒绝交易建议)")
if st.button("导入回答") and answer_text:
    cmd = [
        sys.executable,
        str(CAPTURE_ANSWER_SCRIPT),
        "--answer-text",
        answer_text,
    ]
    packet_hint = st.text_input("若需要，填写 packet 路径", value="")
    if packet_hint:
        cmd.extend(["--packet", packet_hint])
    if strict_mode:
        cmd.append("--strict")
    proc = _run_command(cmd)
    _display_command_result(proc)

st.subheader("Verify")
for script in VERIFY_SCRIPTS:
    if st.button(f"运行 {script}"):
        proc = _run_command([sys.executable, str(ROOT / 'tools' / script)])
        _display_command_result(proc)

st.caption("提示：所有命令均使用 sys.executable，cwd 固定为仓库根目录。")
