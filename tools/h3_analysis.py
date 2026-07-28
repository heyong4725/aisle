"""H3 results assembler (design doc §11.5; ADR-h3-campaign-protocol §7).

Reads the campaign's per-scenario records (runs/h3/arm_*/S*/scenario.json),
emits the cells table, the S1→S3 transfer curve inputs, and the H3
verdict — with integrity flags computed FROM the records (a non-empty
prior_skills on the wiped arm is the campaign-2 wipe leak; holdout
ok=false is a partial cell). CON-8: single JSON object to stdout.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

TIERS = ("S1", "S2", "S3")
VERDICT_TIERS = ("S2", "S3")  # ADR §7: transfer shows up AFTER S1
CRITERION = "arm L S2+S3 time-to-first-success <= 0.5x arm W's (ADR-h3 §7)"


def load_scenarios(campaign_dir: Path) -> list[dict]:
    return [
        json.loads(path.read_text()) for path in sorted(campaign_dir.glob("arm_*/S*/scenario.json"))
    ]


def cell(rec: dict) -> dict:
    """One table cell per (arm, tier), with integrity flags derived from
    the record itself — never hand-annotated."""
    holdout = rec.get("holdout") or {}
    session = rec.get("session") or {}
    flags = []
    if rec.get("arm") == "W" and rec.get("prior_skills"):
        # the WIPED arm saw prior-scenario state (campaign-2 leak): bias
        # direction is conservative for H3 (makes W look more capable)
        flags.append("wipe_leak")
    if not holdout.get("ok"):
        flags.append("holdout_partial")
    return {
        "arm": rec.get("arm"),
        "tier": rec.get("tier"),
        "holdout_pass1": holdout.get("pass1"),
        "holdout_failures": holdout.get("failures"),
        "stopped": session.get("stopped"),
        "tokens": session.get("tokens"),
        "wall_s": session.get("wall_s"),
        "first_success_wall_s": rec.get("first_success_wall_s"),
        "wrong_object_total": rec.get("wrong_object_total"),
        "skills_after": rec.get("skills_after"),
        "skill_reuse_in_deliverable": rec.get("skill_reuse_in_deliverable"),
        "flags": flags,
    }


def _first_success(cells: list[dict], arm: str, tier: str) -> tuple[bool, float | None]:
    """(cell_exists, first_success_wall_s)."""
    for c in cells:
        if c["arm"] == arm and c["tier"] == tier:
            return True, c["first_success_wall_s"]
    return False, None


def h3_verdict(cells: list[dict]) -> dict:
    """ADR §7 per tier (S2, S3): L <= 0.5x W. A tier where L never
    succeeded is not-met; where only W never succeeded, met (L
    transferred, W could not). met is None until every needed cell
    exists. Integrity flags become caveats, never silent."""
    ratios: dict[str, float | None] = {}
    per_tier: dict[str, bool] = {}
    complete = True
    for tier in VERDICT_TIERS:
        have_w, w_first = _first_success(cells, "W", tier)
        have_l, l_first = _first_success(cells, "L", tier)
        if not (have_w and have_l):
            complete = False
            continue
        ratios[tier] = (l_first / w_first) if (w_first and l_first is not None) else None
        if l_first is None:
            per_tier[tier] = False  # L never succeeded: no transfer shown
        elif w_first is None:
            per_tier[tier] = True  # only L succeeded: transfer at its clearest
        else:
            per_tier[tier] = l_first <= 0.5 * w_first
    caveats = [f"{c['arm']}/{c['tier']}: {flag}" for c in cells for flag in c["flags"]]
    return {
        "criterion": CRITERION,
        "ratios": ratios,
        "per_tier": per_tier,
        "met": all(per_tier.values()) if complete and per_tier else None,
        "caveats": caveats,
    }


def results_markdown(cells: list[dict], verdict: dict) -> str:
    header = (
        "| Arm | Tier | Held-out pass@1 | Session end | Tokens | Wall h "
        "| First success (min) | Reuse | Flags |"
    )
    lines = [header, "|---|---|---|---|---|---|---|---|---|"]
    for c in cells:
        pass1 = c["holdout_pass1"]
        shown = "—" if pass1 is None else f"{pass1:.3f}"
        if "holdout_partial" in c["flags"]:
            shown += " (partial)"
        first = c["first_success_wall_s"]
        lines.append(
            f"| {c['arm']} | {c['tier']} | {shown} | {c['stopped']} "
            f"| {c['tokens']} | {(c['wall_s'] or 0) / 3600:.2f} "
            f"| {'—' if first is None else f'{first / 60:.1f}'} "
            f"| {', '.join(c['skill_reuse_in_deliverable'] or []) or '—'} "
            f"| {', '.join(c['flags']) or '—'} |"
        )
    lines += [
        "",
        f"Verdict ({verdict['criterion']}): "
        f"{'pending' if verdict['met'] is None else 'MET' if verdict['met'] else 'NOT MET'}",
    ]
    for caveat in verdict["caveats"]:
        lines.append(f"- caveat: {caveat}")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dir", type=Path, required=True, help="campaign dir (runs/h3)")
    parser.add_argument("--markdown", type=Path, default=None, help="also write the table here")
    args = parser.parse_args()
    cells = [cell(r) for r in load_scenarios(args.dir)]
    verdict = h3_verdict(cells)
    if args.markdown:
        args.markdown.write_text(results_markdown(cells, verdict))
    total_wrong = sum(c["wrong_object_total"] or 0 for c in cells)
    print(
        json.dumps(
            {
                "ok": True,
                "cells": cells,
                "verdict": verdict,
                "wrong_object_total": total_wrong,
            },
            indent=1,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
