"""T17: the RESEARCH-agent contract (harness/CLAUDE.research.md) — the
design doc's §8.2.3/§5 requirements made drift-proof (ADR-20). The
contract is prose, but its load-bearing content is pinned to code truth:
the frozen set it forbids, the failure taxonomy it glosses, and the CLI
examples it teaches MUST all match the implementation, and it MUST stay
under the 300-line token budget. Cites HAR-5, HAR-8, CON-7, CON-8.
"""

import re
import shlex
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]
CONTRACT = REPO_ROOT / "harness" / "CLAUDE.research.md"


def _text() -> str:
    assert CONTRACT.exists(), "harness/CLAUDE.research.md is the T17 deliverable"
    return CONTRACT.read_text()


def test_contract_stays_under_the_300_line_budget():
    """§8.2.3: 'Keep it under 300 lines; every line an agent must scroll
    past costs tokens on every turn.'"""
    lines = _text().splitlines()
    assert len(lines) <= 300, f"{len(lines)} lines"


def test_goal_text_is_verbatim():
    """§8.2.3: the contract opens with the §1 goal 'given to the agent,
    verbatim' — including the asymmetric-penalty sentence (matched with
    blockquote wrapping normalized away)."""
    flat = re.sub(r"\s+", " ", _text().replace("\n> ", " "))
    assert "You operate a pharmacy assistant robot." in flat
    assert "a wrong medicine delivered is 10x worse than a failure to deliver" in flat
    assert "that is cheating" in flat


def test_frozen_set_rule_names_every_frozen_path():
    """CON-7: the no-cheating rule is CONCRETE — every path env_hash
    freezes is named in the contract, so 'do not edit env/' can never
    silently under-describe the real frozen set."""
    import sys

    sys.path.insert(0, str(REPO_ROOT / "tools"))
    from env_hash import FROZEN_DIRS, FROZEN_FILES

    text = _text()
    for path in (*FROZEN_DIRS, *FROZEN_FILES):
        assert path in text, f"frozen path {path!r} missing from the contract"


def test_failure_glossary_covers_both_taxonomies():
    """§8.2.3: the failure-taxonomy glossary covers every desk class
    (VER-3) and every retail class (RS-4) — derived from the code
    constants so the glossary cannot drift."""
    from aisle.verifier.oracle import FAILURE_CLASSES
    from aisle.verifier.retail import RETAIL_FAILURE_CLASSES

    text = _text()
    for cls in (*FAILURE_CLASSES, *RETAIL_FAILURE_CLASSES):
        assert f"`{cls}`" in text, f"failure class {cls!r} missing from the glossary"


def test_idea_gate_and_budget_semantics_are_stated():
    """HAR-8: ideas are logged BEFORE running; HAR-5: token accounting.
    The contract must teach both."""
    text = _text()
    assert "report log --idea" in text
    assert "before" in text.lower() and "rollout" in text
    assert "ANTHROPIC_TOKENS_LOG" in text


def test_every_tier_is_documented():
    """The campaign surface: desk T0–T4 and retail S1–S3 all appear."""
    text = _text()
    for tier in ("T0", "T1", "T2", "T3", "T4", "S1", "S2", "S3"):
        assert re.search(rf"\b{tier}\b", text), tier


def _example_commands() -> list[list[str]]:
    """Every fenced `uv run harness ...` example in the contract, as argv
    (after the `uv run harness` prefix), line-continuations joined."""
    text = _text()
    joined = text.replace("\\\n", " ")
    commands = []
    for line in joined.splitlines():
        line = line.strip()
        if line.startswith("uv run harness "):
            commands.append(shlex.split(line)[3:])
    assert commands, "the contract must include copy-paste-runnable examples"
    return commands


def test_cli_examples_parse_against_the_real_argparse_tree():
    """§8.2.3: examples are 'copy-paste-runnable — agents learn from
    examples, not descriptions'. Every `uv run harness ...` example must
    PARSE against the actual CLI (CON-8): a flag rename or subcommand
    change breaks this test, never silently the contract."""
    from aisle.harness.cli import build_parser

    parser = build_parser()
    for argv in _example_commands():
        try:
            parser.parse_args(argv)
        except SystemExit as bad:
            raise AssertionError(f"contract example does not parse: {argv}") from bad


def test_registry_examples_parse_too():
    """CAP-4: `python -m aisle.harness.registry` examples must parse."""
    from aisle.harness.registry import main as _registry_main  # noqa: F401 — import guard

    text = _text()
    joined = text.replace("\\\n", " ")
    examples = [
        shlex.split(line.strip())[5:]
        for line in joined.splitlines()
        if line.strip().startswith("uv run python -m aisle.harness.registry")
    ]
    assert examples, "the contract must show registry search"
    import aisle.harness.registry as registry_module

    src = Path(registry_module.__file__).read_text()
    for argv in examples:
        assert argv[0] in ("search", "lint"), argv
        for flag in (a for a in argv[1:] if a.startswith("--")):
            assert flag.split("=")[0] in src, f"unknown registry flag {flag} in {argv}"


def test_budget_ceilings_match_the_frozen_config():
    """PR #24 (ADR-21): the contract states the REAL campaign ceilings —
    pinned to harness/budget.toml (itself frozen) so prose and enforcement
    cannot drift."""
    import tomllib

    with open(REPO_ROOT / "harness" / "budget.toml", "rb") as f:
        ceilings = tomllib.load(f)["campaign"]
    text = _text()
    assert f"{ceilings['tokens']:,}" in text  # e.g. 5,000,000
    assert str(ceilings["episodes"]) in text
    assert f"{ceilings['wall_h']:g} hours" in text
    # and the enforcement surface is described
    assert "campaign_ledger" in text and "episodes_left" in text


def test_trusted_baseline_rule_is_stated():
    """PR #24 (ADR-21): the contract describes the TRUSTED gate — baseline
    on origin/main, checker self-verified, local override logged."""
    text = _text()
    assert "origin/main" in text
    assert "--env-baseline local" in text
    assert "manifest" in text
