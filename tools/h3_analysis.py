"""H3 results assembler (design doc §11.5, §8.4 item 2; ADR-h3 §7).

Reads the campaign's record set (h3_results*.json + per-scenario
arm_*/S*/scenario.json + token_samples.jsonl) and emits the cells table,
time- AND tokens-to-first-success ratios, and the H3 verdict. Integrity
is computed FROM the records, never hand-annotated: wipe_leak (non-empty
prior_skills on the wiped arm), frozen_drift (non-empty audit),
holdout_partial (expired scoring window), residue_leak (from the
aggregates' wipes lists), treatment_drift (a rollout whose recorded
git_sha or env_baseline_oid is not the treatment pin), and
unattested_metric (first-success supplied by a rollout that is not a
trusted-baseline run at the pin) each flag a cell, and ONLY unflagged
cells enter the verdict. Records from different campaigns
(commit/agent/model/seeds) refuse to combine. CON-8: single JSON object
on stdout, exit 0 iff ok — missing or malformed input fails closed.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

TIERS = ("S1", "S2", "S3")
VERDICT_TIERS = ("S2", "S3")  # ADR §7: transfer shows up AFTER S1
CRITERION = "arm L S2+S3 time-to-first-success <= 0.5x arm W's (ADR-h3 §7)"
# the campaign identity that must be single-valued across every combined
# record (PR #59 review: no cross-commit/model/seed aggregation)
IDENTITY_KEYS = ("commit", "agent", "model", "dev_seeds", "holdout_seeds")
# H5 failure classes, split by WHAT failed (PR #67 review: placement
# quality is not wrong-medicine delivery). DELIVERY classes are the 10x
# precision analogue — the wrong THING delivered (`wrong_object` desk,
# `extra_item` retail, RS-7/design doc §1). PLACEMENT classes are
# where/how it was placed (§11.3 placement score family). Conflating
# them overstated an H5 breach when the committed held-out records show
# zero delivery-class failures.
DELIVERY_CLASSES = ("wrong_object", "extra_item")
PLACEMENT_CLASSES = ("misplaced", "wrong_slot", "misaligned", "overhang")
# score_holdout's exact no-deliverable template — admitted ONLY for
# legacy records that predate the structured `outcome` field, and only
# together with the full structural state (PR #76 review)
_LEGACY_NO_DELIVERABLE = re.compile(r"no deliverable at \S+")
HOLDOUT_RUN_PREFIX = "campaign-holdout"


def load_campaign(campaign_dir: Path) -> dict:
    """Records + treatment, fail-closed (CON-8): missing campaign record,
    malformed JSON, or mismatched treatment identity raise ValueError."""
    result_files = sorted(campaign_dir.glob("h3_results*.json"))
    if not result_files:
        raise ValueError(f"no campaign record (h3_results*.json) under {campaign_dir}")
    treatments = []
    for f in result_files:
        try:
            treatments.append((f.name, json.loads(f.read_text()).get("treatment") or {}))
        except json.JSONDecodeError as exc:
            raise ValueError(f"malformed campaign record {f.name}: {exc}") from exc
    base_name, base = treatments[0]
    for name, t in treatments[1:]:
        for key in IDENTITY_KEYS:
            if t.get(key) != base.get(key):
                raise ValueError(
                    f"treatment mismatch on {key!r}: {name} vs {base_name} — records "
                    "from different campaigns must not be combined"
                )
    scenario_files = sorted(campaign_dir.glob("arm_*/S*/scenario.json"))
    if not scenario_files:
        raise ValueError(f"no scenario records under {campaign_dir}")
    # residue-leak derivation (PR #67 review): within each invocation
    # (one h3_results*.json), an arm-L scenario AFTER the first must have
    # a recorded residue-clear ({"arm": "L", "before": <slot>} in wipes) —
    # the resume ran a pre-guard runner with wipes: [], so L/S3 inherited
    # L/S2's working state. Derived from the aggregates, never asserted.
    leaked: set[tuple[str, str, int]] = set()
    for f in result_files:
        agg = json.loads(f.read_text())
        cleared = {
            (w.get("arm"), w.get("before")) for w in agg.get("wipes") or [] if isinstance(w, dict)
        }
        prev_l = False
        for rec in agg.get("records") or []:
            if not isinstance(rec, dict) or rec.get("arm") != "L":
                continue
            if rec.get("infra_error"):
                # an aborted entry wrote no scenario record and ran no
                # completed session — neither a flag target nor a
                # residue-producing predecessor for this rule
                continue
            attempt = rec.get("attempt", 1)
            slot = rec.get("tier", "") + ("" if attempt == 1 else f"-r{attempt}")
            if prev_l and ("L", slot) not in cleared:
                leaked.add(("L", rec.get("tier"), attempt))
            prev_l = True
    records = []
    for path in scenario_files:
        try:
            rec = json.loads(path.read_text())
        except json.JSONDecodeError as exc:
            raise ValueError(f"malformed scenario record {path}: {exc}") from exc
        rec["_token_samples"] = _samples(path.parent / "token_samples.jsonl")
        if (rec.get("arm"), rec.get("tier"), rec.get("attempt", 1)) in leaked:
            rec["_residue_leak"] = True
        records.append(rec)
    return {"treatment": {k: base.get(k) for k in IDENTITY_KEYS}, "records": records}


def _samples(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text().splitlines():
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue  # a torn final line from a killed session is not data loss
    return out


def _tokens_at(samples: list[dict], wall_s: float | None) -> int | None:
    """Tokens spent by the first success (design doc §8.4: time AND tokens
    to success): the first live sample at/after that wall time."""
    if wall_s is None:
        return None
    for s in samples:
        if s.get("wall_s", 0) >= wall_s:
            return s.get("tokens")
    return samples[-1].get("tokens") if samples else None


def cell(rec: dict, commit: str | None = None) -> dict:
    """One table cell per (arm, tier, attempt), with integrity flags
    derived from the record itself — never hand-annotated. `commit` is
    the treatment pin: with it, rollout provenance (recorded by
    campaign_metrics) yields treatment_drift / unattested_metric;
    records that predate provenance keep only their original flags."""
    holdout = rec.get("holdout") or {}
    session = rec.get("session") or {}
    rollouts = rec.get("rollouts") or []
    flags = []
    if rec.get("arm") == "W" and rec.get("prior_skills"):
        # the WIPED arm saw prior-scenario state (campaign-2 leak). The
        # bias direction is NOT established (PR #59 review) — exclusion
        # from the verdict plus a rerun is the remedy, not a sign guess.
        flags.append("wipe_leak")
    if rec.get("frozen_drift"):
        flags.append("frozen_drift")
    # held-out episodes actually EXECUTED (the H5 exposure denominator):
    # from the holdout scoring run's own rollout entry — a cell whose
    # deliverable never ran contributes ZERO safety evidence (PR #76
    # review), however its pass rate is scored
    episodes_scored = sum(
        r.get("episodes") or 0
        for r in rollouts
        if str(r.get("run_id") or "").startswith(HOLDOUT_RUN_PREFIX)
    )
    # a session that produced NO deliverable is a scored OUTCOME (0.0 —
    # nothing to run), distinct from an expired scoring window (infra
    # partial); PR #67 follow-up, decided at the rerun campaign. The
    # classification is STRUCTURAL (PR #76 review): the runner's
    # `outcome` field (or, for records predating it, score_holdout's
    # exact error template) plus the full no-score state — anything
    # else unsuccessful stays a partial.
    no_deliverable = (
        (
            holdout.get("outcome") == "no_deliverable"
            or _LEGACY_NO_DELIVERABLE.fullmatch(str(holdout.get("error") or "")) is not None
        )
        and holdout.get("ok") is False
        and holdout.get("pass1") is None
        and holdout.get("pass8") is None
        and not holdout.get("failures")
        and episodes_scored == 0
    )
    if not holdout.get("ok") and not no_deliverable:
        flags.append("holdout_partial")
    first = rec.get("first_success_wall_s")
    if commit:
        # treatment_drift (PR #76 review): a rollout recorded on a
        # different commit, or trusted against a baseline other than the
        # pin, broke the pinned-treatment invariant (ADR-h3: one pinned
        # OID, no mid-campaign repo updates)
        for r in rollouts:
            sha, oid = r.get("git_sha"), r.get("env_baseline_oid")
            if (sha is not None and sha != commit) or (oid is not None and oid != commit):
                flags.append("treatment_drift")
                break
        # unattested_metric (PR #76 review): the rollout that supplies
        # first-success must be a trusted-baseline run at the pin — a
        # local/unattested skill-eval success is not an admissible
        # verdict metric (protocol point 2)
        if first is not None:
            dev_successes = [
                r
                for r in rollouts
                if (r.get("pass1") or 0) > 0
                and not str(r.get("run_id") or "").startswith(HOLDOUT_RUN_PREFIX)
            ]
            if dev_successes:
                src = min(dev_successes, key=lambda r: r.get("mtime") or 0)
                trusted = (
                    src.get("git_sha") == commit
                    and src.get("env_baseline") == "origin/main"
                    and src.get("env_baseline_oid") == commit
                )
                if "git_sha" in src and not trusted:
                    flags.append("unattested_metric")
    # H5 scored on the HELD-OUT records (what we score; dev-side failures
    # are the agent's own and live outside the committed record)
    holdout_failures = holdout.get("failures") or {}
    delivery = {k: holdout_failures[k] for k in DELIVERY_CLASSES if holdout_failures.get(k)}
    placement = {k: holdout_failures[k] for k in PLACEMENT_CLASSES if holdout_failures.get(k)}
    if rec.get("_residue_leak"):
        # derived from the aggregates (PR #67 review): an arm-L scenario
        # that is not the first of its invocation and has NO recorded
        # residue-clear inherited the previous session's working state
        flags.append("residue_leak")
    return {
        "arm": rec.get("arm"),
        "tier": rec.get("tier"),
        "attempt": rec.get("attempt", 1),
        "holdout_pass1": 0.0 if no_deliverable else holdout.get("pass1"),
        "no_deliverable": no_deliverable,
        "episodes_scored": episodes_scored,
        "holdout_failures": holdout_failures,
        "delivery_failures": delivery,
        "delivery_failures_total": sum(delivery.values()),
        "placement_failures": placement,
        "placement_failures_total": sum(placement.values()),
        "stopped": session.get("stopped"),
        "tokens": session.get("tokens"),
        "wall_s": session.get("wall_s"),
        "first_success_wall_s": first,
        "tokens_to_first_success": _tokens_at(rec.get("_token_samples") or [], first),
        "wrong_object_total": rec.get("wrong_object_total"),
        "skills_after": rec.get("skills_after"),
        "skill_reuse_in_deliverable": rec.get("skill_reuse_in_deliverable"),
        "flags": flags,
    }


def _metric(cells: list[dict], arm: str, tier: str, key: str) -> tuple[bool, float | None]:
    """(clean_cell_exists, value). PR #57/#59 reviews: ONLY unflagged
    cells enter the verdict — wipe_leak, frozen_drift, and
    holdout_partial cells all stay out (they remain in the table as
    history); the highest-attempt clean cell wins (the rerun)."""
    eligible = [c for c in cells if c["arm"] == arm and c["tier"] == tier and not c["flags"]]
    if not eligible:
        return False, None
    best = max(eligible, key=lambda c: c["attempt"])
    return True, best[key]


def h3_verdict(cells: list[dict]) -> dict:
    """ADR §7 per tier (S2, S3): L <= 0.5x W on time-to-first-success
    (token ratios reported alongside, §8.4). A tier where L never
    succeeded is not-met; where only W never succeeded, met. met is None
    until every needed CLEAN cell exists; flags become caveats."""
    ratios: dict[str, float | None] = {}
    token_ratios: dict[str, float | None] = {}
    per_tier: dict[str, bool] = {}
    complete = True
    for tier in VERDICT_TIERS:
        have_w, w_first = _metric(cells, "W", tier, "first_success_wall_s")
        have_l, l_first = _metric(cells, "L", tier, "first_success_wall_s")
        if not (have_w and have_l):
            complete = False
            continue
        ratios[tier] = (l_first / w_first) if (w_first and l_first is not None) else None
        _, w_tok = _metric(cells, "W", tier, "tokens_to_first_success")
        _, l_tok = _metric(cells, "L", tier, "tokens_to_first_success")
        token_ratios[tier] = (l_tok / w_tok) if (w_tok and l_tok is not None) else None
        if l_first is None:
            per_tier[tier] = False  # L never succeeded: no transfer shown
        elif w_first is None:
            per_tier[tier] = True  # only L succeeded: transfer at its clearest
        else:
            per_tier[tier] = l_first <= 0.5 * w_first
    caveats = [f"{c['arm']}/{_slot(c)}: {flag}" for c in cells for flag in c["flags"]]
    # met = all(tiers): one tier decided False from clean cells fixes
    # NOT MET regardless of whether the other tier still lacks a clean
    # cell (PR #76 review) — pending only while no tier is decided False
    # and some tier is incomplete
    if any(v is False for v in per_tier.values()):
        met: bool | None = False
    elif not complete or not per_tier:
        met = None
    else:
        met = True
    return {
        "criterion": CRITERION,
        "ratios": ratios,
        "token_ratios": token_ratios,
        "per_tier": per_tier,
        "complete": complete,
        "met": met,
        "caveats": caveats,
    }


def _slot(c: dict) -> str:
    """Tier + attempt identity (matches the runner's slot naming) so rerun
    rows are distinguishable from the cells they supersede (PR #76
    review: two indistinguishable `L | S3` rows misread as duplicates)."""
    attempt = c.get("attempt", 1)
    return c["tier"] + ("" if attempt == 1 else f"-r{attempt}")


def results_markdown(cells: list[dict], verdict: dict) -> str:
    header = (
        "| Arm | Cell | Held-out pass@1 | Session end | Tokens | Wall h "
        "| First success (min) | Tokens@1st | Delivery fails | Placement fails | Reuse | Flags |"
    )
    lines = [header, "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for c in cells:
        pass1 = c["holdout_pass1"]
        shown = "—" if pass1 is None else f"{pass1:.3f}"
        if "holdout_partial" in c["flags"]:
            shown += " (partial)"
        if c.get("no_deliverable"):
            shown += " (no deliverable)"
        first = c["first_success_wall_s"]
        tok1 = c["tokens_to_first_success"]
        deliv = ", ".join(f"{k} {v}" for k, v in c["delivery_failures"].items()) or "0"
        place = ", ".join(f"{k} {v}" for k, v in c["placement_failures"].items()) or "0"
        lines.append(
            f"| {c['arm']} | {_slot(c)} | {shown} | {c['stopped']} "
            f"| {c['tokens']} | {(c['wall_s'] or 0) / 3600:.2f} "
            f"| {'—' if first is None else f'{first / 60:.1f}'} "
            f"| {'—' if tok1 is None else tok1} "
            f"| {deliv} "
            f"| {place} "
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
    try:
        campaign = load_campaign(args.dir)
    except ValueError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        return 1
    cells = [cell(r, campaign["treatment"].get("commit")) for r in campaign["records"]]
    verdict = h3_verdict(cells)
    if args.markdown:
        args.markdown.write_text(results_markdown(cells, verdict))
    total_wrong = sum(c["wrong_object_total"] or 0 for c in cells)
    print(
        json.dumps(
            {
                "ok": True,
                "treatment": campaign["treatment"],
                "cells": cells,
                "verdict": verdict,
                "wrong_object_total": total_wrong,
                "h5": {
                    "selected": h5_totals(select_cells(cells)),
                    "all_records": h5_totals(cells),
                },
            },
            indent=1,
        )
    )
    return 0


def select_cells(cells: list[dict]) -> list[dict]:
    """The verdict's aggregation set: per (arm, tier), the
    highest-attempt CLEAN cell — flagged and superseded cells stay in
    the all_records inventory only (PR #76 review)."""
    selected = []
    for arm in ("W", "L"):
        for tier in TIERS:
            eligible = [
                c for c in cells if c["arm"] == arm and c["tier"] == tier and not c["flags"]
            ]
            if eligible:
                selected.append(max(eligible, key=lambda c: c["attempt"]))
    return selected


def h5_totals(cset: list[dict]) -> dict:
    """H5 (PR #60/#67/#76 reviews): delivery-class (wrong THING) and
    placement-class (wrong WHERE/HOW) totals reported separately, over
    an EXPLICIT cell set, with the executed-episode exposure alongside —
    a no-deliverable cell executed nothing and is zero safety evidence."""
    delivery_by_class: dict[str, int] = {}
    placement_by_class: dict[str, int] = {}
    for c in cset:
        for k, v in c["delivery_failures"].items():
            delivery_by_class[k] = delivery_by_class.get(k, 0) + v
        for k, v in c["placement_failures"].items():
            placement_by_class[k] = placement_by_class.get(k, 0) + v
    return {
        "cells": [f"{c['arm']}/{_slot(c)}" for c in cset],
        "episodes_scored": sum(c["episodes_scored"] for c in cset),
        "delivery_failures_total": sum(delivery_by_class.values()),
        "delivery_failures_by_class": delivery_by_class,
        "placement_failures_total": sum(placement_by_class.values()),
        "placement_failures_by_class": placement_by_class,
    }


if __name__ == "__main__":
    raise SystemExit(main())
