#!/usr/bin/env python3
"""Retain an audit report as a record directory (CON-8): the report with its
raw predictions split into `raw-predictions.json.gz` (every scored row, so the
report hash can be recomputed), plus the run's manifest and episodes.
"""

from __future__ import annotations

import argparse
import gzip
import json
import shutil
import sys
from pathlib import Path

RAW_FILE = "raw-predictions.json.gz"
RAW_NOTE = (
    "every scored row, including out_of_envelope rows that carry no prediction; "
    "merging this file back as raw_predictions recomputes report_hash"
)


def split_record(report: dict, run: Path, output: Path) -> dict:
    if output.exists():
        raise FileExistsError(f"record output exists: {output}")
    if "raw_predictions" not in report:
        raise ValueError("report carries no raw predictions to retain")
    output.mkdir(parents=True)
    raw = report["raw_predictions"]
    with gzip.open(output / RAW_FILE, "wt") as stream:
        json.dump(raw, stream)
    retained = {k: v for k, v in report.items() if k != "raw_predictions"}
    retained["raw_predictions_file"] = RAW_FILE
    retained["raw_predictions_note"] = RAW_NOTE
    (output / "report.json").write_text(json.dumps(retained, indent=2, sort_keys=True) + "\n")
    for name in ("manifest.json", "episodes.jsonl"):
        shutil.copyfile(run / name, output / name)
    return {"ok": True, "output": str(output), "raw_rows": len(raw), "run_id": report["run_id"]}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True, help="full audit report JSON")
    parser.add_argument("--run", type=Path, required=True, help="recorded run directory")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        result = split_record(json.loads(args.report.read_bytes()), args.run, args.output)
    except (OSError, ValueError, KeyError, TypeError) as refused:
        result = {"ok": False, "error": "perception record refused", "details": [repr(refused)]}
    print(json.dumps(result, sort_keys=True))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
