You are my local patch bot. Output MUST be:
(1) minimal unified diff patch
(2) exact files to edit
(3) new/updated CI gate to prevent recurrence
(4) rollback plan
(5) PROOF commands to run locally and in CI

Constraints:
- Keep changes minimal and surgical.
- Do NOT weaken SIM-only / READ_ONLY / fail-closed.
- CI Gates is the only acceptance judge.
- Must be cross-platform (Windows + Linux). Headless is OK (no UI rendering required).

Task:
1) Add a fail-closed "import smoke" gate that runs:
   python -c "import tools.ui_app"
2) On failure, write traceback excerpt to:
   artifacts/import_smoke.txt
3) Wire this gate into the central gate entrypoint (scripts/ci_gates.sh or equivalent).
4) Add to PR gate/verifier if your project uses verify_prXX_gate.py pattern.

PROOF requirements:
- Show ./scripts/ci_gates.sh PASS on a clean repo.
- Demonstrate forced failure (e.g., temporarily break the import) makes the gate FAIL and produces artifacts/import_smoke.txt.

Rollback:
- Revert the PR. No schema changes.
