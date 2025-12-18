# STOCK utilities

## Environment setup

### Windows (PowerShell)
1. Create and activate a venv:
   ```powershell
   python -m venv .\.venv
   .\.venv\Scripts\Activate.ps1
   ```
2. Install pinned dependencies:
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

## Cooldown acceptance (zero-understanding version)
Run directly from PowerShell with the recommended venv interpreter; no YAML edits are required.

```powershell
cd $HOME\Desktop\STOCK
\.\.venv\Scripts\python.exe .\tools\verify_cooldown.py
```

Expected output (example):

```
[2024-01-01T00:00:00+00:00 | 2024-01-01T08:00:00+08:00 CST] ALERTS_START thr=1.0% poll=5s flat=5 stale=180s cooldown=300s debug=False quotes=...\Data\quotes.csv
PASS: cooldown verified (second MOVE suppressed within 300s)
```

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
  python -m py_compile .\main.py .\quotes.py .\alerts.py .\tools\inject_quote.py
  ```
  Expected: command exits quietly if the files are syntactically valid.
- Cooldown / dedupe demo:
  ```powershell
  python .\tools\inject_quote.py --symbol AAPL --delta-pct 1.0
  python .\tools\inject_quote.py --symbol AAPL --delta-pct 1.0
  python .\alerts.py
  ```
  Expected: the first run emits a `MOVE` line, the second quick repeat is suppressed during the cooldown window (check the tail of `.\Logs\alerts.log`).
- Kill switch demo:
  ```powershell
  New-Item -ItemType File .\Data\KILL_SWITCH
  python .\alerts.py
  python .\quotes.py
  Remove-Item .\Data\KILL_SWITCH
  ```
  Expected: alerts/quotes notice the kill switch file, print `KILL_SWITCH detected at ... exiting`, and stop cleanly until the file is removed.
- Debug mode demo:
  ```powershell
  # in config.yaml
  # alerts:
  #   debug: true
  python .\alerts.py
  ```
  Expected: each polling cycle prints compact `DEBUG` lines showing prev/now/move%/threshold/flat_count and file-health stats.
