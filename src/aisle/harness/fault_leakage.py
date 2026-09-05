"""FLT-8 leakage probe: can participant-visible metadata separate sham
from fault cells before any outcome or runtime evidence exists?

The probe is frozen in `analysis/fault-bank/leakage-probe.json` before it
runs: the feature allowlist (what a participant can observe about a
staged file without opening it — size, mode, mtime — plus the staged file
count), the classifier (leave-one-out nearest-centroid on standardized
features), the held-out split (leave-one-out over materialized cells), and
the decision rule (a seeded label-permutation test on balanced accuracy;
the bank stays blocked when the observed balanced accuracy beats chance
at the frozen alpha). Everything here is deterministic given the probe's
seed (CON-5).
"""

from __future__ import annotations

import hashlib
import json
import random
from typing import Any

PROBE_SCHEMA = "aisle.fault-leakage-probe.v1"
CLASSIFIER = "leave-one-out nearest-centroid on z-scored features"


class LeakageProbeError(ValueError):
    pass


def validate_probe(probe: dict) -> list[str]:
    errors = []
    if probe.get("schema_version") != PROBE_SCHEMA:
        errors.append("unsupported probe schema")
    if not probe.get("feature_allowlist"):
        errors.append("feature allowlist is empty")
    if probe.get("classifier") != CLASSIFIER:
        errors.append("classifier is not the frozen one")
    if probe.get("split") != "leave-one-out":
        errors.append("split is not leave-one-out")
    rule = probe.get("decision_rule") or {}
    for key in ("alpha", "permutations", "seed", "minimum_cells_per_class"):
        if key not in rule:
            errors.append(f"decision rule missing {key}")
    return errors


def features(receipt: dict, allowlist: list[str]) -> list[float]:
    """One cell's feature vector from its materialization receipt, in
    allowlist order. `staged_file_count` counts targets; every other name
    is `<metadata key>` aggregated over the staged targets (sum for size,
    max for mode and mtime — a participant sees each file, but the cell
    is the unit)."""
    visible = receipt.get("visible_metadata") or {}
    if not visible:
        raise LeakageProbeError("receipt carries no visible_metadata (FLT-8)")
    row: list[float] = []
    for name in allowlist:
        if name == "staged_file_count":
            row.append(float(len(visible)))
        elif name == "size_bytes":
            row.append(float(sum(v["size_bytes"] for v in visible.values())))
        elif name in ("mode", "mtime"):
            row.append(float(max(v[name] for v in visible.values())))
        else:
            raise LeakageProbeError(f"feature outside the frozen allowlist: {name}")
    return row


def _zscore(rows: list[list[float]]) -> list[list[float]]:
    n, d = len(rows), len(rows[0])
    out = [[0.0] * d for _ in range(n)]
    for j in range(d):
        col = [r[j] for r in rows]
        mean = sum(col) / n
        var = sum((v - mean) ** 2 for v in col) / n
        sd = var**0.5
        for i in range(n):
            out[i][j] = 0.0 if sd == 0 else (col[i] - mean) / sd
    return out


def loo_balanced_accuracy(rows: list[list[float]], labels: list[bool]) -> float:
    """Leave-one-out nearest-centroid; balanced accuracy over the two
    classes. A held-out cell whose class has no other member predicts the
    other class (fail closed toward being distinguishable is NOT what we
    want; the permutation null shares the same rule, so it is fair)."""
    z = _zscore(rows)
    hits = {True: 0, False: 0}
    counts = {True: labels.count(True), False: labels.count(False)}
    for i, row in enumerate(z):
        cents = {}
        for cls in (True, False):
            members = [z[k] for k in range(len(z)) if k != i and labels[k] == cls]
            if members:
                cents[cls] = [sum(m[j] for m in members) / len(members) for j in range(len(row))]
        if len(cents) < 2:
            pred = next(iter(cents)) if cents else not labels[i]
        else:
            dist = {
                cls: sum((a - b) ** 2 for a, b in zip(row, c, strict=True))
                for cls, c in cents.items()
            }
            pred = min(dist, key=lambda c: (dist[c], not c))
        if pred == labels[i]:
            hits[labels[i]] += 1
    return 0.5 * sum(hits[c] / counts[c] for c in (True, False))


def permutation_p(
    rows: list[list[float]], labels: list[bool], observed: float, n: int, seed: int
) -> float:
    rng = random.Random(seed)
    at_least = 0
    for _ in range(n):
        shuffled = list(labels)
        rng.shuffle(shuffled)
        if loo_balanced_accuracy(rows, shuffled) >= observed - 1e-12:
            at_least += 1
    return (at_least + 1) / (n + 1)


def probe_cells(probe: dict, receipts: list[dict]) -> dict:
    """The FLT-8 decision over materialized cells. `receipts` are the
    injector's receipts (sham flag + visible_metadata). Blocked when either
    class is too small to test, when a feature is outside the allowlist, or
    when balanced accuracy beats the permutation null at alpha."""
    errors = validate_probe(probe)
    if errors:
        raise LeakageProbeError("; ".join(errors))
    rule = probe["decision_rule"]
    labels = [bool(r["sham"]) for r in receipts]
    rows = [features(r, probe["feature_allowlist"]) for r in receipts]
    counts = {"sham": labels.count(True), "fault": labels.count(False)}
    report: dict[str, Any] = {
        "schema_version": PROBE_SCHEMA,
        "probe_sha256": hashlib.sha256(
            json.dumps(probe, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
        "cells": len(receipts),
        "counts": counts,
        "feature_allowlist": list(probe["feature_allowlist"]),
    }
    if min(counts.values()) < rule["minimum_cells_per_class"]:
        return {
            **report,
            "decision": "blocked",
            "reason": f"fewer than {rule['minimum_cells_per_class']} cells in a class",
        }
    observed = loo_balanced_accuracy(rows, labels)
    p = permutation_p(rows, labels, observed, int(rule["permutations"]), int(rule["seed"]))
    leaks = p < float(rule["alpha"])
    return {
        **report,
        "balanced_accuracy": observed,
        "permutation_p": p,
        "alpha": rule["alpha"],
        "decision": "blocked" if leaks else "not_distinguishable",
        "reason": (
            "participant-visible metadata separates sham from fault cells"
            if leaks
            else "classifier does not beat the frozen chance criterion"
        ),
    }


def receipts_from_report(report: dict) -> list[dict]:
    """Materialization receipts of every rung of a calibration report that
    actually staged files (rollout may have failed later; the metadata
    exists regardless)."""
    return [rung["receipt"] for rung in report.get("rungs", []) if rung.get("receipt")]
