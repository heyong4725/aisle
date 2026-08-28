"""paper_figures — every figure in docs/paper/ regenerated from committed
records (the derived-never-hand-written discipline, extended to graphics).

Each figure function reads ONLY committed analysis/ records or committed
run artifacts and writes a PNG under docs/paper/figures/. Where a raw
record was purged and the durable record is a findings table, the values
are transcribed in ONE place here with the source cited — the caption
in the paper repeats the citation. Deterministic output: fixed style,
no timestamps. CON-8: JSON manifest on stdout, logs stderr, exit 0 iff
every figure regenerated.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "docs" / "paper" / "figures"
plt.rcParams.update(
    {
        "figure.dpi": 150,
        "font.size": 9,
        "axes.grid": True,
        "grid.alpha": 0.3,
        "axes.spines.top": False,
        "axes.spines.right": False,
        "svg.hashsalt": "aisle",
    }
)
C_CLAUDE, C_CODEX, C_A, C_B, C_C = "#4C72B0", "#DD8452", "#55A868", "#C44E52", "#8172B3"


def _save(fig, name: str) -> str:
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / name
    fig.savefig(path, bbox_inches="tight", metadata={"Software": "aisle paper_figures"})
    plt.close(fig)
    return str(path.relative_to(REPO))


def fig_h1_funnel() -> tuple[str, list[str]]:
    """H1 composition funnel per agent: valid -> launched -> any-pass."""
    sources, rows = [], {}
    for agent in ("claude", "codex"):
        src = REPO / "analysis" / "h1" / f"h1_attempts_{agent}.json"
        sources.append(str(src.relative_to(REPO)))
        attempts = json.loads(src.read_text())["attempts"]
        valid = sum(1 for a in attempts if a["valid"])
        launched = sum(1 for a in attempts if a.get("launch") == "launched")
        passed = sum(1 for a in attempts if (a.get("pass1") or 0) > 0)
        rows[agent] = (len(attempts), valid, launched, passed)
    fig, ax = plt.subplots(figsize=(4.6, 2.8))
    stages = ["attempts", "schema-valid", "launched", "pass@1 > 0"]
    x = range(len(stages))
    for i, (agent, vals) in enumerate(rows.items()):
        color = C_CLAUDE if agent == "claude" else C_CODEX
        ax.bar([p + (i - 0.5) * 0.38 for p in x], vals, width=0.36, label=agent, color=color)
        for p, v in zip(x, vals, strict=True):
            ax.text(p + (i - 0.5) * 0.38, v + 0.3, str(v), ha="center", fontsize=8)
    ax.set_xticks(list(x), stages)
    ax.set_ylabel("graphs (of 20 per agent)")
    ax.set_title("H1: composition is schema-solved, launchability-limited")
    ax.legend(frameon=False)
    return _save(fig, "h1_funnel.png"), sources


def fig_a5_fleet() -> tuple[str, list[str]]:
    """A5 fleet scaling: throughput saturates; per-agent tokens rise."""
    src = REPO / "analysis" / "a5" / "a5_results.json"
    configs = json.loads(src.read_text())["configs"]
    ns, thr, tok = [], [], []
    for c in sorted(configs, key=lambda c: c["fleet"]):
        agents = c["agents"]
        ns.append(c["fleet"])
        wall_h = c["config_wall_s"] / 3600
        first = [
            a.get("first_success_wall_s")
            for a in agents
            if a.get("first_success_wall_s") is not None
        ]
        thr.append(len(first) / wall_h if wall_h else 0)
        tok.append(sum(a["session"]["tokens"] for a in agents) / len(agents) / 1000)
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(6.4, 2.6))
    ax1.plot(ns, thr, "o-", color=C_A)
    ax1.set_xlabel("agents (N)")
    ax1.set_ylabel("first successes / hour")
    ax1.set_title("throughput saturates ~4 lanes")
    ax1.set_xticks(ns)
    ax2.plot(ns, tok, "s-", color=C_B)
    ax2.set_xlabel("agents (N)")
    ax2.set_ylabel("mean tokens / agent (k)")
    ax2.set_title("token super-linearity")
    ax2.set_xticks(ns)
    fig.suptitle("A5: fleet scaling on one host", y=1.04)
    return _save(fig, "a5_fleet.png"), [str(src.relative_to(REPO))]


def fig_t2_arc() -> tuple[str, list[str]]:
    """The T2 arc: expert wall -> breakthrough -> registered stack, with
    the four pre-registered n=8 re-measures and their failure classes."""
    sources = ["analysis/t2/t2_curve_findings.md", "analysis/t2_breakthrough/"]
    stage_names = ["expert\n(0.08)", "breakthrough\n(0.375 holdout)", "registered stack\n(0.5 n=8)"]
    stage_vals = [0.08, 0.375, 0.5]
    runs = {
        "registration": REPO / "runs" / "t2-stack-registration",
        "post-#314": REPO / "runs" / "t2-diverge-remeasure",
        "post-#325": REPO / "runs" / "t2-flipfilter-remeasure",
        "post-#328": REPO / "runs" / "t2-scope-v2",
    }
    classes = ["success", "collision", "never_grasped", "dropped"]
    colors = {"success": C_A, "collision": C_B, "never_grasped": "#937860", "dropped": C_C}
    mix = {}
    for label, rd in runs.items():
        eps = [json.loads(x) for x in (rd / "episodes.jsonl").read_text().splitlines() if x.strip()]
        sources.append(str((rd / "episodes.jsonl").relative_to(REPO)))
        counts = {c: 0 for c in classes}
        for e in eps:
            key = e["status"] if e["status"] == "success" else e["failure"]
            counts[key] = counts.get(key, 0) + 1
        mix[label] = counts
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(7.0, 2.8), width_ratios=[1, 1.3])
    ax1.bar(range(3), stage_vals, color=[C_C, C_B, C_A], width=0.6)
    for i, v in enumerate(stage_vals):
        ax1.text(i, v + 0.01, f"{v:.3g}", ha="center", fontsize=8)
    ax1.set_xticks(range(3), stage_names, fontsize=7)
    ax1.set_ylabel("pass@1")
    ax1.set_title("the wall broke in stages")
    bottoms = [0.0] * len(mix)
    for cls in classes:
        vals = [mix[label].get(cls, 0) for label in mix]
        ax2.bar(range(len(mix)), vals, bottom=bottoms, label=cls, color=colors[cls], width=0.6)
        bottoms = [b + v for b, v in zip(bottoms, vals, strict=True)]
    ax2.set_xticks(range(len(mix)), list(mix), fontsize=7)
    ax2.set_ylabel("episodes (of 8)")
    ax2.set_title("failure mix across the fix ledger")
    ax2.legend(frameon=False, fontsize=7, ncol=2)
    fig.suptitle("T2: 0.08 → 0.375 → 0.5, and what the residual is made of", y=1.06)
    return _save(fig, "t2_arc.png"), sources


def fig_h6_timelines() -> tuple[str, list[str]]:
    """H6 per-cell operation timeline from the raw cell records."""
    sources, rows = [], []
    for cell in ("F1", "F2", "F3"):
        src = REPO / "analysis" / "h6" / "records" / cell / "cell.json"
        sources.append(str(src.relative_to(REPO)))
        r = json.loads(src.read_text())
        t0 = r["inject_ts"]
        detect = r["diagnosis"]["ts"] - t0
        repair = r["repair"]["ts"] - t0
        end = max(r["timeline"]) - r["stream_t0"] if r.get("timeline") else repair + 120
        rows.append((cell, r["node"], detect, repair, min(end, repair + 400)))
    fig, ax = plt.subplots(figsize=(6.2, 2.4))
    for i, (cell, node, detect, repair, end) in enumerate(rows):
        y = len(rows) - 1 - i
        ax.barh(y, detect, color=C_B, height=0.5, label="fault active" if i == 0 else None)
        ax.barh(
            y,
            repair - detect,
            left=detect,
            color=C_C,
            height=0.5,
            label="diagnosed → repairing" if i == 0 else None,
        )
        ax.barh(
            y,
            end - repair,
            left=repair,
            color=C_A,
            height=0.5,
            label="restored (post window 1.0)" if i == 0 else None,
        )
        ax.text(detect, y + 0.32, f"detect {detect:.0f}s", fontsize=7)
        ax.text(repair, y - 0.45, f"repair +{repair - detect:.0f}s", fontsize=7)
        ax.text(-8, y, f"{cell}\n{node}", ha="right", va="center", fontsize=7)
    ax.set_yticks([])
    ax.set_xlabel("seconds since fault injection")
    ax.set_title("H6: detect → localize → repair → restore, per cell (3/3 PASS)")
    ax.legend(frameon=False, fontsize=7, loc="lower right")
    return _save(fig, "h6_timelines.png"), sources


def fig_m1_mix() -> tuple[str, list[str]]:
    """M1 live vs lockstep failure mix (same adapter, same seeds)."""
    live_src = "analysis/m1/m1_zeroshot_findings.md"  # live mix per findings
    live = {"never_grasped": 5, "wall_clamp": 3, "collision": 0, "dropped": 0}
    lock_src = REPO / "runs" / "m1-lockstep-n8" / "episodes.jsonl"
    eps = [json.loads(x) for x in lock_src.read_text().splitlines() if x.strip()]
    lock = {"never_grasped": 0, "wall_clamp": 0, "collision": 0, "dropped": 0}
    for e in eps:
        lock[e["failure"]] = lock.get(e["failure"], 0) + 1
    classes = ["never_grasped", "wall_clamp", "collision", "dropped"]
    fig, ax = plt.subplots(figsize=(4.8, 2.6))
    x = range(len(classes))
    ax.bar(
        [p - 0.19 for p in x],
        [live[c] for c in classes],
        width=0.36,
        label="live (latency-dominated)",
        color=C_C,
    )
    ax.bar(
        [p + 0.19 for p in x],
        [lock[c] for c in classes],
        width=0.36,
        label="lockstep (latency removed)",
        color=C_CLAUDE,
    )
    ax.set_xticks(list(x), classes, fontsize=8)
    ax.set_ylabel("episodes (of 8)")
    ax.set_title("M1: the failure mix inverts when latency is removed (both 0/8)")
    ax.legend(frameon=False, fontsize=8)
    return _save(fig, "m1_mix.png"), [live_src, str(lock_src.relative_to(REPO))]


def fig_judge_tally() -> tuple[str, list[str]]:
    """Five judge configurations vs the promotion gate."""
    # (config, agreement, false_success, holdout_n, success_recall, source)
    # the v1-era corpus had a 5-episode holdout; the extended corpus 13 —
    # rates must carry their own denominator
    rows = [
        (
            "500M calibrated\n(5-ep holdout)",
            0.2,
            4,
            5,
            None,
            "analysis/ver-vlm/vlm_judge_findings.md",
        ),
        ("2B calibrated\n(5-ep holdout)", 0.6, 2, 5, 1.0, "analysis/ver-vlm/vlm_judge_findings.md"),
        ("2B semantic\n(13-ep)", 0.615, 0, 13, 0.0, "analysis/ver-vlm/bench_2b_ext_semantic.jsonl"),
        (
            "2B calibrated\n(13-ep)",
            0.538,
            5,
            13,
            0.8,
            "analysis/ver-vlm/bench_2b_ext_calibrated.jsonl",
        ),
        ("2B label\n(13-ep)", 0.615, 0, 13, 0.0, "analysis/ver-vlm/bench_2b_ext_label.jsonl"),
    ]
    fig, ax = plt.subplots(figsize=(5.6, 2.8))
    x = range(len(rows))
    ax.bar(
        [p - 0.2 for p in x],
        [r[1] for r in rows],
        width=0.38,
        label="holdout agreement",
        color=C_CLAUDE,
    )
    ax.axhline(0.8, color=C_A, lw=1, ls="--", label="gate floor 0.8")
    for p, r in zip(x, rows, strict=True):
        ax.bar(
            p + 0.2,
            r[2] / r[3],
            width=0.38,
            color=C_C,
            label="false-success rate" if p == 0 else None,
        )
        note = "recall 0" if r[4] == 0.0 else (f"fs={r[2]}/{r[3]}" if r[2] else "")
        if note:
            ax.text(p, 0.86, note, ha="center", fontsize=7, color=C_C)
    ax.set_xticks(list(x), [r[0] for r in rows], fontsize=6.5)
    ax.set_ylim(0, 1.0)
    ax.set_title("Five judge configurations, five refusals, zero false promotions")
    ax.legend(frameon=False, fontsize=7)
    return _save(fig, "judge_tally.png"), sorted({r[5] for r in rows})


def fig_m3_scatter() -> tuple[str, list[str]]:
    """M3: Genesis vs surrogate pass@1 per graph — the variance problem."""
    src = REPO / "analysis" / "m3" / "records.json"
    records = json.loads(src.read_text())
    import collections

    counts = collections.Counter((r["pass1_genesis"], r["pass1_surrogate"]) for r in records)
    fig, ax = plt.subplots(figsize=(3.6, 3.2))
    for (gx, sy), n in counts.items():
        ax.scatter(gx, sy, s=60 + 60 * n, color=C_CLAUDE, alpha=0.75)
        ax.annotate(f"n={n}", (gx, sy), textcoords="offset points", xytext=(8, 4), fontsize=7)
    ax.plot([0, 1], [0, 1], ls=":", color="gray", lw=1)
    ax.set_xlim(0.6, 1.05)
    ax.set_ylim(0.6, 1.05)
    ax.set_xlabel("Genesis pass@1")
    ax.set_ylabel("surrogate pass@1")
    ax.set_title("M3: ranking is undecidable\nwithout population variance")
    return _save(fig, "m3_scatter.png"), [str(src.relative_to(REPO))]


def fig_cost_bars() -> tuple[str, list[str]]:
    """Session-cost contrasts: A3 arms, A4 arms, H3 T2-differential arms.
    A3 parsed from its results.json; A4 + differential transcribed from
    their findings tables (the durable records; captions cite them)."""
    src_a3 = REPO / "analysis" / "a3" / "a3_results.json"
    a3 = json.loads(src_a3.read_text())["records"]
    a3_tokens = {r["arm"]: r["session"]["tokens"] / 1000 for r in a3}
    groups = [
        ("A3\nparams-only", a3_tokens.get("P", 200), C_A),
        ("A3\nparams+code", a3_tokens.get("C", 396), C_B),
        ("A4\nClaude", 186, C_CLAUDE),
        ("A4\nCodex", 364, C_CODEX),
        ("H3-diff\nlibrary", 451, C_A),
        ("H3-diff\nwiped", 696, C_B),
    ]
    fig, ax = plt.subplots(figsize=(5.4, 2.6))
    x = range(len(groups))
    ax.bar(x, [g[1] for g in groups], color=[g[2] for g in groups], width=0.62)
    for p, g in zip(x, groups, strict=True):
        ax.text(p, g[1] + 8, f"{g[1]:.0f}k", ha="center", fontsize=8)
    ax.set_xticks(list(x), [g[0] for g in groups], fontsize=7.5)
    ax.set_ylabel("session tokens (k)")
    ax.set_title("Equal quality, unequal cost: every pair matched on outcome")
    return _save(fig, "cost_bars.png"), [
        str(src_a3.relative_to(REPO)),
        "analysis/a4/a4_findings.md",
        "analysis/h3/t2_differential/findings.md",
    ]


def fig_pw0() -> tuple[str, list[str]]:
    """PW-0 solver throughput; values from the ratified ADR tables (the
    durable record — raw sweep JSON was purged; the ADR is Class-B
    ratified evidence)."""
    src = "docs/decisions/ADR-powder-spike.md"
    n = [4913, 19683, 50653]
    mpm, pbd, sph = [154, 130, 94], [113, 56, 26], [148, 111, 74]
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(6.4, 2.6))
    ax1.plot(n, mpm, "o-", label="MPM sand", color=C_A)
    ax1.plot(n, pbd, "s-", label="PBD", color=C_B)
    ax1.plot(n, sph, "^-", label="SPH (liquid)", color=C_C)
    ax1.set_xlabel("particles")
    ax1.set_ylabel("steps/s (Metal)")
    ax1.set_xscale("log")
    ax1.set_title("solver throughput")
    ax1.legend(frameon=False, fontsize=7)
    cases = ["Metal 4mm\n50.7k", "Metal 2mm\n39.3k", "CPU 2mm\n39.3k"]
    vals = [91.9, 27.4, 12.8]
    ax2.bar(range(3), vals, color=[C_B, C_CLAUDE, C_A], width=0.6)
    for i, v in enumerate(vals):
        ax2.text(i, v + 1.5, f"{v}", ha="center", fontsize=8)
    ax2.set_xticks(range(3), cases, fontsize=7)
    ax2.set_ylabel("steps/s")
    ax2.set_title("the 2 mm probe: grid dominates")
    fig.suptitle("PW-0: the determinism/throughput/fidelity trilemma, measured", y=1.06)
    return _save(fig, "pw0_throughput.png"), [src]


FIGURES = [
    fig_h1_funnel,
    fig_a5_fleet,
    fig_t2_arc,
    fig_h6_timelines,
    fig_m1_mix,
    fig_judge_tally,
    fig_m3_scatter,
    fig_cost_bars,
    fig_pw0,
]


def main() -> int:
    manifest = []
    for fn in FIGURES:
        try:
            path, sources = fn()
            manifest.append({"figure": path, "sources": sources})
            print(f"[figures] {path}", file=sys.stderr)
        except Exception as exc:  # noqa: BLE001 — report and fail closed
            manifest.append({"figure": fn.__name__, "error": str(exc)})
            print(f"[figures] FAIL {fn.__name__}: {exc}", file=sys.stderr)
    ok = all("error" not in m for m in manifest)
    print(json.dumps({"ok": ok, "figures": manifest}, indent=1))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
