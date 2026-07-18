#!/usr/bin/env python3
"""trace_check: enforce spec→test traceability (HAR-9, CON-8).

Scans specs/*.md for requirement IDs (`- <ID>: text` bullets), flags the
MUST-bearing ones (RFC 2119 MUST / REQUIRED / SHALL in the requirement text),
and scans tests/**/*.py docstrings for ID citations. Exits nonzero listing
uncovered MUSTs, citations or waivers of undefined IDs, duplicate IDs, and
unparseable test files.

Pre-M0, not-yet-implemented specs are waived in tools/trace_waivers.toml
(id + reason, reviewable); --strict ignores waivers and --specs NNN-MMM
scopes the MUST universe by spec number (the M0 gate runs
`--strict --specs 000-080`). See docs/decisions/ADR-1.md.
"""

import argparse
import ast
import json
import re
import sys
import tomllib
from pathlib import Path

ID_PATTERN = r"[A-Z][A-Z0-9]*-[A-Z]?\d+"
REQ_BULLET = re.compile(rf"^- ({ID_PATTERN}):(.*)$")
MUST_KEYWORD = re.compile(r"\b(MUST|REQUIRED|SHALL)\b")


def extract_requirements(specs_dir: Path) -> tuple[dict[str, str], dict[str, set[str]], set[str]]:
    """Scan all specs. Returns (id → requirement text, spec file number →
    ids defined there, ids defined more than once).

    Requirement text spans the bullet plus indented continuation lines;
    blank lines inside a list item do not end it.
    """
    requirements: dict[str, str] = {}
    by_spec_number: dict[str, set[str]] = {}
    duplicates: set[str] = set()
    for spec in sorted(specs_dir.glob("*.md")):
        number = spec.name.split("-")[0]
        ids_here = by_spec_number.setdefault(number, set())
        current_id = None
        for line in spec.read_text().splitlines():
            match = REQ_BULLET.match(line)
            if match:
                current_id = match.group(1)
                if current_id in requirements:
                    duplicates.add(current_id)
                requirements[current_id] = match.group(2)
                ids_here.add(current_id)
            elif not line.strip():
                continue  # blank line inside a list item keeps the current requirement
            elif current_id and line.startswith(" "):
                requirements[current_id] += " " + line.strip()
            else:
                current_id = None
    return requirements, by_spec_number, duplicates


def extract_citations(tests_dir: Path) -> tuple[set[str], list[str]]:
    """Collect requirement IDs cited in docstrings of test modules, classes,
    and functions; report unparseable files instead of crashing (CON-8)."""
    cited: set[str] = set()
    parse_errors: list[str] = []
    for path in sorted(tests_dir.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(), filename=str(path))
        except SyntaxError as exc:
            parse_errors.append(f"{path}: {exc.msg} (line {exc.lineno})")
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
                doc = ast.get_docstring(node)
                if doc:
                    cited.update(re.findall(ID_PATTERN, doc))
    return cited, parse_errors


def load_waivers(root: Path) -> dict[str, str]:
    """Flat id → reason table under [waivers]."""
    path = root / "tools" / "trace_waivers.toml"
    if not path.exists():
        return {}
    with open(path, "rb") as f:
        return tomllib.load(f).get("waivers", {})


def scoped_ids(by_spec_number: dict[str, set[str]], specs_range: str) -> set[str]:
    """IDs from spec files whose numeric NNN- prefix falls in 'NNN-MMM'."""
    lo, hi = (int(part) for part in specs_range.split("-"))
    ids: set[str] = set()
    for number, file_ids in by_spec_number.items():
        if lo <= int(number) <= hi:
            ids.update(file_ids)
    return ids


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument(
        "--strict", action="store_true", help="ignore waivers (the M0 gate runs strict)"
    )
    parser.add_argument(
        "--specs", metavar="NNN-MMM", help="restrict the MUST universe to this spec-number range"
    )
    args = parser.parse_args()

    requirements, by_spec_number, duplicates = extract_requirements(args.root / "specs")
    must_ids = {rid for rid, text in requirements.items() if MUST_KEYWORD.search(text)}
    if args.specs:
        must_ids &= scoped_ids(by_spec_number, args.specs)
    cited, parse_errors = extract_citations(args.root / "tests")
    known_prefixes = {rid.split("-")[0] for rid in requirements}
    cited = {c for c in cited if c.split("-")[0] in known_prefixes}
    waivers = {} if args.strict else load_waivers(args.root)

    unknown_citations = sorted(cited - requirements.keys())
    unknown_waivers = sorted(set(waivers) - requirements.keys())
    uncovered = sorted(must_ids - cited - waivers.keys())
    errors = [] if requirements else [f"no requirement IDs found under {args.root / 'specs'}"]
    ok = not (
        uncovered or unknown_citations or unknown_waivers or duplicates or parse_errors or errors
    )

    report = {
        "ok": ok,
        "strict": args.strict,
        "specs_range": args.specs,
        "requirements": len(requirements),
        "must_ids": sorted(must_ids),
        "covered": sorted(must_ids & cited),
        "waived": sorted(set(waivers) & must_ids),
        "uncovered": uncovered,
        "unknown_citations": unknown_citations,
        "unknown_waivers": unknown_waivers,
        "duplicate_ids": sorted(duplicates),
        "parse_errors": parse_errors,
        "errors": errors,
    }
    print(json.dumps(report))
    if not ok:
        for rid in uncovered:
            print(f"uncovered MUST: {rid}: {requirements[rid][:80]}", file=sys.stderr)
        for rid in unknown_citations:
            print(f"test cites undefined ID: {rid}", file=sys.stderr)
        for rid in unknown_waivers:
            print(f"waiver for undefined ID: {rid}", file=sys.stderr)
        for rid in sorted(duplicates):
            print(f"duplicate requirement ID: {rid}", file=sys.stderr)
        for msg in parse_errors + errors:
            print(msg, file=sys.stderr)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
