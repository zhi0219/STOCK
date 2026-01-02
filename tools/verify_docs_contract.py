import argparse
import json
import sys
import traceback
from pathlib import Path

def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]

def _write_text(p: Path, text: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text.replace("\r\n", "\n").replace("\r", "\n"), encoding="utf-8")

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--artifacts-dir", required=True)
    args = ap.parse_args()

    repo = _repo_root()
    sys.path.insert(0, str(repo))
    art = Path(args.artifacts_dir).resolve()
    art.mkdir(parents=True, exist_ok=True)
    err_file = art / "docs_contract.txt"

    required = [
        ("docs/vision.md", repo / "docs" / "vision.md"),
        ("docs/gates.md", repo / "docs" / "gates.md"),
        ("docs/backlog.md", repo / "docs" / "backlog.md"),
        (".github/pull_request_template.md", repo / ".github" / "pull_request_template.md"),
    ]

    print("DOCS_CONTRACT_START")
    status_ok = True
    missing_files = []

    try:
        for name, path in required:
            if not path.exists():
                status_ok = False
                missing_files.append(name)
                print(f"DOCS_CONTRACT_ITEM|name={name}|status=FAIL|detail=missing_file")
            else:
                print(f"DOCS_CONTRACT_ITEM|name={name}|status=PASS|detail=present")

        # MEMORY_COMMIT presence in each file (if file exists)
        missing_mc = []
        for name, path in required:
            if not path.exists():
                continue
            txt = path.read_text(encoding="utf-8", errors="strict")
            if "MEMORY_COMMIT" not in txt:
                status_ok = False
                missing_mc.append(name)
                print(f"DOCS_CONTRACT_ITEM|name={name}|status=FAIL|detail=missing_MEMORY_COMMIT")
        if missing_mc:
            print(f"DOCS_CONTRACT_ITEM|name=MEMORY_COMMIT|status=FAIL|detail=missing_in={','.join(missing_mc)}")
        else:
            print("DOCS_CONTRACT_ITEM|name=MEMORY_COMMIT|status=PASS|detail=present_in_all")

        # IMP-001..IMP-040 in backlog
        backlog = repo / "docs" / "backlog.md"
        missing_imp = []
        if backlog.exists():
            bl = backlog.read_text(encoding="utf-8", errors="strict")
            for i in range(1, 41):
                imp = f"IMP-{i:03d}"
                if imp not in bl:
                    missing_imp.append(imp)
            if missing_imp:
                status_ok = False
                print(f"DOCS_CONTRACT_ITEM|name=IMP_INDEX|status=FAIL|detail=missing={','.join(missing_imp)}")
            else:
                print("DOCS_CONTRACT_ITEM|name=IMP_INDEX|status=PASS|detail=IMP-001..IMP-040 present")
        else:
            status_ok = False
            print("DOCS_CONTRACT_ITEM|name=IMP_INDEX|status=FAIL|detail=backlog_missing")

        if status_ok:
            print(f"DOCS_CONTRACT_SUMMARY|status=PASS|missing=none|artifacts={art}")
            print("DOCS_CONTRACT_END")
            return 0

        # failure: write details
        detail = {
            "missing_files": missing_files,
            "missing_imp": missing_imp,
        }
        _write_text(err_file, json.dumps(detail, ensure_ascii=False, indent=2))
        print(f"DOCS_CONTRACT_SUMMARY|status=FAIL|missing_files={','.join(missing_files) if missing_files else 'none'}|artifacts={art}|err={err_file}")
        print("DOCS_CONTRACT_END")
        return 2

    except Exception:
        status_ok = False
        _write_text(err_file, traceback.format_exc())
        print(f"DOCS_CONTRACT_SUMMARY|status=FAIL|missing=unknown|artifacts={art}|err={err_file}")
        print("DOCS_CONTRACT_END")
        return 2

if __name__ == "__main__":
    raise SystemExit(main())