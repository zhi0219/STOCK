import argparse
import json
import os
import platform
import subprocess
import sys
import traceback
from pathlib import Path
from datetime import datetime

def _write_text(p: Path, text: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text.replace("\r\n", "\n").replace("\r", "\n"), encoding="utf-8")

def _run_apply_edits(ps_cmd: list[str], repo: Path, edits_path: Path, artifacts_dir: Path, tag: str) -> tuple[int, str]:
    out = ""
    try:
        r = subprocess.run(
            ps_cmd + ["-ExecutionPolicy", "Bypass", "-File", str(repo / "scripts" / "apply_edits_v1.ps1"),
                      "-RepoRoot", str(repo), "-EditsPath", str(edits_path), "-DryRun"],
            capture_output=True, text=True, encoding="utf-8", errors="replace"
        )
        out = (r.stdout or "") + "\n" + (r.stderr or "")
        _write_text(artifacts_dir / f"redteam_{tag}.txt", out)
        return r.returncode, out
    except Exception as e:
        tb = traceback.format_exc()
        _write_text(artifacts_dir / f"redteam_{tag}.txt", tb)
        return 99, tb

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True)
    ap.add_argument("--artifacts-dir", required=True)
    args = ap.parse_args()

    repo = Path(args.repo).resolve()
    art = Path(args.artifacts_dir).resolve()
    art.mkdir(parents=True, exist_ok=True)
    err_file = art / "redteam.txt"

    print("REDTEAM_START")
    try:
        apply_ps1 = repo / "scripts" / "apply_edits_v1.ps1"
        if not apply_ps1.exists():
            _write_text(err_file, f"missing_apply_edits_script|path={apply_ps1}")
            print("REDTEAM_CHECK|name=apply_edits_script_present|status=FAIL|detail=missing")
            print(f"REDTEAM_SUMMARY|status=FAIL|artifacts={art}|err={err_file}")
            print("REDTEAM_END")
            return 2
        print("REDTEAM_CHECK|name=apply_edits_script_present|status=PASS|detail=present")

        # choose powershell executable
        if platform.system().lower() == "windows":
            ps = ["powershell.exe", "-NoProfile"]
        else:
            # ubuntu runners usually have pwsh; fail-closed if missing
            ps = ["pwsh", "-NoProfile"]

        # craft edits payloads
        def payload(path: str) -> dict:
            return {
                "version": "1",
                "created_at": datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
                "edits": [{"op": "FILE_WRITE", "path": path, "content": "redteam"}],
            }

        # absolute path test (platform-specific)
        abs_path = "C:\\Windows\\System32\\pwn.txt" if platform.system().lower() == "windows" else "/etc/pwn.txt"
        trav_path = "../pwn.txt"
        ok_path = "docs/redteam_ok.md"

        tests = [
            ("reject_absolute_path", abs_path, True),
            ("reject_path_traversal", trav_path, True),
            ("allow_docs_write", ok_path, False),
        ]

        all_ok = True
        for name, pth, should_reject in tests:
            edits_file = art / f"redteam_edits_{name}.json"
            edits_file.write_text(json.dumps(payload(pth), ensure_ascii=False, indent=2), encoding="utf-8")
            rc, out = _run_apply_edits(ps, repo, edits_file, art, name)

            if should_reject:
                if rc == 0:
                    all_ok = False
                    print(f"REDTEAM_CHECK|name={name}|status=FAIL|detail=accepted_untrusted_path")
                else:
                    print(f"REDTEAM_CHECK|name={name}|status=PASS|detail=rejected")
            else:
                if rc == 0:
                    print(f"REDTEAM_CHECK|name={name}|status=PASS|detail=allowed")
                else:
                    all_ok = False
                    print(f"REDTEAM_CHECK|name={name}|status=FAIL|detail=rejected_allowed_path")

        if all_ok:
            print(f"REDTEAM_SUMMARY|status=PASS|artifacts={art}")
            print("REDTEAM_END")
            return 0

        _write_text(err_file, "see redteam_*.txt for details")
        print(f"REDTEAM_SUMMARY|status=FAIL|artifacts={art}|err={err_file}")
        print("REDTEAM_END")
        return 2

    except Exception:
        _write_text(err_file, traceback.format_exc())
        print(f"REDTEAM_SUMMARY|status=FAIL|artifacts={art}|err={err_file}")
        print("REDTEAM_END")
        return 2

if __name__ == "__main__":
    raise SystemExit(main())