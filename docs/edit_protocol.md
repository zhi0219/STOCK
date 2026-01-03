# Edit Protocol (v1)

This repo accepts AI-generated changes ONLY via a structured edits JSON.

Ops:
- FILE_WRITE: write full file content
- ANCHOR_EDIT: replace a uniquely anchored block

Hard rules:
- paths must be in allowlist (docs/, tools/, scripts/, .github/, tests/)
- no path traversal
- anchor hits must equal 1
- fail-closed

Contract notes:
- Edits outputs must be JSON-only (no fences or prose).
- Use tools/normalize_edits.py to normalize raw output before apply.
