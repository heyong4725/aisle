"""`harness` CLI entry point (CON-8). Subcommands per SPEC 070:
validate (SPEC 060), rollout (HAR-1..5), traces (HAR-6), report (HAR-7)."""

import argparse
import datetime
import hashlib
import json
import os
import platform
import subprocess
import sys
import uuid
from pathlib import Path

from aisle.harness.common import DEFAULT_ROOT, emit_report
from aisle.harness.validate import validate


class _JsonArgumentParser(argparse.ArgumentParser):
    """Keep argument refusals inside the CON-8 JSON stdout contract."""

    def error(self, message: str) -> None:
        print(json.dumps({"ok": False, "error": "invalid arguments", "details": [message]}))
        raise SystemExit(2)


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
    parser = _JsonArgumentParser(prog="harness", description=__doc__)
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
    val.add_argument(
        "--write-turn-plan",
        action="store_true",
        help="rewrite the graph's committed ADR-30 turn plan from its topology (issue #227)",
    )

    roll = subparsers.add_parser("rollout", help="run seeded episodes through a graph (HAR-1)")
    roll.add_argument("--graph", type=Path, required=True)
    roll.add_argument("--tier", default="T0")
    roll.add_argument("--embodiment", default="franka", choices=["franka", "so101", "mobile"])
    roll.add_argument("--episodes", type=int, required=True)
    roll.add_argument("--seeds", required=True, help="a..b or comma list")
    roll.add_argument("--reset", default="teleport", choices=["teleport", "behavioral"])
    roll.add_argument("--verifier", default="oracle", choices=["oracle", "realistic", "both"])
    roll.add_argument(
        "--sim-extra",
        default="sim",
        choices=["sim", "cuda"],
        help="attested dependency/backend selection: portable sim or Linux CUDA",
    )
    roll.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    roll.add_argument(
        "--no-idea-gate",
        action="store_true",
        help="skip the HAR-8 open-idea gate (humans only; recorded in the manifest)",
    )
    roll.add_argument(
        "--perception",
        default=None,
        choices=["L0", "L1", "L2"],
        help="assert the graph's declared perception rung (TC-9); the rung "
        "rides the graph, so a mismatch refuses the run",
    )
    roll.add_argument("--run-id", default=None, help="override the generated run id (CON-5)")
    roll.add_argument(
        "--per-episode-wall-s",
        type=int,
        default=None,
        help="override the tier's per-episode WALL clamp (lockstep VLA eval, ADR-38 amendment)",
    )
    roll.add_argument(
        "--env-baseline",
        default=os.environ.get("AISLE_ENV_BASELINE", "origin/main"),
        help="TRUSTED frozen-set baseline: protected server main or a "
        "campaign-pinned main-history OID (ADR-21); 'local' is the logged "
        "dev override (humans only)",
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
    skr.add_argument(
        "--sandbox",
        action="store_true",
        help="ADR-40: admit the id for VALIDATION only — no eval, no evalcard, "
        "no quality claim, never safety_class motion, never counted as a "
        "library skill. Promote by re-registering without this flag.",
    )

    sw = subparsers.add_parser("swap", help="hot-swap a node on a live dataflow (HAR-10)")
    sw.add_argument("--graph", type=Path, required=True)
    sw.add_argument("--dataflow", required=True)
    sw.add_argument("--replace", required=True, dest="node_id")
    sw.add_argument("--with", type=Path, required=True, dest="with_yaml")
    sw.add_argument("--embodiment", default="franka")
    sw.add_argument("--root", type=Path, default=DEFAULT_ROOT)

    fl = subparsers.add_parser("fleet", help="N agents on one batched sim (design doc 8.4.3)")
    fl.add_argument("--graph", type=Path, required=True, help="single-env base graph to stamp")
    fl.add_argument("--agents", type=int, required=True)
    fl.add_argument("--episodes", type=int, required=True, help="episodes per agent")
    fl.add_argument("--seeds", required=True, help="a..b or comma list (per-agent episode seeds)")
    fl.add_argument("--out", type=Path, default=None, help="output dir (default runs/fleet-<ts>)")
    fl.add_argument("--timeout-s", type=float, default=1200.0)
    fl.add_argument("--root", type=Path, default=DEFAULT_ROOT)

    pr = subparsers.add_parser("probe", help="attach a temporary topic inspector (HAR-11)")
    pr.add_argument("--dataflow", required=True)
    pr.add_argument("--topic", required=True)
    pr.add_argument("--for", type=float, default=30.0, dest="seconds")
    pr.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    skr.add_argument("--run-id", default=None, help="override the eval run id (CON-5)")

    stats = subparsers.add_parser(
        "stats", help="session-level benchmark statistics and power (SPEC 400)"
    )
    stats_sub = stats.add_subparsers(dest="stats_command", required=True)
    stats_analyze = stats_sub.add_parser("analyze", help="analyze retained raw session records")
    stats_analyze.add_argument("--protocol", type=Path, required=True)
    stats_analyze.add_argument("--records", type=Path, required=True)
    stats_analyze.add_argument("--output", type=Path, default=None)
    stats_power = stats_sub.add_parser("power", help="power analysis from frozen assumptions")
    stats_power.add_argument("--protocol", type=Path, required=True)
    stats_power.add_argument("--output", type=Path, default=None)
    stats_validate = stats_sub.add_parser(
        "validate", help="validate a protocol for power, analysis, or confirmatory freeze"
    )
    stats_validate.add_argument("--protocol", type=Path, required=True)
    stats_validate.add_argument(
        "--purpose", choices=["power", "analysis", "freeze"], default="analysis"
    )
    stats_validate.add_argument("--output", type=Path, default=None)

    freeze = subparsers.add_parser(
        "freeze", help="content-addressed campaign freeze registry (CSE-15, SFE-9, SEM-9, BND-12)"
    )
    freeze_sub = freeze.add_subparsers(dest="freeze_command", required=True)
    freeze_build = freeze_sub.add_parser("build", help="hash a declaration into a manifest")
    freeze_build.add_argument("--declaration", type=Path, required=True)
    freeze_build.add_argument("--output", type=Path, default=None)
    freeze_build.add_argument("--timestamp", default=None, help="ISO-8601 with zone")
    freeze_build.add_argument("--timestamp-source", default=None, help="who issued it")
    freeze_build.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    freeze_check = freeze_sub.add_parser("check", help="recompute a manifest; drift is ok:false")
    freeze_check.add_argument("--manifest", type=Path, required=True)
    freeze_check.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    freeze_check.add_argument(
        "--allow-withheld-seeds",
        action="store_true",
        help="seed sources are withheld on this host: report the commitment as unverified",
    )

    exposure = subparsers.add_parser(
        "exposure", help="safety exposure ledger and zero-event report (SPEC 470)"
    )
    exposure_sub = exposure.add_subparsers(dest="exposure_command", required=True)
    exposure_ledger = exposure_sub.add_parser("ledger", help="derive a run's exposure ledger")
    exposure_ledger.add_argument("--run", type=Path, required=True)
    exposure_ledger.add_argument("--campaign-id", required=True)
    exposure_ledger.add_argument("--source-map", type=Path, required=True)
    exposure_ledger.add_argument("--output", type=Path, default=None)
    exposure_analyze = exposure_sub.add_parser("analyze", help="regenerate the exposure report")
    exposure_analyze.add_argument("--ledger", type=Path, action="append", required=True)
    exposure_analyze.add_argument("--confidence", type=float, default=0.95)
    exposure_analyze.add_argument("--output", type=Path, default=None)
    exposure_corpus = exposure_sub.add_parser(
        "corpus", help="deterministic fixed-proposal trace corpus (SFE-9, SFE-11)"
    )
    exposure_corpus.add_argument("--embodiment", choices=["franka", "so101"], default="franka")
    exposure_corpus.add_argument("--seed", type=int, required=True)
    exposure_corpus.add_argument("--per-family", type=int, default=8)
    exposure_corpus.add_argument("--output", type=Path, default=None)
    exposure_ablate = exposure_sub.add_parser(
        "ablate", help="guard_on vs guard_observe_only on a fake driver (SFE-10..12)"
    )
    exposure_ablate.add_argument("--corpus", type=Path, required=True)
    exposure_ablate.add_argument("--analysis-seed", type=int, required=True)
    exposure_ablate.add_argument("--output", type=Path, default=None)

    semantic = subparsers.add_parser(
        "semantic", help="semantic authorization held-plan corpus and replay (SPEC 480)"
    )
    semantic_sub = semantic.add_subparsers(dest="semantic_command", required=True)
    semantic_corpus = semantic_sub.add_parser("corpus", help="build the SEM-10 held-plan corpus")
    semantic_corpus.add_argument("--seed", type=int, required=True)
    semantic_corpus.add_argument("--per-condition", type=int, default=4)
    semantic_corpus.add_argument("--output", type=Path, default=None)
    semantic_run = semantic_sub.add_parser("run", help="replay every plan through the three arms")
    semantic_run.add_argument("--corpus", type=Path, required=True)
    semantic_run.add_argument("--analysis-seed", type=int, required=True)
    semantic_run.add_argument("--output", type=Path, default=None)

    hardware = subparsers.add_parser(
        "hardware", help="SO-101 hardware phase dry run and report (SPEC 520)"
    )
    hardware_sub = hardware.add_subparsers(dest="hardware_command", required=True)
    hardware_dry = hardware_sub.add_parser("dry-run", help="HWP-8 scenarios on doubles")
    hardware_dry.add_argument("--seed", type=int, default=0)
    hardware_dry.add_argument("--output", type=Path, default=None)
    hardware_report = hardware_sub.add_parser("report", help="HWP-18/20 report; hardware_pending")
    hardware_report.add_argument("--station", type=Path, default=None)
    hardware_report.add_argument("--output", type=Path, default=None)
    return parser


def _maybe_gunzip(path: Path) -> bytes:
    data = path.read_bytes()
    if path.suffix == ".gz":
        import gzip

        return gzip.decompress(data)
    return data


def main() -> int:
    args = build_parser().parse_args()

    if args.command == "hardware":
        from aisle.hardware.adapters import SCENARIOS, run_scenario
        from aisle.harness.hardware_phase import (
            HardwarePhaseError,
            hardware_report,
            validate_telemetry_stream,
        )

        try:
            if args.hardware_command == "dry-run":
                scenarios = {}
                for name in SCENARIOS:
                    result = run_scenario(name, seed=args.seed)
                    scenarios[name] = {
                        "observations": result["observations"],
                        "telemetry": validate_telemetry_stream(result["rows"]),
                    }
                report = {
                    "ok": True,
                    "schema_version": "aisle.hardware-phase.dry-run.v1",
                    "evidence_kind": "loopback",
                    "scenarios": scenarios,
                    "wording": "doubles only; no realized-hardware field is produced here",
                }
            else:
                station = json.loads(args.station.read_bytes()) if args.station else None
                report = hardware_report(
                    station=station,
                    motor_calibration=None,
                    workspace_calibration=None,
                    telemetry=None,
                    interventions=None,
                )
                report["ok"] = True
            if args.output is not None:
                args.output.parent.mkdir(parents=True, exist_ok=True)
                args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
        except HardwarePhaseError as refused:
            report = {"ok": False, "error": str(refused), "details": refused.details}
        except (OSError, json.JSONDecodeError, ValueError) as refused:
            report = {"ok": False, "error": "hardware command refused", "details": [repr(refused)]}
        print(json.dumps(report, sort_keys=True))
        return 0 if report["ok"] else 1

    if args.command == "semantic":
        from aisle.harness.semantic_corpus import build_corpus as build_semantic_corpus
        from aisle.harness.semantic_corpus import run_corpus

        try:
            if args.semantic_command == "corpus":
                report = build_semantic_corpus(seed=args.seed, per_condition=args.per_condition)
                report["ok"] = True
            else:
                corpus = json.loads(_maybe_gunzip(args.corpus))
                report = run_corpus(corpus, analysis_seed=args.analysis_seed)
            if args.output is not None:
                args.output.parent.mkdir(parents=True, exist_ok=True)
                args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
        except (OSError, json.JSONDecodeError, KeyError, ValueError) as refused:
            report = {"ok": False, "error": "semantic replay refused", "details": [repr(refused)]}
        summary = {k: v for k, v in report.items() if k not in ("plans", "runs")}
        print(json.dumps(summary, sort_keys=True))
        return 0 if report["ok"] else 1

    if args.command == "exposure":
        from aisle.harness.exposure import ExposureError, ledger_for_run, sha256_file
        from aisle.harness.exposure_report import analyze_ledgers

        try:
            if args.exposure_command in ("corpus", "ablate"):
                from aisle.harness.held_command import HeldCommandError, build_corpus, run_ablation
                from aisle.nodes.budget_guard import load_limits

                try:
                    if args.exposure_command == "corpus":
                        report = build_corpus(
                            load_limits(args.embodiment),
                            embodiment=args.embodiment,
                            seed=args.seed,
                            per_family=args.per_family,
                        )
                        report["ok"] = True
                    else:
                        corpus = json.loads(_maybe_gunzip(args.corpus))
                        report = run_ablation(
                            corpus,
                            load_limits(corpus["embodiment"]),
                            analysis_seed=args.analysis_seed,
                        )
                except HeldCommandError as refused:
                    raise ExposureError(str(refused), refused.details) from refused
            elif args.exposure_command == "ledger":
                source_map = json.loads(args.source_map.read_bytes())
                report = ledger_for_run(
                    args.run.resolve(), campaign_id=args.campaign_id, source_map=source_map
                )
                report["source_map_hash"] = sha256_file(args.source_map)
                report["ok"] = True
            else:
                ledgers = [json.loads(_maybe_gunzip(p)) for p in args.ledger]
                report = analyze_ledgers(
                    ledgers,
                    confidence=args.confidence,
                    input_hashes={str(p): sha256_file(p) for p in args.ledger},
                )
            if args.output is not None:
                args.output.parent.mkdir(parents=True, exist_ok=True)
                payload = (json.dumps(report, indent=2, sort_keys=True) + "\n").encode()
                if args.output.suffix == ".gz":
                    import gzip

                    args.output.write_bytes(gzip.compress(payload, mtime=0))
                else:
                    args.output.write_bytes(payload)
        except ExposureError as refused:
            report = {"ok": False, "error": str(refused), "details": refused.details}
        except (OSError, json.JSONDecodeError, KeyError, ValueError) as refused:
            report = {"ok": False, "error": "input read failed", "details": [repr(refused)]}
        summary = {
            k: v
            for k, v in report.items()
            if k not in ("proposals", "episodes", "observed_envelope", "traces", "pairs")
        }
        print(json.dumps(summary, sort_keys=True))
        return 0 if report["ok"] else 1

    if args.command == "freeze":
        import subprocess as git_subprocess

        from aisle.harness.freeze import FreezeError, build_manifest, check_manifest

        try:
            if args.freeze_command == "build":
                declaration = json.loads(args.declaration.read_bytes())
                head = git_subprocess.run(
                    ["git", "rev-parse", "HEAD"], capture_output=True, text=True, cwd=args.root
                )
                manifest = build_manifest(
                    args.root.resolve(),
                    declaration,
                    git_head=head.stdout.strip() if head.returncode == 0 else None,
                    timestamp=args.timestamp,
                    timestamp_source=args.timestamp_source,
                )
                report = {"ok": True, **manifest}
                if args.output is not None:
                    if args.output.resolve() == args.declaration.resolve():
                        raise FreezeError("output path collides with an input", [str(args.output)])
                    args.output.parent.mkdir(parents=True, exist_ok=True)
                    args.output.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
            else:
                report = check_manifest(
                    args.root.resolve(),
                    json.loads(args.manifest.read_bytes()),
                    require_seed_sources=not args.allow_withheld_seeds,
                )
        except FreezeError as refused:
            report = {"ok": False, "error": str(refused), "details": refused.details}
        except (OSError, json.JSONDecodeError) as refused:
            report = {"ok": False, "error": "input read failed", "details": [str(refused)]}
        print(json.dumps(report, sort_keys=True))
        return 0 if report["ok"] else 1

    if args.command == "stats":
        import aisle.harness.benchmark_statistics as statistics_module
        from aisle.harness.benchmark_statistics import (
            StatisticsInputError,
            analyze_campaign,
            power_analysis,
            protocol_core_hash,
            validate_protocol,
        )

        try:
            protocol_bytes = args.protocol.read_bytes()
            protocol = json.loads(protocol_bytes)
            protocol_hash = hashlib.sha256(protocol_bytes).hexdigest()
            if args.stats_command == "validate":
                errors = validate_protocol(protocol, purpose=args.purpose)
                report = {
                    "ok": not errors,
                    "schema_version": "aisle.stats.validation.v1",
                    "protocol_id": protocol.get("protocol_id")
                    if isinstance(protocol, dict)
                    else None,
                    "protocol_core_sha256": protocol_core_hash(protocol)
                    if isinstance(protocol, dict)
                    else None,
                    "purpose": args.purpose,
                    "input_hashes": {"protocol_sha256": protocol_hash},
                    "errors": errors,
                }
            elif args.stats_command == "power":
                report = power_analysis(protocol, protocol_hash=protocol_hash)
            else:
                records_bytes = args.records.read_bytes()
                records = json.loads(records_bytes)
                report = analyze_campaign(
                    protocol,
                    records,
                    input_hashes={
                        "protocol_sha256": protocol_hash,
                        "records_sha256": hashlib.sha256(records_bytes).hexdigest(),
                    },
                )
            implementation_path = Path(statistics_module.__file__)
            report["execution_environment"] = {
                "python": platform.python_version(),
                "python_implementation": platform.python_implementation(),
                "system": platform.system(),
                "release": platform.release(),
                "machine": platform.machine(),
                "analysis_implementation_sha256": hashlib.sha256(
                    implementation_path.read_bytes()
                ).hexdigest(),
            }
        except StatisticsInputError as refused:
            report = {"ok": False, "error": str(refused), "details": refused.details}
        except (OSError, json.JSONDecodeError) as refused:
            report = {"ok": False, "error": "input read failed", "details": [str(refused)]}
        if args.output is not None:
            inputs = {args.protocol.resolve()}
            if args.stats_command == "analyze":
                inputs.add(args.records.resolve())
            if args.output.resolve() in inputs:
                report = {
                    "ok": False,
                    "error": "output path collides with an input",
                    "details": [str(args.output)],
                }
            else:
                try:
                    args.output.parent.mkdir(parents=True, exist_ok=True)
                    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
                except OSError as refused:
                    report = {
                        "ok": False,
                        "error": "output write failed",
                        "details": [str(refused)],
                    }
        print(json.dumps(report))
        return 0 if report["ok"] else 1

    if args.command == "validate":
        if args.write_turn_plan:
            # issue #227: the barrier loads the COMMITTED plan, so a topology
            # edit that skips regeneration is refused by TURN_PLAN_STALE and
            # `harness rollout` will not run. `compile_turn_plan` was
            # reachable only from Python and the research contract never
            # mentioned turn plans, so an agent editing its own
            # `graphs/agent_campaign.yaml` had no documented way out and
            # burned metered budget rediscovering the rule.
            from aisle.harness.validate import write_turn_plan

            report = write_turn_plan(args.graph, args.root)
            return emit_report(report, lambda level, e: f"turn plan {level}: {e['detail']}")
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
            perception=args.perception,
            sim_extra=args.sim_extra,
            per_episode_wall_s=args.per_episode_wall_s,
        )
        return emit_report(report, lambda level, e: f"rollout {level}: {e}")

    if args.command == "swap":
        from aisle.harness.swap import swap

        report = swap(
            args.root,
            args.graph,
            args.dataflow,
            args.node_id,
            args.with_yaml,
            args.embodiment,
            _branch(args.root),
        )
        return emit_report(report, lambda level, e: f"swap {level}: {e}")
    if args.command == "fleet":
        import json as json_module
        import os
        import subprocess
        import time as time_module

        from aisle.harness.fleet import run_fleet
        from aisle.harness.rollout import parse_seed_range, scrub_bringup_env
        from aisle.harness.validate import validate as validate_graph

        out_dir = args.out or (args.root / "runs" / f"fleet-{int(time_module.time())}")

        procs = []

        def launch(graph_path):
            env = {**scrub_bringup_env(dict(os.environ))}
            proc = subprocess.Popen(
                ["dora", "run", str(graph_path), "--uv"],
                cwd=args.root,
                env=env,
                stdout=open(out_dir / "dora.log", "w"),
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            procs.append(proc)
            return proc.poll

        report = run_fleet(
            args.graph if args.graph.is_absolute() else args.root / args.graph,
            args.agents,
            args.episodes,
            parse_seed_range(args.seeds),
            out_dir,
            args.timeout_s,
            launch,
            root=args.root,
        )
        # validate the STAMPED graph and attach the verdict (VAL gates)
        stamped = validate_graph(Path(report["graph"]), args.root, "franka", False)
        report["validate_ok"] = stamped["ok"]
        import signal as signal_module

        for proc in procs:
            if proc.poll() is None:
                os.killpg(proc.pid, signal_module.SIGTERM)
                try:
                    proc.wait(timeout=20)
                except subprocess.TimeoutExpired:
                    os.killpg(proc.pid, signal_module.SIGKILL)
        print(json_module.dumps(report))
        return 0 if report["episodes_total"] >= args.agents * args.episodes else 1

    if args.command == "probe":
        from aisle.harness.swap import probe

        report = probe(args.root, args.dataflow, args.topic, args.seconds, _branch(args.root))
        return emit_report(report, lambda level, e: f"probe {level}: {e}")
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
                sandbox=args.sandbox,
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
