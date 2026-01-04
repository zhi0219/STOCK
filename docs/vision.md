# Vision

## Long-term safety baseline
- SIM-only and READ_ONLY forever: no live trading, no broker logins, and no fund movement.
- deterministic decision layer for any automated workflow.
- kill switch + fail-closed defaults + manual confirmation required for any sensitive action.

## AI role boundary
- The AI role boundary is explanation, evidence assembly, and guard proposals only.
- The AI must not issue trading recommendations or operational actions.

## Governance
- CI gates are the sole judge for repository changes.
- Every gate must be auditable and fail-closed.

MEMORY_COMMIT:
- (autofix) This file is part of the canonical project constraints.
