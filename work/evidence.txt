You are my local patch bot.

OUTPUT CONTRACT:
[PATCH]
(unified diff, no code fences)
[/PATCH]

Task:
- Update scripts/patch_wrapper_v1.ps1 so that in APPLY mode it FAILS if current branch is "main".
- The failure must be fail-closed with a clear single-line marker:
  WRAP_SUMMARY|status=FAIL|reason=refuse_apply_on_main|next=git checkout -b <branch>
- DRY_RUN should still work on main (only APPLY is blocked).
- Keep changes minimal.

Do not weaken SIM-only/READ_ONLY/fail-closed.
