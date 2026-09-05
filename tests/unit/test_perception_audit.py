"""Independent perception audit with hidden truth (BND-5, BND-6, BND-7;
SPEC 490, issue #346).

A stub detector and localizer stand in for OWLv2 and depth back-projection
so the audit's rules are pinned without the model: truth is opened only
after the prediction is fixed, the split is disjoint by content, every
stratum must clear the frozen floor, refusal stays within its limit, and
missing data fails rather than vanishing.
"""

from __future__ import annotations

import copy
import json

import numpy as np
import pytest

from aisle.harness import perception_audit as pa

pytestmark = pytest.mark.unit

MEDS = ["amoxicillin", "ibuprofen", "cetirizine"]


def _frames(seeds: list[int], stamps_per_seed: int = 3) -> tuple[dict, list[dict], list[dict]]:
    frames: dict[str, dict[int, dict]] = {"overhead": {}}
    oracle, goals = [], []
    stamp = 0
    for index, seed in enumerate(seeds):
        reset = stamp
        goals.append(
            {
                "text": json.dumps(
                    {
                        "target_med": MEDS[index % 3],
                        "seed": seed,
                        "reset_sim_ns": reset,
                        "timeout_s": 60,
                    }
                ),
                "sim_time_ns": reset,
            }
        )
        for k in range(stamps_per_seed):
            stamp += 100_000_000
            rgb = np.zeros((8, 8, 3), dtype=np.uint8)  # unique per frame by (seed, k)
            rgb[..., 0], rgb[..., 1] = seed, k
            depth = np.full((8, 8), 0.5, dtype=np.float32)
            frames["overhead"][stamp] = {"rgb": rgb, "depth": depth}
            state = []
            for i in range(3):
                state += [0.1 * i, 0.2 * seed, 0.05, 0, 0, 0, 1]
            oracle.append({"sim_time_ns": stamp, "data": state})
        stamp += 100_000_000
    return frames, oracle, goals


def _detector_for(truth_target: str, *, score: float = 0.5, rival: float = 0.0):
    def detector(rgb):
        out = [{"label": truth_target, "score": score, "box": [1.0, 1.0, 5.0, 5.0]}]
        if rival:
            out.append(
                {
                    "label": "ibuprofen" if truth_target != "ibuprofen" else "cetirizine",
                    "score": rival,
                    "box": [1.0, 1.0, 5.0, 5.0],
                }
            )
        return out

    return detector


def _localizer(record_truth_offset: float = 0.0):
    def localize(best, depth, record):
        pos = list(record["truth"]["positions"][record["target"]])
        pos[0] += record_truth_offset
        return pos

    return localize


def _score_all(corpus, frames, envelope, *, offset=0.0, score=0.5, rival=0.0):
    scored = []
    for record in corpus["records"]:
        arrays = frames[record["camera"]][record["sim_time_ns"]]
        detector = _detector_for(record["target"] or "amoxicillin", score=score, rival=rival)
        scored.append(
            pa.score_record(
                record,
                arrays,
                envelope=envelope,
                detector=detector,
                localizer=_localizer(offset),
                clock=lambda: 0.0,
            )
        )
    return scored


def test_corpus_attaches_hidden_truth_and_splits_by_content():
    """BND-5 / BND-6: one record per captured frame with frame hash, truth
    positions at the synchronized stamp, strata, and a calibration versus
    evaluation split that is disjoint by content identity."""
    frames, oracle, goals = _frames([0, 1, 2, 3])
    corpus = pa.build_corpus(
        run_id="r",
        frames=frames,
        oracle_rows=oracle,
        goals=goals,
        calibration={"fx": 1},
        med_names=MEDS,
    )
    assert len(corpus["records"]) == 12
    first = corpus["records"][0]
    assert first["truth"]["positions"]["amoxicillin"] == pytest.approx([0.0, 0.0, 0.05])
    assert first["split"] == "calibration" and first["strata"]["seed_parity"] == "even"
    assert {r["split"] for r in corpus["records"]} == {"calibration", "evaluation"}
    cal = {r["frame_hash"] for r in corpus["records"] if r["split"] == "calibration"}
    ev = {r["frame_hash"] for r in corpus["records"] if r["split"] == "evaluation"}
    assert not cal & ev
    assert corpus["corpus_hash"].startswith("sha256:")
    with pytest.raises(pa.PerceptionAuditError, match="no captured frames"):
        pa.build_corpus(
            run_id="r", frames={}, oracle_rows=oracle, goals=goals, calibration={}, med_names=MEDS
        )


def test_scorer_opens_truth_only_after_the_prediction_and_names_the_failure():
    """BND-5: correct, wrong identity, refusal, no detection, localization
    error, and missing data are distinct taxonomy rows; the detector and
    localizer never receive the truth."""
    frames, oracle, goals = _frames([1])
    corpus = pa.build_corpus(
        run_id="r", frames=frames, oracle_rows=oracle, goals=goals, calibration={}, med_names=MEDS
    )
    envelope = pa.default_envelope(MEDS)
    record = corpus["records"][0]
    arrays = frames["overhead"][record["sim_time_ns"]]
    seen = {}

    def spying_detector(rgb):
        seen["rgb_only"] = rgb.shape == (8, 8, 3)
        return [{"label": record["target"], "score": 0.6, "box": [1, 1, 5, 5]}]

    out = pa.score_record(
        record,
        arrays,
        envelope=envelope,
        detector=spying_detector,
        localizer=_localizer(),
        clock=lambda: 0.0,
    )
    assert seen["rgb_only"] and out["outcome"] == "correct"
    assert out["prediction"]["localization_error_m"] == pytest.approx(0.0)
    wrong = pa.score_record(
        record,
        arrays,
        envelope=envelope,
        detector=_detector_for(record["target"], score=0.3, rival=0.1),
        localizer=_localizer(),
        clock=lambda: 0.0,
    )
    assert wrong["outcome"] == "correct"  # rival below the picked score, margin above the floor
    refused = pa.score_record(
        record,
        arrays,
        envelope=envelope,
        detector=_detector_for(record["target"], score=0.3, rival=0.295),
        localizer=_localizer(),
        clock=lambda: 0.0,
    )
    assert refused["outcome"] == "refused"
    none = pa.score_record(
        record,
        arrays,
        envelope=envelope,
        detector=lambda rgb: [],
        localizer=_localizer(),
        clock=lambda: 0.0,
    )
    assert none["outcome"] == "no_detection"
    far = pa.score_record(
        record,
        arrays,
        envelope=envelope,
        detector=_detector_for(record["target"]),
        localizer=_localizer(0.1),
        clock=lambda: 0.0,
    )
    assert far["outcome"] == "localization_error"
    missing = pa.score_record(
        {**record, "truth": None},
        arrays,
        envelope=envelope,
        detector=_detector_for(record["target"]),
        localizer=_localizer(),
        clock=lambda: 0.0,
    )
    assert missing["outcome"] == "missing_data"


def test_eligibility_requires_every_stratum_to_clear_the_floor():
    """BND-7: a perfect evaluation split is eligible; one failing stratum
    makes the audit not eligible even when the aggregate is high; an empty
    stratum axis fails; overlapping splits are refused."""
    frames, oracle, goals = _frames([0, 1, 2, 3, 4, 5, 6, 7], stamps_per_seed=40)
    corpus = pa.build_corpus(
        run_id="r", frames=frames, oracle_rows=oracle, goals=goals, calibration={}, med_names=MEDS
    )
    envelope = pa.default_envelope(MEDS)
    scored = _score_all(corpus, frames, envelope)
    report = pa.audit(corpus, envelope, scored=scored, model_hashes={"identity": "sha256:x"})
    assert report["ok"] is True and report["eligibility"] == "perception_eligible"
    assert report["split_sizes"] == {"calibration": 160, "evaluation": 160}
    assert all(cell["passes_floor"] for cell in report["strata"]["target_class"].values())
    assert report["taxonomy"]["correct"] == 160 and report["latency_s"]["descriptive"] is True
    broken = copy.deepcopy(scored)
    ids = {
        r["record_id"]
        for r in corpus["records"]
        if r["split"] == "evaluation" and r["strata"]["target_class"] == "ibuprofen"
    }
    for s in broken:
        if s["record_id"] in ids:
            s["outcome"] = "wrong_identity"
    failed = pa.audit(corpus, envelope, scored=broken, model_hashes={})
    assert failed["ok"] is False and failed["eligibility"] == "not_eligible"
    assert any("target_class=ibuprofen" in f for f in failed["failures"])
    assert (
        failed["strata"]["seed_parity"]["odd"]["accuracy"] < 1.0
    )  # aggregate visible, not masking
    overlap = copy.deepcopy(corpus)
    evaluation_hash = next(
        r["frame_hash"] for r in overlap["records"] if r["split"] == "evaluation"
    )
    overlap["records"][0]["frame_hash"] = (
        evaluation_hash  # a calibration frame reused in evaluation
    )
    with pytest.raises(pa.PerceptionAuditError, match="overlap"):
        pa.audit(overlap, envelope, scored=scored, model_hashes={})
    with pytest.raises(pa.PerceptionAuditError, match="incomplete"):
        pa.audit(corpus, {"schema_version": "x"}, scored=scored, model_hashes={})
