#!/usr/bin/env python3
"""Generate/check the source-derived contributor inventory (CON-5, CON-8).

The committed Markdown appendix is deterministic: it contains no wall time,
Git branch, or working-tree state. CI runs --check so graph, capability, CLI,
ADR, and test changes cannot silently leave the contributor reference stale.
"""

import argparse
import hashlib
import json
import sys
import tomllib
from collections import Counter
from pathlib import Path

import yaml

from aisle.harness.cli import build_parser as build_harness_parser
from aisle.harness.registry import build_parser as build_registry_parser

DEFAULT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = Path("docs/generated/project-inventory.md")


def _cell(value: object) -> str:
    """Escape a compact value for a Markdown table cell."""
    return str(value).replace("|", "\\|").replace("\n", " ").strip() or "—"


def _link(label: str, target: str) -> str:
    return f"[{_cell(label)}]({_cell(target)})"


def _graph_inventory(root: Path) -> list[dict]:
    rows = []
    for path in sorted((root / "graphs").glob("*.yaml")):
        graph = yaml.safe_load(path.read_text())
        if not isinstance(graph, dict) or not isinstance(graph.get("nodes"), list):
            raise ValueError(f"{path}: graph must contain a nodes list")
        nodes = graph["nodes"]
        node_ids = [node.get("id") for node in nodes if isinstance(node, dict)]
        if len(node_ids) != len(nodes) or not all(isinstance(node_id, str) for node_id in node_ids):
            raise ValueError(f"{path}: every node must have a string id")
        bridge = next((node for node in nodes if node.get("id") == "dora-genesis"), {})
        env = bridge.get("env", {}) if isinstance(bridge, dict) else {}
        if not isinstance(env, dict):
            env = {}
        rows.append(
            {
                "path": path,
                "scene": env.get("AISLE_SCENE", "pharmacy (default)"),
                "scenario": env.get("AISLE_SCENARIO", "—"),
                "embodiment": env.get("AISLE_EMBODIMENT", "franka (default)"),
                "perception": env.get("AISLE_PERCEPTION", "L0 (default)"),
                "nodes": node_ids,
            }
        )
    return rows


def _capability_inventory(root: Path) -> list[dict]:
    rows = []
    for path in sorted((root / "registry" / "manifests").glob("*.yaml")):
        manifest = yaml.safe_load(path.read_text())
        if not isinstance(manifest, dict) or not isinstance(manifest.get("id"), str):
            raise ValueError(f"{path}: manifest must contain a string id")
        embodiment = manifest.get("embodiment", {})
        arms = embodiment.get("arm", []) if isinstance(embodiment, dict) else []
        if not isinstance(arms, list):
            arms = []
        provides = manifest.get("provides", [])
        if not isinstance(provides, list):
            provides = []
        rows.append(
            {
                "path": path,
                "id": manifest["id"],
                "provides": provides,
                "embodiment": arms,
                "safety": manifest.get("safety_class", "—"),
                "origin": manifest.get("origin", "—"),
                "source": manifest.get("source", "—"),
            }
        )
    return rows


def _parser_leaves(
    parser: argparse.ArgumentParser, prefix: tuple[str, ...]
) -> list[tuple[tuple[str, ...], argparse.ArgumentParser]]:
    subparser_action = next(
        (action for action in parser._actions if isinstance(action, argparse._SubParsersAction)),
        None,
    )
    if subparser_action is None:
        return [(prefix, parser)]
    leaves = []
    for name, child in sorted(subparser_action.choices.items()):
        leaves.extend(_parser_leaves(child, (*prefix, name)))
    return leaves


def _parser_arguments(parser: argparse.ArgumentParser) -> list[str]:
    arguments = []
    for action in parser._actions:
        if isinstance(action, argparse._HelpAction | argparse._SubParsersAction):
            continue
        if action.option_strings:
            arguments.append(action.option_strings[-1])
        else:
            arguments.append(f"<{action.dest}>")
    return arguments


def _cli_inventory() -> list[dict]:
    rows = [
        {
            "command": ("harness",),
            "arguments": [],
        }
    ]
    rows.extend(
        {
            "command": command,
            "arguments": _parser_arguments(parser),
        }
        for command, parser in _parser_leaves(build_harness_parser(), ("harness",))
    )
    rows.extend(
        {
            "command": ("python", "-m", "aisle.harness.registry", *command),
            "arguments": _parser_arguments(parser),
        }
        for command, parser in _parser_leaves(build_registry_parser(), ())
    )
    return rows


def _adr_inventory(root: Path) -> list[dict]:
    rows = []
    for path in sorted((root / "docs" / "decisions").glob("*.md")):
        lines = path.read_text().splitlines()
        title = next(
            (line.removeprefix("# ").strip() for line in lines if line.startswith("# ")), None
        )
        if title is None:
            raise ValueError(f"{path}: ADR has no level-one heading")
        status = "not declared"
        for index, line in enumerate(lines):
            if not line.startswith("Status:"):
                continue
            parts = [line.removeprefix("Status:").strip()]
            for continuation in lines[index + 1 :]:
                if not continuation.strip() or continuation.startswith("#"):
                    break
                parts.append(continuation.strip())
            status = " ".join(parts)
            break
        rows.append({"path": path, "title": title, "status": status})
    return rows


def _test_inventory(root: Path) -> tuple[list[dict], list[str]]:
    modules = sorted((root / "tests").rglob("test_*.py"))
    counts = Counter(path.relative_to(root / "tests").parts[0] for path in modules)
    rows = [{"suite": suite, "count": count} for suite, count in sorted(counts.items())]

    with open(root / "pyproject.toml", "rb") as file:
        config = tomllib.load(file)
    configured = config["tool"]["pytest"]["ini_options"]["markers"]
    markers = [entry.split(":", 1)[0].strip() for entry in configured]
    return rows, markers


def _relative(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def render_inventory(root: Path) -> tuple[str, dict[str, int]]:
    graphs = _graph_inventory(root)
    capabilities = _capability_inventory(root)
    cli_commands = _cli_inventory()
    adrs = _adr_inventory(root)
    test_suites, markers = _test_inventory(root)
    test_modules = sorted((root / "tests").rglob("test_*.py"))

    lines = [
        "# Generated project inventory",
        "",
        "> Generated by `tools/docs_inventory.py`. Do not edit manually. Run",
        "> `uv run python tools/docs_inventory.py --write` after changing a source surface.",
        "",
        "This appendix is intentionally factual and source-derived. Qualitative maturity,",
        "architecture, and contribution guidance remain in the",
        "[contributor wiki](../contributor-wiki.md).",
        "",
        "## Snapshot counts",
        "",
        "| Surface | Count |",
        "|---|---:|",
        f"| Graphs | {len(graphs)} |",
        f"| Capability manifests | {len(capabilities)} |",
        f"| CLI command entries | {len(cli_commands)} |",
        f"| ADR files | {len(adrs)} |",
        f"| Test modules | {len(test_modules)} |",
        "",
        "## Graphs",
        "",
        "| Graph | Scene/scenario | Embodiment | Perception | Nodes |",
        "|---|---|---|---|---:|",
    ]
    for row in graphs:
        relative = _relative(row["path"], root)
        lines.append(
            "| "
            + " | ".join(
                [
                    _link(relative, f"../../{relative}"),
                    _cell(f"{row['scene']} / {row['scenario']}"),
                    _cell(row["embodiment"]),
                    _cell(row["perception"]),
                    str(len(row["nodes"])),
                ]
            )
            + " |"
        )
    lines.extend(["", "### Graph node membership", ""])
    for row in graphs:
        relative = _relative(row["path"], root)
        lines.extend(
            [
                f"- **{relative}:** " + ", ".join(f"`{node}`" for node in row["nodes"]),
            ]
        )

    lines.extend(
        [
            "",
            "## Capability manifests",
            "",
            "| Capability | Provides | Arm profile(s) | Safety | Origin | Source |",
            "|---|---|---|---|---|---|",
        ]
    )
    for row in capabilities:
        relative = _relative(row["path"], root)
        lines.append(
            "| "
            + " | ".join(
                [
                    _link(relative, f"../../{relative}"),
                    _cell(", ".join(row["provides"])),
                    _cell(", ".join(row["embodiment"])),
                    _cell(row["safety"]),
                    _cell(row["origin"]),
                    f"`{_cell(row['source'])}`",
                ]
            )
            + " |"
        )

    lines.extend(
        [
            "",
            "## CLI commands",
            "",
            "| Command | Arguments/options |",
            "|---|---|",
        ]
    )
    for row in cli_commands:
        command = " ".join(row["command"])
        arguments = ", ".join(f"`{argument}`" for argument in row["arguments"]) or "—"
        lines.append(f"| `{_cell(command)}` | {arguments} |")

    lines.extend(
        [
            "",
            "## Architecture decision records",
            "",
            "Status is the literal first `Status:` line when present; `not declared` is not",
            "an inferred decision state.",
            "",
            "| ADR | Title | Declared status |",
            "|---|---|---|",
        ]
    )
    for row in adrs:
        relative = _relative(row["path"], root)
        target = f"../decisions/{row['path'].name}"
        lines.append(
            f"| {_link(relative, target)} | {_cell(row['title'])} | {_cell(row['status'])} |"
        )

    lines.extend(
        [
            "",
            "## Tests",
            "",
            f"Configured pytest markers: {', '.join(f'`{marker}`' for marker in markers)}.",
            "",
            "| Suite directory | Test modules |",
            "|---|---:|",
        ]
    )
    for row in test_suites:
        lines.append(f"| `tests/{_cell(row['suite'])}` | {row['count']} |")
    lines.extend(["", "### Test modules", ""])
    for path in test_modules:
        relative = _relative(path, root)
        lines.append(f"- {_link(relative, f'../../{relative}')}")
    lines.append("")

    counts = {
        "adrs": len(adrs),
        "capabilities": len(capabilities),
        "cli_commands": len(cli_commands),
        "graphs": len(graphs),
        "test_modules": len(test_modules),
    }
    return "\n".join(lines), counts


def _report(ok: bool, output: Path, content: str, counts: dict[str, int], reason: str) -> dict:
    return {
        "ok": ok,
        "output": str(output),
        "reason": reason,
        "sha256": hashlib.sha256(content.encode()).hexdigest(),
        "counts": counts,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write", action="store_true", help="write the generated Markdown")
    mode.add_argument("--check", action="store_true", help="fail when generated Markdown drifts")
    args = parser.parse_args()

    root = args.root.resolve()
    output = args.output if args.output.is_absolute() else root / args.output
    try:
        content, counts = render_inventory(root)
        if args.write:
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(content)
            report = _report(True, output, content, counts, "written")
        elif not output.exists():
            report = _report(False, output, content, counts, "missing")
        elif output.read_text() != content:
            report = _report(False, output, content, counts, "stale")
        else:
            report = _report(True, output, content, counts, "current")
    except (OSError, KeyError, TypeError, ValueError, yaml.YAMLError) as error:
        print(json.dumps({"ok": False, "error": str(error)}))
        return 1

    print(json.dumps(report))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
