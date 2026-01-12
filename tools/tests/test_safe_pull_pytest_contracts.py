from pathlib import Path

import pytest
import re

from tools import verify_win_daily_green_contract


SAFE_PULL_ORDERED_DICT_PATTERNS = [
    r"\$SummaryPayload\.(?!ContainsKey\b)",
    r"\$script:RunPayload\.(?!Count\b)",
    r"\$script:DecisionTrace\.",
]


@pytest.mark.parametrize(
    "pattern",
    SAFE_PULL_ORDERED_DICT_PATTERNS,
)
def test_safe_pull_no_ordered_dict_dot_assignments(pattern: str) -> None:
    content = Path("scripts/safe_pull_v1.ps1").read_text(encoding="utf-8", errors="replace")
    assert not re.search(pattern, content)


@pytest.mark.parametrize(
    "artifact_name",
    [
        "safe_pull_markers.txt",
        "safe_pull_summary.json",
        "safe_pull_run.json",
        "safe_pull_out.txt",
        "safe_pull_err.txt",
    ],
)
def test_safe_pull_artifacts_no_bom(artifact_name: str) -> None:
    base_dir = Path("fixtures") / "safe_pull_contract" / "good"
    targets = [base_dir] + [path for path in base_dir.iterdir() if path.is_dir()]
    for target in targets:
        artifact = target / artifact_name
        assert artifact.exists()
        data = artifact.read_bytes()
        assert not data.startswith(b"\xef\xbb\xbf")


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        ("-Mode dry_run", "mode:dry_run"),
        ("-Mode apply", "mode:apply"),
        ("-DryRun", "dryrun"),
    ],
)
def test_win_daily_green_contract_patterns(content: str, expected: str) -> None:
    mode = verify_win_daily_green_contract._detect_safe_pull_pattern(content)
    assert mode == expected
