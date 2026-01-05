import tempfile
import unittest
from pathlib import Path

from tools import verify_docs_contract


def _write_required_docs(root: Path) -> None:
    docs_dir = root / "docs"
    docs_dir.mkdir(parents=True, exist_ok=True)
    github_dir = root / ".github"
    github_dir.mkdir(parents=True, exist_ok=True)

    (docs_dir / "vision.md").write_text(
        """# Vision

SIM-only and READ_ONLY with a deterministic decision layer.
AI role boundary: explanation, evidence, guard proposals only.
kill switch, fail-closed, manual confirmation required.
CI gates are the sole judge.

MEMORY_COMMIT:
- test
""",
        encoding="utf-8",
    )

    gates_text = """
MEMORY_COMMIT:\n- test\n\n# Gates\n\nPASS means success.\nDEGRADED means warnings.\nFAIL means fail-closed.\nPASS vs DEGRADED is explicit.\n\ncompile_check\nsyntax_guard\nps_parse_guard\nsafe_push_contract\npowershell_join_path_contract\nui_preflight\ndocs_contract\nverify_edits_contract\ninventory_repo\nverify_inventory_contract\napply_edits_dry_run\nextract_json_strict_negative\nverify_pr36_gate\nimport_contract\nverify_pr40_gate.py\nverify_foundation.py\nverify_consistency.py\n"""
    (docs_dir / "gates.md").write_text(gates_text, encoding="utf-8")

    backlog_lines = ["# Backlog", "", "## P0"]
    backlog_lines.extend([f"- IMP-{i:03d}" for i in range(1, 11)])
    backlog_lines.extend(["", "## P1"])
    backlog_lines.extend([f"- IMP-{i:03d}" for i in range(11, 26)])
    backlog_lines.extend(["", "## P2"])
    backlog_lines.extend([f"- IMP-{i:03d}" for i in range(26, 41)])
    backlog_lines.extend(["", "MEMORY_COMMIT:", "- test"])
    (docs_dir / "backlog.md").write_text("\n".join(backlog_lines), encoding="utf-8")

    pr_template_text = """
Summary\n- \n\nRisks\n- \n\nTesting\n- \n\nMEMORY_COMMIT:\n- test\n\n## Summary\n- \n\n## Acceptance Criteria\n- \n\n## Gates\n- \n\n## Evidence / Artifacts\n- \n\n## Data Hash\n- \n\n## Code Hash\n- \n\n## Failure Signals\n- \n\n## Rollback\n- \n\n## MEMORY_COMMIT\n- \n"""
    (github_dir / "pull_request_template.md").write_text(pr_template_text, encoding="utf-8")


class VerifyDocsContractTests(unittest.TestCase):
    def test_passes_with_complete_docs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            _write_required_docs(root)
            artifacts = root / "artifacts"
            rc = verify_docs_contract.main(
                ["--artifacts-dir", str(artifacts), "--repo-root", str(root)]
            )
            self.assertEqual(rc, 0)

    def test_fails_when_doc_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            _write_required_docs(root)
            (root / "docs" / "vision.md").unlink()
            artifacts = root / "artifacts"
            rc = verify_docs_contract.main(
                ["--artifacts-dir", str(artifacts), "--repo-root", str(root)]
            )
            self.assertNotEqual(rc, 0)

    def test_fails_when_imp_list_incomplete(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            _write_required_docs(root)
            backlog = root / "docs" / "backlog.md"
            text = backlog.read_text(encoding="utf-8")
            backlog.write_text(text.replace("IMP-040", ""), encoding="utf-8")
            artifacts = root / "artifacts"
            rc = verify_docs_contract.main(
                ["--artifacts-dir", str(artifacts), "--repo-root", str(root)]
            )
            self.assertNotEqual(rc, 0)

    def test_fails_when_pr_template_missing_sections(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            _write_required_docs(root)
            pr_template = root / ".github" / "pull_request_template.md"
            pr_template.write_text("MEMORY_COMMIT:\n- test\n", encoding="utf-8")
            artifacts = root / "artifacts"
            rc = verify_docs_contract.main(
                ["--artifacts-dir", str(artifacts), "--repo-root", str(root)]
            )
            self.assertNotEqual(rc, 0)


if __name__ == "__main__":
    unittest.main()
