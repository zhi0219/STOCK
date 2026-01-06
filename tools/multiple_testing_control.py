from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List

from tools.paths import repo_root, to_repo_relative

ROOT = repo_root()
DEFAULT_BUDGET_PATH = ROOT / "fixtures" / "multiple_testing_control" / "trial_budget.json"


class TrialBudgetError(RuntimeError):
    pass


@dataclass(frozen=True)
class BudgetEnforcement:
    requested_candidate_count: int
    requested_trial_count: int
    enforced_candidate_count: int
    enforced_trial_count: int
    budget_candidate_count: int
    budget_trial_count: int
    baseline_count: int
    status: str
    reasons: List[str] = field(default_factory=list)
    budget_path: Path = DEFAULT_BUDGET_PATH

    def as_dict(self) -> Dict[str, object]:
        return {
            "schema_version": 1,
            "status": self.status,
            "requested_candidate_count": self.requested_candidate_count,
            "requested_trial_count": self.requested_trial_count,
            "enforced_candidate_count": self.enforced_candidate_count,
            "enforced_trial_count": self.enforced_trial_count,
            "budget_candidate_count": self.budget_candidate_count,
            "budget_trial_count": self.budget_trial_count,
            "baseline_count": self.baseline_count,
            "reasons": list(self.reasons),
            "budget_path": to_repo_relative(self.budget_path),
        }


def _load_budget(path: Path) -> Dict[str, int]:
    if not path.exists():
        raise TrialBudgetError(f"trial_budget_missing: {to_repo_relative(path)}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise TrialBudgetError("trial_budget_invalid")
    trial_count = payload.get("trial_count")
    candidate_count = payload.get("candidate_count")
    if not isinstance(trial_count, int) or not isinstance(candidate_count, int):
        raise TrialBudgetError("trial_budget_invalid")
    return {"trial_count": trial_count, "candidate_count": candidate_count}


def enforce_budget(
    requested_candidate_count: int,
    baseline_count: int,
    budget_path: Path | None = None,
) -> BudgetEnforcement:
    resolved_budget_path = budget_path or DEFAULT_BUDGET_PATH
    budget = _load_budget(resolved_budget_path)
    budget_trial = int(budget["trial_count"])
    budget_candidate = int(budget["candidate_count"])
    baseline_count = int(baseline_count)
    requested_candidate_count = int(requested_candidate_count)
    requested_trial_count = requested_candidate_count + baseline_count

    max_candidates_by_trial = budget_trial - baseline_count
    if max_candidates_by_trial < 0:
        raise TrialBudgetError(
            "trial_budget_below_baselines|"
            f"baseline_count={baseline_count}|"
            f"budget_trial_count={budget_trial}|"
            "next=reduce search scale"
        )

    enforced_candidate_count = min(requested_candidate_count, budget_candidate, max_candidates_by_trial)
    enforced_trial_count = baseline_count + enforced_candidate_count
    reasons: List[str] = []
    if requested_candidate_count > budget_candidate:
        reasons.append("candidate_budget_clamped")
    if requested_trial_count > budget_trial:
        reasons.append("trial_budget_clamped")
    if enforced_candidate_count < requested_candidate_count:
        reasons.append("candidate_count_reduced")

    status = "CLAMPED" if reasons else "OK"
    return BudgetEnforcement(
        requested_candidate_count=requested_candidate_count,
        requested_trial_count=requested_trial_count,
        enforced_candidate_count=enforced_candidate_count,
        enforced_trial_count=enforced_trial_count,
        budget_candidate_count=budget_candidate,
        budget_trial_count=budget_trial,
        baseline_count=baseline_count,
        status=status,
        reasons=reasons,
        budget_path=resolved_budget_path,
    )


def write_enforcement_artifact(path: Path, enforcement: BudgetEnforcement) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = enforcement.as_dict()
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
