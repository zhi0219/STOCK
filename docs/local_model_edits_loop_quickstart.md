# Local Model Edits Loop (Windows) — Quickstart

This repo supports a strict, fail-closed workflow:

local model output -> strict JSON extraction -> payload validation -> apply_edits (allowlist) -> evidence artifacts -> CI Gates judge.

## 1) Preconditions
- Repo root: C:\DONE\MONEY\STOCK
- venv python: .\.venv\Scripts\python.exe
- Ollama installed + model available (e.g., qwen2.5-coder:7b-instruct)

## 2) Run (Dry Run first)
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts\run_local_model_edits_v1.ps1 -RepoRoot C:\DONE\MONEY\STOCK -Model qwen2.5-coder:7b-instruct -PromptPath <PROMPT_FILE> -ArtifactsDir C:\DONE\MONEY\STOCK\artifacts -DryRun

Expected: RUN_LOCAL_MODEL_SUMMARY|status=PASS ... and APPLY_EDITS_SUMMARY|status=PASS|dry_run=true ...

## 3) Apply (only on a feature branch)
Re-run without -DryRun, then run:
- python -m tools.verify_foundation --artifacts-dir artifacts
- python -m tools.verify_consistency --artifacts-dir artifacts

## 4) Where to look when it fails
- artifacts/ollama_raw_*.txt
- artifacts/verify_edits_payload.txt + .json
- artifacts/apply_edits_result.json
- artifacts/run_local_model_summary_*.txt