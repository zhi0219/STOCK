# STOCK utilities

## Environment setup

### Windows (PowerShell)
1. Create and activate a venv (if you want to reuse system packages under restricted networks, add `--system-site-packages`):
   ```powershell
   python -m venv .\.venv
   .\.venv\Scripts\Activate.ps1
   ```
2. Install pinned dependencies (only when creating a fresh venv and network allows):
   ```powershell
   .\.venv\Scripts\python.exe -m pip install -r requirements.txt
   ```

推荐闸门（复制即用）：

```
.\.venv\Scripts\python.exe .\tools\verify_consistency.py
```

PR11 gate（PowerShell 复制即用）：

```
.\.venv\Scripts\python.exe .\tools\verify_pr11_gate.py
# 可选：单独审核既有训练产物（SIM-only，默认扫描 Logs/train_runs）
.\.venv\Scripts\python.exe .\tools\progress_judge.py --runs-root .\Logs\train_runs
```

## PowerShell 状态一眼读懂

- 输出中包含 `*_SUMMARY` / `*_HEADER` 这类 marker 行，直接看这些行即可判断 PASS / DEGRADED / FAIL。
- 推荐复制即用命令（Windows / PowerShell）：
  ```powershell
  .\.venv\Scripts\python.exe .\tools\verify_consistency.py
  .\.venv\Scripts\python.exe .\tools\verify_repo_hygiene.py
  ```
- 不要把 `git clean -fd` 当成日常手段；训练/验收产物默认落在 `Logs/train_runs/` 等安全目录，由内置的 retention sweep 处理。

## git status 出现红色未跟踪运行时文件时怎么办？

1. 先跑卫生闸门获取高亮摘要：
   ```powershell
   .\.venv\Scripts\python.exe .\tools\verify_repo_hygiene.py
   ```
   顶部/底部的 `REPO_HYGIENE_SUMMARY` 行会标注缺失的 ignore 规则、未跟踪的运行时文件、以及需要人工处理的路径。
2. 若卫生闸门提示缺少规则或有 runtime 路径未被忽略，按提示更新 `.gitignore` 或移动文件到已忽略的根（例如 `Logs/train_service/` 或 `Logs/train_runs/`）。
3. 如需人工清理运行时产物（可选），仅在受控目录下删除，例如：
   ```powershell
   Remove-Item -Recurse -Force .\Logs\train_service -ErrorAction SilentlyContinue
   Remove-Item -Recurse -Force .\Logs\train_runs -ErrorAction SilentlyContinue
   ```
   不建议在仓库根目录使用 `git clean`；保留期/归档策略仍按守护脚本内置逻辑执行，Reports/ 属于运行时报告默认被 gitignore（可本地留存审计、不需提交）。

## 本机真实基线（1条命令）

```powershell
cd %USERPROFILE%\Desktop\STOCK
.\.venv\Scripts\python.exe .\tools\verify_foundation.py
```

运行后关注 `FOUNDATION_SUMMARY` 行：

- 缺少可选依赖或受限环境跳过部分检查时，预期 `FOUNDATION_SUMMARY|status=PASS|degraded=1|failed=0`（退出码 0，会附带缺失项或跳过原因）。
- 依赖齐全且全部通过时，预期 `FOUNDATION_SUMMARY|status=PASS|degraded=0|failed=0`。

## PR7 Windows 复制即用门禁（PowerShell）
合并前至少顺序跑完下列 5~6 条，预期为 `PASS`；若缺少可选依赖导致 `DEGRADED` 可接受，但 `FAIL` 必须为 0：

```powershell
.\.venv\Scripts\python.exe .\tools\verify_foundation.py
.\.venv\Scripts\python.exe .\tools\verify_train_daemon_safety.py
.\.venv\Scripts\python.exe .\tools\verify_train_retention.py
.\.venv\Scripts\python.exe .\tools\verify_train_service_safety.py
.\.venv\Scripts\python.exe .\tools\verify_train_semantic_loop.py
# 可选：一致性闸门（推荐）
.\.venv\Scripts\python.exe .\tools\verify_consistency.py
```

如需同时落盘日志，可选用 PowerShell tee 版本（最朴素版命令如上，复制即用）：

```powershell
.\.venv\Scripts\python.exe .\tools\verify_foundation.py *>&1 | Tee-Object -FilePath .\Logs\verify_foundation.log
.\.venv\Scripts\python.exe .\tools\verify_train_daemon_safety.py *>&1 | Tee-Object -FilePath .\Logs\verify_train_daemon_safety.log
.\.venv\Scripts\python.exe .\tools\verify_train_retention.py *>&1 | Tee-Object -FilePath .\Logs\verify_train_retention.log
.\.venv\Scripts\python.exe .\tools\verify_train_service_safety.py *>&1 | Tee-Object -FilePath .\Logs\verify_train_service_safety.log
.\.venv\Scripts\python.exe .\tools\verify_train_semantic_loop.py *>&1 | Tee-Object -FilePath .\Logs\verify_train_semantic_loop.log
.\.venv\Scripts\python.exe .\tools\verify_consistency.py *>&1 | Tee-Object -FilePath .\Logs\verify_consistency.log
```

### macOS / Linux (bash)
1. Create and activate a venv:
   ```bash
   python -m venv .venv
   source .venv/bin/activate
   ```
2. Install pinned dependencies:
   ```bash
   python -m pip install -r requirements.txt
   ```

## 零理解验收（Windows / PowerShell）
只需在 PowerShell 里顺序执行下面几行命令，无需手工改 YAML：

```powershell
cd $HOME\Desktop\STOCK
# 如果已有可用 venv，可直接运行验收脚本；若首次创建且网络允许，可选运行：
# python -m venv .\.venv            # 若需复用系统依赖，可改为: python -m venv .\.venv --system-site-packages
# .\.venv\Scripts\python.exe -m pip install -r requirements.txt  # 可选，网络受限环境可跳过
.\.venv\Scripts\python.exe .\tools\verify_smoke.py
.\.venv\Scripts\python.exe .\tools\verify_cooldown.py
.\.venv\Scripts\python.exe -m py_compile .\tools\sim_replay.py .\tools\verify_sim_replay.py .\tools\verify_no_lookahead_sim.py .\tools\sim_tournament.py .\tools\verify_sim_tournament.py .\tools\policy_candidate.py .\tools\verify_policy_promotion.py .\tools\verify_policy_lifecycle.py
.\.venv\Scripts\python.exe .\tools\verify_sim_replay.py
.\.venv\Scripts\python.exe .\tools\verify_no_lookahead_sim.py
.\.venv\Scripts\python.exe .\tools\verify_sim_tournament.py
.\.venv\Scripts\python.exe .\tools\verify_policy_lifecycle.py
.\.venv\Scripts\python.exe .\tools\verify_train_daemon_safety.py
.\.venv\Scripts\python.exe .\tools\verify_train_semantic_loop.py
.\.venv\Scripts\python.exe .\tools\train_daemon.py --help
.\.venv\Scripts\python.exe .\tools\verify_consistency.py
```

## Replay 倍速训练场（SIM-only）
- 使用历史 quotes 回放 SIM 自动驾驶（仅日志、无真实交易能力）：
  ```powershell
  .\.venv\Scripts\python.exe .\tools\sim_replay.py --input .\Data\quotes.csv --max-steps 500 --speed 0
  ```
- 核心验收（都会自动清理临时文件）：
  ```powershell
  .\.venv\Scripts\python.exe -m py_compile .\tools\sim_replay.py .\tools\verify_sim_replay.py .\tools\verify_no_lookahead_sim.py .\tools\policy_candidate.py .\tools\verify_policy_promotion.py .\tools\verify_policy_lifecycle.py
  .\.venv\Scripts\python.exe .\tools\verify_sim_replay.py
  .\.venv\Scripts\python.exe .\tools\verify_no_lookahead_sim.py
  ```
- SIM-only 挂机训练（夜间跑满 8 小时预算，严格预算闸门+kill switch，带产物保留策略）：
  ```powershell
  .\.venv\Scripts\python.exe .\tools\train_daemon.py --nightly --max-runtime-seconds 28800 --retain-days 7 --retain-latest-n 50 --max-total-train-runs-mb 5000
  ```
  - 运行产物默认落在 `Logs/train_runs/`，遵循保留/轮换策略；产物文件被 gitignore，避免污染 git。
  - 不建议用 `git clean -fd` 清理日志，推荐使用内置 retention 参数（可选 `--retention-dry-run` 查看计划）。
  - 默认只写入 `PROMOTION_DECISION` 事件，不会自动晋升；如需自动晋升必须显式传入 `--auto-promote`，仍会经过额外闸门。
  - 守护/候选/决策报告：`Logs/tournament_runs/tournament_report_*`、`Logs/policy_candidate.json` 与 `Logs/events_train.jsonl` 中的路径可回溯提案/候选/决策来源。
  - 一键停机：创建 `config.yaml` 里 `risk_guards.kill_switch_path` 指向的文件（默认 `Data/KILL_SWITCH`）即可强制停机。

## 像应用一样一键启动

- 打开 UI：

```
.\.venv\Scripts\python.exe .\tools\ui_app.py
```

- 如果提示 KILL_SWITCH present：在弹窗点 Remove & Start 即可恢复运行（或保留文件以保持紧急停机）。

- 可选 Streamlit UI（更直观、只读）：

```
.\.venv\Scripts\python.exe -m pip install -r requirements-ui.txt
.\.venv\Scripts\python.exe -m streamlit run .\tools\ui_streamlit.py
```

### UI 内完成零成本 AI 问答闭环
1. 打开 UI 后，找到 “AI Q&A” 区块。
2. 在 Question 输入框输入问题，点击 **Generate Q&A Packet**，UI 会调用 `qa_flow` 并显示生成的 `packet` / `evidence_pack` 路径。
   - 若仍看到 “Packet path not detected”，请将 Verify 页签的完整 stdout/stderr 贴出；UI 已使用 UTF-8 捕获修复了路径丢失问题。
3. 点击 **Copy Packet to Clipboard**，将内容粘贴到 ChatGPT（无需命令行）。
4. 把 ChatGPT 的回答粘贴回 UI 的 Answer 文本框，必要时勾选 Strict mode（拒绝含交易建议的回答），然后点击 **Import Answer**。UI 会落盘回答并追加 `AI_ANSWER` 事件；若严格模式拦截会弹出提示让你让 ChatGPT 重写。
5. **Open output folder** 按钮可直接打开输出目录，方便查看生成的包/回答文件。命令行仍可作为备选：`qa_flow`/`capture_ai_answer` 可独立运行。

- CLI 启停：

```
.\.venv\Scripts\python.exe .\tools\supervisor.py start
.\.venv\Scripts\python.exe .\tools\supervisor.py stop
```

- 一键验收：

```
.\.venv\Scripts\python.exe .\tools\verify_supervisor.py
```

预期输出：
- `verify_smoke` 尾部打印解释器/依赖版本，并以 `PASS: smoke verified ...` 结束。
- `verify_cooldown` 会打印 `ALERTS_START ... cooldown=300s`，随后首个 MOVE 行，最后以 `PASS: cooldown verified ...` 收尾。

 提示：不要把 `config.yaml` 的 YAML 片段当成 PowerShell 命令去敲；验收脚本会自动临时调整配置并在退出时还原。

## How to use Progress (SIM-only)

- 构建/刷新进度索引（原子写入 `Logs\\train_runs\\progress_index.json`）：

  ```powershell
  .\\.venv\\Scripts\\python.exe .\\tools\\progress_index.py
  ```

- 打开 UI 并切换到 **Progress (SIM-only)** 页签：

  ```powershell
  .\\.venv\\Scripts\\python.exe .\\tools\\ui_app.py
  ```

  页签内可点击 **Generate index** / **Refresh view**，以及直接打开 run 目录、`summary.md` 或 `equity_curve.csv`。

- 产物位置：最新一条训练通常在 `Logs\\train_runs\\<日期>\\<run_id>\\`，包含 `summary.md`、`equity_curve.csv`、`orders_sim.jsonl` 等产物（UI 会用 holdings 预览和 equity 曲线渲染）。

- 一键停机：创建 `config.yaml` 的 `risk_guards.kill_switch_path` 指向的文件（默认 `Data\\KILL_SWITCH`）即可停止训练/服务进程；删除后可恢复运行。

## PR12 Progress Panel: interpreting statuses + Truthful XP

- 核心产物：每个 run 目录会写入 `summary.json` 和 `holdings.json`，UI 优先读取 JSON（`summary.md` 仍用于人工阅读）。
- Status 列含义：
  - `OK`：summary/holdings/equity 曲线均可读，字段齐全。
  - `MISSING_FILES`：缺少 `summary.json` / `holdings.json` / `equity_curve.csv` 中的至少一个。
  - `PARSE_ERROR`：文件存在但无法解析/字段缺失（保持 fail-closed，不猜测）。
  - `IN_PROGRESS`：检测到 `.tmp` 或 0 字节文件，说明仍在写入。
- Missing reason：列出缺失或解析失败的具体原因（例如 `summary_json_missing;holdings_json_missing`）。
- Truthful XP/Level：仅当 run 目录内存在 judge 输出（如 `judge_summary.json` 且包含 `xp`/`level`）时显示；否则固定显示 “No judge data”。

## Deterministic MOVE self-test
Run a synthetic injection so `alerts.py` emits a MOVE alert without waiting for real market moves.

1. Compile-time check:
   ```powershell
   .\.venv\Scripts\python.exe -m py_compile .\main.py .\quotes.py .\alerts.py .\tools\inject_quote.py .\tools\verify_cooldown.py .\tools\verify_smoke.py
   ```
2. Inject a deterministic price jump (defaults: symbol AAPL, +1.0%):
   ```powershell
   .\.venv\Scripts\python.exe .\tools\inject_quote.py --symbol AAPL --delta-pct 1.0
   ```
3. Trigger alerts using the injected rows:
   ```powershell
   .\.venv\Scripts\python.exe .\alerts.py
   ```
   Expected: console prints at least one `🚨 MOVE ...` line, and entries are appended to `.\Logs\alerts.log` and `.\Data\learning_cards.md`.
4. Cleanup to remove only the injected rows:
   ```powershell
   .\.venv\Scripts\python.exe .\tools\inject_quote.py --cleanup
   ```

The injector writes to `.\Data\quotes.csv` with `source=SELF_TEST_INJECT` so the synthetic rows can be removed safely after testing.

## Stability features
- Compile-time check:
  ```powershell
  .\.venv\Scripts\python.exe -m py_compile .\main.py .\quotes.py .\alerts.py .\tools\inject_quote.py .\tools\tail_events.py
  ```
  Expected: command exits quietly if the files are syntactically valid.
- Cooldown / dedupe demo:
  ```powershell
  .\.venv\Scripts\python.exe .\tools\inject_quote.py --symbol AAPL --delta-pct 1.0
  .\.venv\Scripts\python.exe .\tools\inject_quote.py --symbol AAPL --delta-pct 1.0
  .\.venv\Scripts\python.exe .\alerts.py
  ```
  Expected: the first run emits a `MOVE` line, the second quick repeat is suppressed during the cooldown window (check the tail of `.\Logs\alerts.log`).
- Kill switch demo:
  ```powershell
  New-Item -ItemType File .\Data\KILL_SWITCH
  .\.venv\Scripts\python.exe .\alerts.py
  .\.venv\Scripts\python.exe .\quotes.py
  Remove-Item .\Data\KILL_SWITCH
  ```
  Expected: alerts/quotes notice the kill switch file, print `KILL_SWITCH detected at ... exiting`, and stop cleanly until the file is removed.
- Debug mode demo:
  ```powershell
  # in config.yaml
  # alerts:
  #   debug: true
  .\.venv\Scripts\python.exe .\alerts.py
  ```
  Expected: each polling cycle prints compact `DEBUG` lines showing prev/now/move%/threshold/flat_count and file-health stats.

## Events / Status 观察与排障
- 启动 alerts（会立刻写入一条 `ALERTS_START` 事件行，以及 `Logs\\status.json` 快照）：
  ```powershell
  .\.venv\Scripts\python.exe .\alerts.py
  ```
- 查看/轮转 events：事件文件按 **UTC 日期** 分片，位于 `Logs\\events_YYYY-MM-DD.jsonl`；每天会自动写入当天文件，选择“最新文件”依赖这个 UTC 命名规则。
- tail 最新 events（支持过滤，容错坏行）：
  ```powershell
  .\.venv\Scripts\python.exe .\tools\tail_events.py --tail 5
  .\.venv\Scripts\python.exe .\tools\tail_events.py --symbol AAPL --type MOVE --since-minutes 10
  ```
  预期：打印最新 events json 对象；如果存在坏行会在 stderr 提示 `[WARN] skipped ...` 但不中断。
- Kill switch（PowerShell）：创建/移除 `Data\\KILL_SWITCH` 可让 alerts/quotes 安全退出，事件日志也会记录 `KILL_SWITCH`：
  ```powershell
  New-Item -ItemType File .\Data\KILL_SWITCH
  Remove-Item .\Data\KILL_SWITCH
  ```
- 证据驱动简报（pip install 可选，不作为验收前置）：
  ```powershell
  .\.venv\Scripts\python.exe .\tools\brief_report.py --limit 50
  ```
- 自测简报生成（pip install 可选，不作为验收前置）：
  ```powershell
  .\.venv\Scripts\python.exe .\tools\verify_brief.py
  ```

## Dashboard（图形化）
- 启动 UI（含 Dashboard/Events 表格）：
  ```powershell
  .\.venv\Scripts\python.exe .\tools\ui_app.py
  ```
- 一键验收 dashboard 模型：
  ```powershell
  .\.venv\Scripts\python.exe .\tools\verify_dashboard.py
  ```

## PR13 Progress Gate (SIM-only)
- One-command proof gate (Windows venv required, expect `using_venv=1` in output):
  ```powershell
  .\.venv\Scripts\python.exe .\tools\verify_pr13_gate.py
  ```

## 零成本 AI 问答（复制即用）
- 生成证据包：
  ```powershell
  .\.venv\Scripts\python.exe .\tools\make_ai_packet.py --question "我想知道今天哪些事件最重要？为什么？"
  ```
- 把上述输出完整复制粘贴到 ChatGPT（无需 API、无需额外付费），按提示生成可审计回答。
- 一键验收：
  ```powershell
  .\.venv\Scripts\python.exe .\tools\verify_ai_packet.py
  ```

## 零成本 AI 闭环（复制即用）
- 1) 生成证据包（只读、零成本）：
  ```powershell
  .\.venv\Scripts\python.exe .\tools\make_ai_packet.py --question "今天需要关注什么？给出证据引用"
  ```
- 2) 将生成的包粘贴到 ChatGPT，按照 "REQUIRED OUTPUT FORMAT" 输出。
- 3) 保存回答并入库（可直接传文本，也可先保存到文件）：
  ```powershell
  # 直接传回答文本
  .\.venv\Scripts\python.exe .\tools\capture_ai_answer.py --packet .\qa_packets\2024-01-01\packet.md --answer-text "粘贴 ChatGPT 回答"

  # 如先保存为 answer.md，可用 --answer-file
  .\.venv\Scripts\python.exe .\tools\capture_ai_answer.py --packet .\qa_packets\2024-01-01\packet.md --answer-file .\answer.md --strict
  ```
- 4) 回放 AI 回答事件（可过滤 AI_ANSWER）：
  ```powershell
  .\.venv\Scripts\python.exe .\tools\replay_events.py --type AI_ANSWER --limit 20
  ```

## 回放 / 复盘（复制即用）
- 回放最近 60 分钟并输出统计（pip install 可选，不是前置条件）：
  ```powershell
  .\.venv\Scripts\python.exe .\tools\replay_events.py --since-minutes 60 --limit 50 --stats
  ```
- 可选：拉长窗口并生成学习卡（追加到 `Data\\learning_cards.md`）：
  ```powershell
  .\.venv\Scripts\python.exe .\tools\replay_events.py --since-minutes 1440 --limit 200 --stats --write-learning-card
  ```

## 解决字符限制：迷你证据包
- 生成迷你证据包（按关键词裁剪最近事件，自动限制输出长度）：
  ```powershell
  .\.venv\Scripts\python.exe .\tools\select_evidence.py --question "今天哪些事件最重要？为什么？" --since-minutes 1440 --limit 30 --max-chars 12000
  ```
- 一键验收（生成并自测截断逻辑）：
  ```powershell
  .\.venv\Scripts\python.exe .\tools\verify_select_evidence.py
  ```
  说明：`pip install -r requirements.txt` 依然可选，但不是验收前置条件。

## 零成本问答：一条命令工作流
- 运行：
  ```powershell
  .\.venv\Scripts\python.exe .\tools\qa_flow.py --question "今天最重要的事件是什么？为什么？"
  ```
- 一键端到端验收：
  ```powershell
  .\.venv\Scripts\python.exe .\tools\verify_e2e_qa_loop.py
  ```

### Risks / Assumptions
- 选择“最新 events 文件”依赖 `events_YYYY-MM-DD.jsonl` 的 UTC 命名模式，如果手工改名需自行注意。
- 当 `zoneinfo` 不可用或本地时区获取失败时，`ts_local` 会回退为本地系统时间（无时区信息），不会阻断主流程。
