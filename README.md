# STOCK utilities

## Deterministic MOVE self-test
Run a synthetic injection so `alerts.py` emits a MOVE alert without waiting for real market moves.

1. Compile-time check:
   ```powershell
   .\.venv\Scripts\python.exe -m py_compile .\main.py .\quotes.py .\alerts.py .\tools\inject_quote.py
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
  .\.venv\Scripts\python.exe -m py_compile .\main.py .\quotes.py .\alerts.py .\tools\inject_quote.py
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

## Cooldown 验收（零理解版）
不需要手改 YAML 或了解 PowerShell。执行一条命令即可验证 cooldown=300 是否生效：

```powershell
cd $HOME\Desktop\STOCK
.\.venv\Scripts\python.exe .\tools\verify_cooldown.py
```

脚本会自动：
- 清理 `.\Data\KILL_SWITCH`
- 临时将 `alerts.cooldown_seconds` 设置为 300
- 启动 `alerts.py` 并确认启动行打印 `cooldown=300`
- 在 30 秒内连续注入两次 `SELF_TEST_INJECT`（AAPL, +5%）
- 断言第一次出现 `MOVE`，第二次在 300 秒内不再出现同一 symbol 的 `MOVE`
- 清理注入行

预期输出示例：
```
PASS ✅ cooldown=300s
```

如果 FAIL，会附带原因（例如 `cooldown 仍为 60s`、`未捕捉到第一次 MOVE`、`第二次未抑制`），下一步建议检查 `.\Logs\alerts.log` 末尾或确认 `config.yaml` 是否可读。
