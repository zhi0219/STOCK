from __future__ import annotations

import argparse
import ast
import json
import re
from pathlib import Path
from typing import Iterable, Sequence

ROOT = Path(__file__).resolve().parents[1]
TOOLS_DIR = ROOT / "tools"
SCRIPTS_DIR = ROOT / "scripts"
DOCS_DIR = ROOT / "docs"
GITHUB_DIR = ROOT / ".github"

MARKER_RE = re.compile(r"\b[A-Z][A-Z0-9]+_(?:START|SUMMARY|END)\b")
ARTIFACT_RE = re.compile(r"artifacts[\\/][A-Za-z0-9_./\\-]+")
ARTIFACT_DIR_REF_RE = re.compile(r"artifacts_dir\s*/\s*[\"']([^\"']+)[\"']")


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="ignore")


def _normalize_artifact(path_value: str) -> str:
    normalized = path_value.replace("\\\\", "/").replace("\\", "/")
    normalized = normalized.strip().strip("\"'")
    return normalized


def _extract_markers(text: str) -> list[str]:
    return sorted(set(MARKER_RE.findall(text)))


def _extract_artifacts(text: str) -> list[str]:
    artifacts: set[str] = set()
    for match in ARTIFACT_RE.findall(text):
        artifacts.add(_normalize_artifact(match))
    for match in ARTIFACT_DIR_REF_RE.findall(text):
        artifacts.add(f"artifacts/{_normalize_artifact(match)}")
    return sorted(artifacts)


def _has_cli_signature(text: str) -> bool:
    return "argparse" in text or "ArgumentParser" in text or "--help" in text or "-h" in text


def _usage_hint(module: str, text: str, prefix: str) -> str:
    if _has_cli_signature(text):
        return f"{prefix} --help"
    return prefix


def _iter_tool_entrypoints() -> list[dict[str, str]]:
    entrypoints: list[dict[str, str]] = []
    for path in sorted(TOOLS_DIR.glob("*.py")):
        if path.name.startswith("__"):
            continue
        module = path.stem
        text = _read_text(path)
        usage = _usage_hint(module, text, f"python -m tools.{module}")
        entrypoints.append(
            {
                "name": module,
                "type": "py_module",
                "path": str(path.relative_to(ROOT)),
                "usage_hint": usage,
            }
        )
    return entrypoints


def _iter_script_entrypoints() -> list[dict[str, str]]:
    entrypoints: list[dict[str, str]] = []
    for path in sorted(SCRIPTS_DIR.glob("*.sh")):
        text = _read_text(path)
        usage = _usage_hint(path.stem, text, f"{path.relative_to(ROOT)} --help")
        entrypoints.append(
            {
                "name": path.stem,
                "type": "sh",
                "path": str(path.relative_to(ROOT)),
                "usage_hint": usage,
            }
        )
    for path in sorted(SCRIPTS_DIR.glob("*.ps1")):
        text = _read_text(path)
        usage = _usage_hint(path.stem, text, f"{path.relative_to(ROOT)}")
        entrypoints.append(
            {
                "name": path.stem,
                "type": "ps1",
                "path": str(path.relative_to(ROOT)),
                "usage_hint": usage,
            }
        )
    return entrypoints


def _parse_allow_prefixes() -> list[str]:
    apply_edits = TOOLS_DIR / "apply_edits.py"
    if not apply_edits.exists():
        return []
    try:
        module = ast.parse(_read_text(apply_edits))
    except SyntaxError:
        return []
    for node in module.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "ALLOW_PREFIXES":
                    try:
                        value = ast.literal_eval(node.value)
                    except Exception:
                        continue
                    if isinstance(value, (list, tuple)):
                        return sorted({str(item) for item in value})
    return []


def _extract_gate_commands(text: str) -> list[tuple[str, str]]:
    commands: list[tuple[str, str]] = []
    seen: set[str] = set()
    for line in text.splitlines():
        stripped = line.strip()
        match = re.search(r"python(?:3)?\s+-m\s+(tools\.[A-Za-z0-9_]+)", stripped)
        if match:
            name = match.group(1)
            command = stripped
            if "action_center_report" in name:
                continue
            if name not in seen:
                commands.append((name, command))
                seen.add(name)
            continue
        match = re.search(r"python(?:3)?\s+tools/([A-Za-z0-9_]+)\.py", stripped)
        if match:
            name = f"tools.{match.group(1)}"
            command = stripped
            if name not in seen:
                commands.append((name, command))
                seen.add(name)
            continue
    if "preflight_gate=" in text and "tools.verify_pr36_gate" not in seen:
        commands.append(("tools.verify_pr36_gate", "python3 tools/verify_pr36_gate.py (if present)"))
        seen.add("tools.verify_pr36_gate")
    if "gate_script=" in text:
        commands.append(
            (
                "canonical_gate_runner",
                "python tools/verify_prNN_gate.py | python tools/verify_foundation.py | python tools/verify_consistency.py",
            )
        )
    return commands


def _gate_file_for(name: str) -> Path | None:
    if name.startswith("tools."):
        tool = name.split("tools.")[-1]
        path = TOOLS_DIR / f"{tool}.py"
        return path if path.exists() else None
    return None


def _build_gates(ci_script: Path) -> list[dict[str, object]]:
    text = _read_text(ci_script)
    gate_entries: list[dict[str, object]] = []
    for name, command in _extract_gate_commands(text):
        markers_expected: list[str] = []
        artifacts_expected: list[str] = []
        gate_file = _gate_file_for(name)
        if gate_file:
            file_text = _read_text(gate_file)
            markers_expected = _extract_markers(file_text)
            artifacts_expected = _extract_artifacts(file_text)
        gate_entries.append(
            {
                "name": name,
                "command": command,
                "artifacts_expected": artifacts_expected,
                "markers_expected": markers_expected,
            }
        )
    return gate_entries


def _collect_artifacts_from_files(paths: Sequence[Path]) -> dict[str, set[str]]:
    artifact_map: dict[str, set[str]] = {}
    for path in paths:
        text = _read_text(path)
        for artifact in _extract_artifacts(text):
            artifact_map.setdefault(artifact, set()).add(str(path.relative_to(ROOT)))
    return artifact_map


def _feature_map(
    entrypoints: list[dict[str, str]],
    gates: list[dict[str, object]],
    artifacts: dict[str, set[str]],
) -> list[dict[str, object]]:
    gate_names = {gate["name"] for gate in gates}
    features: list[dict[str, object]] = []
    for entry in entrypoints:
        feature_name = entry["name"]
        files = [entry["path"]]
        commands = [entry["usage_hint"]]
        tool_gate_name = f"tools.{entry['name']}"
        gates_for_entry = []
        if tool_gate_name in gate_names:
            gates_for_entry.append(tool_gate_name)
        artifact_list = sorted(
            [artifact for artifact, producers in artifacts.items() if entry["path"] in producers]
        )
        features.append(
            {
                "feature": feature_name,
                "files": sorted(files),
                "commands": sorted(commands),
                "gates": sorted(gates_for_entry),
                "artifacts": artifact_list,
            }
        )
    return sorted(features, key=lambda item: item["feature"])


def _workflow_features(repo_root: Path) -> list[dict[str, object]]:
    workflow_dir = repo_root / ".github" / "workflows"
    if not workflow_dir.exists():
        return []
    features: list[dict[str, object]] = []
    for path in sorted(workflow_dir.glob("*.yml")):
        text = _read_text(path)
        commands = []
        for line in text.splitlines():
            stripped = line.strip()
            if "scripts/ci_gates.sh" in stripped or "python -m tools." in stripped:
                commands.append(stripped)
            if "python3 -m tools." in stripped:
                commands.append(stripped)
        if commands:
            features.append(
                {
                    "feature": f"workflow:{path.stem}",
                    "files": [str(path.relative_to(repo_root))],
                    "commands": sorted(set(commands)),
                    "gates": [],
                    "artifacts": [],
                }
            )
    return features


def _contracts() -> list[dict[str, str]]:
    contracts: list[dict[str, str]] = []
    docs = {
        "vision": DOCS_DIR / "vision.md",
        "gates": DOCS_DIR / "gates.md",
        "backlog": DOCS_DIR / "backlog.md",
        "pr_template": GITHUB_DIR / "pull_request_template.md",
    }
    for name, path in docs.items():
        if path.exists():
            contracts.append(
                {
                    "name": name,
                    "enforced_by": "docs_contract",
                    "docs_path": str(path.relative_to(ROOT)),
                }
            )
    return sorted(contracts, key=lambda item: item["name"])


def _render_markdown(inventory: dict[str, object]) -> str:
    lines: list[str] = []
    lines.append("# Repository Capability Inventory")
    lines.append("")
    lines.append("Version: v1")
    lines.append("")

    lines.append("## Entrypoints")
    for entry in inventory["entrypoints"]:  # type: ignore[index]
        lines.append(
            f"- **{entry['name']}** ({entry['type']}): `{entry['path']}` -> `{entry['usage_hint']}`"
        )
    lines.append("")

    lines.append("## Gates (ordered)")
    for gate in inventory["gates"]:  # type: ignore[index]
        markers = ", ".join(gate["markers_expected"]) or "none"
        artifacts = ", ".join(gate["artifacts_expected"]) or "none"
        lines.append(f"- **{gate['name']}**: `{gate['command']}`")
        lines.append(f"  - markers_expected: {markers}")
        lines.append(f"  - artifacts_expected: {artifacts}")
    lines.append("")

    lines.append("## Artifacts")
    for artifact in inventory["artifacts"]:  # type: ignore[index]
        producers = ", ".join(artifact["produced_by"]) or "unknown"
        lines.append(f"- `{artifact['path_glob']}` (produced_by: {producers})")
    lines.append("")

    lines.append("## Contracts")
    for contract in inventory["contracts"]:  # type: ignore[index]
        lines.append(
            f"- **{contract['name']}**: {contract['docs_path']} (enforced_by: {contract['enforced_by']})"
        )
    lines.append("")

    allow_prefixes = inventory.get("allow_prefixes", [])
    if allow_prefixes:
        lines.append("## Allow Prefixes")
        for prefix in allow_prefixes:
            lines.append(f"- `{prefix}`")
        lines.append("")

    lines.append("## Feature Map")
    for feature in inventory["feature_map"]:  # type: ignore[index]
        lines.append(f"- **{feature['feature']}**")
        lines.append(f"  - files: {', '.join(feature['files']) or 'none'}")
        lines.append(f"  - commands: {', '.join(feature['commands']) or 'none'}")
        lines.append(f"  - gates: {', '.join(feature['gates']) or 'none'}")
        lines.append(f"  - artifacts: {', '.join(feature['artifacts']) or 'none'}")
    lines.append("")

    return "\n".join(lines).replace("\r\n", "\n").replace("\r", "\n")


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.replace("\r\n", "\n").replace("\r", "\n"), encoding="utf-8")


def generate_inventory(repo_root: Path) -> dict[str, object]:
    entrypoints = _iter_tool_entrypoints() + _iter_script_entrypoints()
    entrypoints_sorted = sorted(entrypoints, key=lambda item: (item["type"], item["name"]))

    ci_script = repo_root / "scripts" / "ci_gates.sh"
    gates = _build_gates(ci_script) if ci_script.exists() else []

    scanned_paths = [Path(repo_root / entry["path"]) for entry in entrypoints_sorted]
    scanned_paths.extend([repo_root / "scripts" / "ci_gates.sh"])
    artifact_map = _collect_artifacts_from_files([p for p in scanned_paths if p.exists()])

    artifacts = [
        {
            "path_glob": path,
            "produced_by": sorted(producers),
        }
        for path, producers in sorted(artifact_map.items())
    ]

    inventory: dict[str, object] = {
        "version": "v1",
        "entrypoints": entrypoints_sorted,
        "gates": gates,
        "artifacts": artifacts,
        "contracts": _contracts(),
        "feature_map": sorted(
            _feature_map(entrypoints_sorted, gates, artifact_map)
            + _workflow_features(repo_root),
            key=lambda item: item["feature"],
        ),
    }

    allow_prefixes = _parse_allow_prefixes()
    if allow_prefixes:
        inventory["allow_prefixes"] = allow_prefixes

    return inventory


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate repository capability inventory.")
    parser.add_argument("--artifacts-dir", default="artifacts")
    parser.add_argument("--write-docs", action="store_true")
    parser.add_argument("--repo-root", default=None)
    args = parser.parse_args(argv)

    repo_root = Path(args.repo_root).resolve() if args.repo_root else ROOT
    inventory = generate_inventory(repo_root)

    artifacts_dir = Path(args.artifacts_dir).resolve()
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    json_path = artifacts_dir / "repo_inventory.json"
    md_path = artifacts_dir / "repo_inventory.md"

    json_path.write_text(
        json.dumps(inventory, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    markdown = _render_markdown(inventory)
    _write_text(md_path, markdown)

    if args.write_docs:
        docs_path = repo_root / "docs" / "inventory.md"
        _write_text(docs_path, markdown)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
