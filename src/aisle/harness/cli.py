"""`harness` CLI entry point (CON-8). Subcommands per SPEC 070:
validate (SPEC 060), rollout (HAR-1..5), traces (HAR-6), report (HAR-7)."""

import argparse
import datetime
import json
import subprocess
import sys
import uuid
from pathlib import Path

from aisle.harness.common import DEFAULT_ROOT, emit_report
from aisle.harness.validate import validate


def _branch(root: Path) -> str:
    return (
        subprocess.run(
            ["git", "branch", "--show-current"], capture_output=True, text=True, cwd=root
        ).stdout.strip()
        or "detached"
    )


def _git_sha(root: Path) -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True, cwd=root
    ).stdout.strip()


def build_parser() -> argparse.ArgumentParser:
    """The full CLI surface (CON-8). Exposed so the research contract's
    copy-paste examples are TESTED against the real argparse tree (T17):
    a doc example that drifts from the CLI fails a unit test."""
    parser = argparse.ArgumentParser(prog="harness", description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    val = subparsers.add_parser("validate", help="validate a dora dataflow YAML (SPEC 060)")
    val.add_argument("graph", type=Path)
    val.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    val.add_argument("--embodiment", default="franka")
    val.add_argument(
        "--allow-unproven",
        action="store_true",
        help="downgrade EVAL_MISSING_FOR_MOTION to a warning (never set for agents)",
    )

    roll = subparsers.add_parser("rollout", help="run seeded episodes through a graph (HAR-1)")
    roll.add_argument("--graph", type=Path, required=True)
    roll.add_argument("--tier", default="T0")
    roll.add_argument("--embodiment", default="franka", choices=["franka", "so101", "mobile"])
    roll.add_argument("--episodes", type=int, required=True)
    roll.add_argument("--seeds", required=True, help="a..b or comma list")
    roll.add_argument("--reset", default="teleport", choices=["teleport", "behavioral"])
    roll.add_argument("--verifier", default="oracle", choices=["oracle", "realistic", "both"])
    roll.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    roll.add_argument(
        "--no-idea-gate",
        action="store_true",
        help="skip the HAR-8 open-idea gate (humans only; recorded in the manifest)",
    )
    roll.add_argument("--run-id", default=None, help="override the generated run id (CON-5)")
    roll.add_argument(
        "--env-baseline",
        default="origin/main",
        help="git ref for the TRUSTED frozen-set baseline (ADR-21); "
        "'local' is the logged dev override (humans only)",
    )
    roll.add_argument("--timeout-s", type=float, default=None)

    tr = subparsers.add_parser("traces", help="query recorded traces (HAR-6)")
    tr_sub = tr.add_subparsers(dest="traces_command", required=True)
    trq = tr_sub.add_parser("query")
    trq.add_argument("--run", required=True)
    trq.add_argument("--topic", required=True)
    trq.add_argument("--t0", type=int, default=None, help="slice start, sim ns")
    trq.add_argument("--t1", type=int, default=None, help="slice end, sim ns (exclusive)")
    trq.add_argument("--episode", type=int, default=None, help="episode index (reset windows)")
    trq.add_argument("--node", default=None, help="producing node id (verified vs the run graph)")
    trq.add_argument("--format", default="json", choices=["json", "npz"])
    trq.add_argument("--out", type=Path, default=None, help="npz output path")
    trq.add_argument("--summarize", action="store_true")
    trq.add_argument("--root", type=Path, default=DEFAULT_ROOT)

    rep = subparsers.add_parser("report", help="idea tree (HAR-7)")
    rep_sub = rep.add_subparsers(dest="report_command", required=True)
    rlog = rep_sub.add_parser("log")
    rlog.add_argument("--idea", required=True)
    rlog.add_argument("--parent", default=None)
    rlog.add_argument("--expect", default=None)
    rlog.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    rclose = rep_sub.add_parser("close")
    rclose.add_argument("--id", required=True)
    rclose.add_argument("--observed", required=True)
    rclose.add_argument("--verdict", required=True, choices=["up", "down", "flat"])
    rclose.add_argument("--root", type=Path, default=DEFAULT_ROOT)

    sk = subparsers.add_parser("skill", help="skill library operations (design doc 8.4)")
    sk_sub = sk.add_subparsers(dest="skill_command", required=True)
    skr = sk_sub.add_parser("register", help="validate + eval + evalcard + install")
    skr.add_argument("skill_dir", type=Path, help="skills/<name>/ directory")
    skr.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    skr.add_argument("--run-id", default=None, help="override the eval run id (CON-5)")
    return parser


def main() -> int:
    args = build_parser().parse_args()

    if args.command == "validate":
        report = validate(args.graph, args.root, args.embodiment, args.allow_unproven)
        return emit_report(
            report,
            lambda level, e: (
                f"validate {level}: {e['code']} at {e.get('edge') or e.get('node')}: {e['detail']}"
            ),
        )

    if args.command == "rollout":
        from aisle.harness.rollout import parse_seed_range, rollout

        run_id = args.run_id or (
            datetime.datetime.now(datetime.UTC).strftime("%Y%m%d-%H%M%S")
            + "-"
            + uuid.uuid4().hex[:6]
        )
        report = rollout(
            root=args.root,
            graph=args.graph,
            tier=args.tier,
            episodes=args.episodes,
            seeds=parse_seed_range(args.seeds),
            reset_mode=args.reset,
            verifier=args.verifier,
            run_id=run_id,
            branch=_branch(args.root),
            no_idea_gate=args.no_idea_gate,
            timeout_s=args.timeout_s,
            embodiment=args.embodiment,
            env_baseline=args.env_baseline,
        )
        return emit_report(report, lambda level, e: f"rollout {level}: {e}")

    if args.command == "skill":
        from aisle.harness.rollout import rollout
        from aisle.harness.skill import RegistrationError, register_skill

        try:
            report = register_skill(
                args.skill_dir,
                args.root,
                run_rollout=rollout,
                now=datetime.date.today().isoformat(),
                run_id=args.run_id,
            )
        except RegistrationError as refused:
            print(json.dumps({"ok": False, "error": str(refused)}))
            return 1
        print(json.dumps(report))
        return 0

    if args.command == "traces":
        from aisle.harness.traces import query

        npz_path = None
        if args.format == "npz":
            npz_path = args.out or (args.root / "runs" / args.run / f"{args.topic}.npz")
        try:
            report = {
                "ok": True,
                **query(
                    args.root / "runs" / args.run,
                    args.topic,
                    t0_ns=args.t0,
                    t1_ns=args.t1,
                    summarize=args.summarize,
                    episode=args.episode,
                    node=args.node,
                    npz_path=npz_path,
                ),
            }
        except FileNotFoundError as missing:
            report = {"ok": False, "error": str(missing)}
        return emit_report(report, lambda level, e: f"traces {level}: {e}")

    if args.command == "report":
        from aisle.harness.ideas import close_idea, log_idea

        ts = datetime.datetime.now(datetime.UTC).isoformat()
        branch = _branch(args.root)
        try:
            if args.report_command == "log":
                entry = log_idea(
                    args.root, branch, args.idea, ts, _git_sha(args.root), args.parent, args.expect
                )
            else:
                entry = close_idea(args.root, branch, args.id, args.observed, args.verdict, ts)
            report = {"ok": True, **entry}
        except ValueError as bad:
            report = {"ok": False, "error": str(bad)}
        return emit_report(report, lambda level, e: f"report {level}: {e}")

    raise AssertionError("unreachable: argparse enforces a known subcommand")


if __name__ == "__main__":
    sys.exit(main())
