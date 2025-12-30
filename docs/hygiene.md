# Repo Hygiene

## Policy registry layout

- **Tracked seed:** `Data/policy_registry.seed.json`
- **Runtime registry:** `Logs/runtime/policy_registry.json` (ignored)

On startup, the runtime registry is created from the seed if missing. This keeps runtime mutations out of the tracked working tree while preserving a deterministic baseline.

## Repo hygiene scan and fix

Run a scan:

```
python -m tools.repo_hygiene scan
```

Run a safe fix (restores tracked runtime artifacts and tracked seed files, removes only untracked runtime files under known runtime directories):

```
python -m tools.repo_hygiene fix --mode safe
```

Run a runtime-only safe action (stashes or discards only runtime artifacts when no code changes are present):

```
python -m tools.runtime_hygiene scan
python -m tools.runtime_hygiene fix --mode stash
python -m tools.runtime_hygiene fix --mode discard
```

Artifacts:

- `artifacts/runtime_hygiene_report.json`
- `artifacts/runtime_hygiene_result.json`

Action Center (SIM-only) also exposes **Fix Git Red (Safe)** (shown as **Fix All (Safe)** in the UI) and writes:

- `artifacts/git_hygiene_fix_plan.json`
- `artifacts/git_hygiene_fix_result.json`

Aggressive cleanup (optional) deletes only `Logs/runtime/` and `artifacts/` and requires an explicit confirmation token:

```
python -m tools.repo_hygiene fix --mode aggressive --i-know-what-im-doing --confirm DELETE-RUNTIME
```

## Optional Git hooks

Hooks are optional and safe. Enable once per clone:

```
./scripts/enable_githooks.sh
```

On Windows:

```
.\scripts\enable_githooks.ps1
```

These hooks run the safe hygiene fix after `git checkout` and `git merge`.

## Expected behavior

After SIM runs or training, `git status` should stay clean for tracked files. Runtime artifacts should live under ignored runtime directories and be classified as `RUNTIME_ARTIFACT` by the hygiene scan.
