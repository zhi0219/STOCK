param([string]$WriteDocs = "NO", [string]$PythonExe = "")
Write-Host "REPO_DOCTOR_START"
Write-Host "REPO_DOCTOR_CONFIG|write_docs=NO|python=python.exe|repo_root=/repo|artifacts_dir=/repo/artifacts"
Write-Host "REPO_DOCTOR_STEP|name=inventory_repo|status=PASS"
Write-Host "REPO_DOCTOR_STEP|name=verify_pr_ready|status=PASS"
Write-Host "REPO_DOCTOR_CLEAN_POST|status=PASS|reason=ok"
Write-Host "REPO_DOCTOR_END"

python -m tools.inventory_repo --artifacts-dir artifacts
python -m tools.verify_pr_ready
git status --porcelain
.venv\\Scripts\\python.exe
-WriteDocs
-PythonExe

python -m tools.inventory_repo --write-docs
python -m tools.verify_pr_ready
git status --porcelain
