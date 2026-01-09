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


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="strict")


def _missing_terms(text: str, terms: list[str]) -> list[str]:
    return [term for term in terms if term not in text]

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--artifacts-dir", required=True)
    ap.add_argument("--repo-root", default=None)
    args = ap.parse_args(argv)

    repo = Path(args.repo_root).resolve() if args.repo_root else _repo_root()
    sys.path.insert(0, str(repo))
    art = Path(args.artifacts_dir).resolve()
    art.mkdir(parents=True, exist_ok=True)
    txt_report = art / "verify_docs_contract.txt"
    json_report = art / "verify_docs_contract.json"

    required = [
        ("docs/vision.md", repo / "docs" / "vision.md"),
        ("docs/gates.md", repo / "docs" / "gates.md"),
        (".github/pull_request_template.md", repo / ".github" / "pull_request_template.md"),
    ]
    backlog = repo / "docs" / "backlog.md"
    roadmap = repo / "docs" / "roadmap.md"
    backlog_label = "docs/backlog.md"
    if not backlog.exists() and roadmap.exists():
        backlog = roadmap
        backlog_label = "docs/roadmap.md"

    print("DOCS_CONTRACT_START")
    status_ok = True
    missing_files: list[str] = []
    missing_sections: dict[str, list[str]] = {}
    missing_imp: list[str] = []
    missing_gates: list[str] = []
    missing_pr_sections: list[str] = []
    next_hint = f"next=inspect {txt_report}"

    try:
        for name, path in required:
            if not path.exists():
                status_ok = False
                missing_files.append(name)
                print(f"DOCS_CONTRACT_ITEM|name={name}|status=FAIL|detail=missing_file")
            else:
                print(f"DOCS_CONTRACT_ITEM|name={name}|status=PASS|detail=present")

        # MEMORY_COMMIT presence in each file (if file exists)
        missing_mc: list[str] = []
        for name, path in required:
            if not path.exists():
                continue
            txt = _read_text(path)
            if "MEMORY_COMMIT" not in txt:
                status_ok = False
                missing_mc.append(name)
                print(f"DOCS_CONTRACT_ITEM|name={name}|status=FAIL|detail=missing_MEMORY_COMMIT")
        if backlog.exists():
            bl = _read_text(backlog)
            if "MEMORY_COMMIT" not in bl:
                status_ok = False
                missing_mc.append(backlog_label)
                print(
                    f"DOCS_CONTRACT_ITEM|name={backlog_label}|status=FAIL|detail=missing_MEMORY_COMMIT"
                )
        if missing_mc:
            print(f"DOCS_CONTRACT_ITEM|name=MEMORY_COMMIT|status=FAIL|detail=missing_in={','.join(missing_mc)}")
        else:
            print("DOCS_CONTRACT_ITEM|name=MEMORY_COMMIT|status=PASS|detail=present_in_all")

        vision = repo / "docs" / "vision.md"
        if vision.exists():
            required_terms = [
                "SIM-only",
                "READ_ONLY",
                "deterministic decision layer",
                "AI role boundary",
                "kill switch",
                "fail-closed",
                "manual confirmation",
                "CI gates are the sole judge",
            ]
            missing = _missing_terms(_read_text(vision), required_terms)
            if missing:
                status_ok = False
                missing_sections["docs/vision.md"] = missing
                print(
                    "DOCS_CONTRACT_ITEM|name=docs/vision.md|status=FAIL"
                    f"|detail=missing_terms={','.join(missing)}"
                )
            else:
                print("DOCS_CONTRACT_ITEM|name=docs/vision.md|status=PASS|detail=required_terms_present")

        gates_doc = repo / "docs" / "gates.md"
        if gates_doc.exists():
            required_terms = [
                "PASS vs DEGRADED",
                "PASS means",
                "DEGRADED means",
                "FAIL means",
            ]
            gate_markers = [
                "compile_check",
                "syntax_guard",
                "ps_parse_guard",
                "safe_push_contract",
                "verify_safe_pull_contract",
                "powershell_join_path_contract",
                "ui_preflight",
                "docs_contract",
                "verify_edits_contract",
                "inventory_repo",
                "verify_inventory_contract",
                "apply_edits_dry_run",
                "extract_json_strict_negative",
                "verify_pr36_gate",
                "import_contract",
                "verify_pr",
                "verify_foundation",
                "verify_consistency",
            ]
            text = _read_text(gates_doc)
            missing_terms = _missing_terms(text, required_terms)
            missing_gates = _missing_terms(text, gate_markers)
            if missing_terms or missing_gates:
                status_ok = False
                missing_sections["docs/gates.md"] = missing_terms
                if missing_gates:
                    missing_gates = missing_gates
                print(
                    "DOCS_CONTRACT_ITEM|name=docs/gates.md|status=FAIL"
                    f"|detail=missing_terms={','.join(missing_terms) or 'none'}"
                    f"|missing_gates={','.join(missing_gates) or 'none'}"
                )
            else:
                print("DOCS_CONTRACT_ITEM|name=docs/gates.md|status=PASS|detail=required_terms_present")

        # IMP-001..IMP-040 in backlog
        if backlog.exists():
            bl = _read_text(backlog)
            for i in range(1, 41):
                imp = f"IMP-{i:03d}"
                if imp not in bl:
                    missing_imp.append(imp)
            required_groups = ["## P0", "## P1", "## P2"]
            missing_groups = [group for group in required_groups if group not in bl]
            if missing_groups:
                status_ok = False
                missing_sections[backlog_label] = missing_groups
                print(
                    f"DOCS_CONTRACT_ITEM|name={backlog_label}|status=FAIL"
                    f"|detail=missing_groups={','.join(missing_groups)}"
                )
            if missing_imp:
                status_ok = False
                print(f"DOCS_CONTRACT_ITEM|name=IMP_INDEX|status=FAIL|detail=missing={','.join(missing_imp)}")
            else:
                print("DOCS_CONTRACT_ITEM|name=IMP_INDEX|status=PASS|detail=IMP-001..IMP-040 present")
        else:
            status_ok = False
            missing_files.append("docs/backlog.md|docs/roadmap.md")
            print("DOCS_CONTRACT_ITEM|name=IMP_INDEX|status=FAIL|detail=backlog_or_roadmap_missing")

        pr_template = repo / ".github" / "pull_request_template.md"
        if pr_template.exists():
            required_sections = [
                "## Evidence / Artifacts",
                "## Acceptance Criteria",
                "## Failure Signals",
                "## Rollback",
                "## Data Hash",
                "## Code Hash",
                "## MEMORY_COMMIT",
            ]
            missing_pr_sections = _missing_terms(_read_text(pr_template), required_sections)
            if missing_pr_sections:
                status_ok = False
                print(
                    "DOCS_CONTRACT_ITEM|name=pull_request_template|status=FAIL"
                    f"|detail=missing_sections={','.join(missing_pr_sections)}"
                )
            else:
                print("DOCS_CONTRACT_ITEM|name=pull_request_template|status=PASS|detail=required_sections_present")

        next_hint = f"next=inspect {txt_report}"

        if status_ok:
            summary = {
                "status": "PASS",
                "missing_files": [],
                "missing_sections": {},
                "missing_imp": [],
                "missing_gates": [],
                "missing_pr_sections": [],
                "artifacts_dir": str(art),
                "next": "none",
            }
            _write_text(txt_report, "PASS: docs contract satisfied\n")
            _write_text(json_report, json.dumps(summary, ensure_ascii=False, indent=2))
            print(f"DOCS_CONTRACT_SUMMARY|status=PASS|missing=none|artifacts={art}|next=none")
            print("DOCS_CONTRACT_END")
            return 0

        # failure: write details
        detail = {
            "status": "FAIL",
            "missing_files": missing_files,
            "missing_sections": missing_sections,
            "missing_imp": missing_imp,
            "missing_gates": missing_gates,
            "missing_pr_sections": missing_pr_sections,
            "artifacts_dir": str(art),
            "next": str(txt_report),
        }
        _write_text(
            txt_report,
            "FAIL: docs contract missing requirements\n"
            f"missing_files={missing_files or 'none'}\n"
            f"missing_sections={missing_sections or 'none'}\n"
            f"missing_imp={missing_imp or 'none'}\n"
            f"missing_gates={missing_gates or 'none'}\n"
            f"missing_pr_sections={missing_pr_sections or 'none'}\n",
        )
        _write_text(json_report, json.dumps(detail, ensure_ascii=False, indent=2))
        print(
            "DOCS_CONTRACT_SUMMARY|status=FAIL"
            f"|missing_files={','.join(missing_files) if missing_files else 'none'}"
            f"|artifacts={art}|err={txt_report}|{next_hint}"
        )
        print("DOCS_CONTRACT_END")
        return 2

    except Exception:
        status_ok = False
        _write_text(txt_report, traceback.format_exc())
        _write_text(
            json_report,
            json.dumps(
                {"status": "FAIL", "error": "exception", "artifacts_dir": str(art)},
                ensure_ascii=False,
                indent=2,
            ),
        )
        print(f"DOCS_CONTRACT_SUMMARY|status=FAIL|missing=unknown|artifacts={art}|err={txt_report}|{next_hint}")
        print("DOCS_CONTRACT_END")
        return 2

if __name__ == "__main__":
    raise SystemExit(main())
