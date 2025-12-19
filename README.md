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
```

预期输出：
- `verify_smoke` 尾部打印解释器/依赖版本，并以 `PASS: smoke verified ...` 结束。
- `verify_cooldown` 会打印 `ALERTS_START ... cooldown=300s`，随后首个 MOVE 行，最后以 `PASS: cooldown verified ...` 收尾。

提示：不要把 `config.yaml` 的 YAML 片段当成 PowerShell 命令去敲；验收脚本会自动临时调整配置并在退出时还原。

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

### Risks / Assumptions
- 选择“最新 events 文件”依赖 `events_YYYY-MM-DD.jsonl` 的 UTC 命名模式，如果手工改名需自行注意。
- 当 `zoneinfo` 不可用或本地时区获取失败时，`ts_local` 会回退为本地系统时间（无时区信息），不会阻断主流程。
