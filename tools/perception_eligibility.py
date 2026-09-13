#!/usr/bin/env python3
"""BND-7 eligibility record from a retained perception-audit record (CON-8).

JSON to stdout, logs to stderr, exit 0 iff the candidate is eligible.
"""

from __future__ import annotations

import argparse
import gzip
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def load_raw_predictions(report: dict, report_path: Path) -> list:
    if "raw_predictions" in report:
        return report["raw_predictions"]
    with gzip.open(report_path.parent / report["raw_predictions_file"], "rt") as stream:
        return json.load(stream)


def main(argv: list[str] | None = None) -> int:
    from aisle.harness.perception_audit import PerceptionAuditError
    from aisle.harness.perception_eligibility import eligibility

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--envelope", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, default=None, help="default: beside the report")
    parser.add_argument("--candidate", required=True)
    parser.add_argument("--role", required=True, choices=["short_composition", "engineering"])
    parser.add_argument("--graph", required=True, help="candidate graph the run must have used")
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args(argv)
    try:
        report = json.loads(args.report.read_bytes())
        manifest_path = args.manifest or args.report.parent / "manifest.json"
        result = eligibility(
            report,
            envelope=json.loads(args.envelope.read_bytes()),
            manifest=json.loads(manifest_path.read_bytes()),
            raw_predictions=load_raw_predictions(report, args.report),
            candidate=args.candidate,
            role=args.role,
            graph=args.graph,
        )
        if args.output is not None:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    except PerceptionAuditError as refused:
        result = {"ok": False, "error": str(refused), "details": refused.details}
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as refused:
        result = {
            "ok": False,
            "error": "perception eligibility refused",
            "details": [repr(refused)],
        }
    print(json.dumps(result, sort_keys=True))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
