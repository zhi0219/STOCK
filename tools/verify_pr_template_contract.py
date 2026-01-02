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
    err_file = art / "pr_template_contract.txt"

    tpl = repo / ".github" / "pull_request_template.md"

    required_terms = [
        "Summary",
        "Gates",
        "Evidence",
        "Data hash",
        "Code hash",
        "Failure signals",
        "Rollback",
        "ANTI_REGRESSION",
        "MEMORY_COMMIT",
    ]

    print("PR_TEMPLATE_CONTRACT_START")
    try:
        if not tpl.exists():
            _write_text(err_file, "missing .github/pull_request_template.md")
            print("PR_TEMPLATE_CONTRACT_ITEM|name=template|status=FAIL|detail=missing_file")
            print(f"PR_TEMPLATE_CONTRACT_SUMMARY|status=FAIL|artifacts={art}|err={err_file}")
            print("PR_TEMPLATE_CONTRACT_END")
            return 2

        txt = tpl.read_text(encoding="utf-8", errors="strict")
        low = txt.lower()
        missing = []
        for term in required_terms:
            if term.lower() not in low:
                missing.append(term)
                print(f"PR_TEMPLATE_CONTRACT_ITEM|name={term}|status=FAIL|detail=missing")
            else:
                print(f"PR_TEMPLATE_CONTRACT_ITEM|name={term}|status=PASS|detail=present")

        if missing:
            _write_text(err_file, json.dumps({"missing_terms": missing}, ensure_ascii=False, indent=2))
            print(f"PR_TEMPLATE_CONTRACT_SUMMARY|status=FAIL|artifacts={art}|err={err_file}")
            print("PR_TEMPLATE_CONTRACT_END")
            return 2

        print(f"PR_TEMPLATE_CONTRACT_SUMMARY|status=PASS|artifacts={art}")
        print("PR_TEMPLATE_CONTRACT_END")
        return 0

    except Exception:
        _write_text(err_file, traceback.format_exc())
        print(f"PR_TEMPLATE_CONTRACT_SUMMARY|status=FAIL|artifacts={art}|err={err_file}")
        print("PR_TEMPLATE_CONTRACT_END")
        return 2

if __name__ == "__main__":
    raise SystemExit(main())