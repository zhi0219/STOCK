Write-Host "REPO_DOCTOR_START"
Write-Host "REPO_DOCTOR_STEP|name=inventory_repo|status=PASS"
Write-Host "REPO_DOCTOR_END"

python -m tools.inventory_repo --write-docs
python -m tools.verify_pr_ready
git status --porcelain
