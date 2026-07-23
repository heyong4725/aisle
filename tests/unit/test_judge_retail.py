"""Table-driven unit tests for the retail verifier (SPEC 200 RS-4..9) —
the module named by the spec: >= 10 cases per scenario, including every
RS-4 placement criterion failing ALONE. Pure verdicts, no dora/sim
(VER-1 discipline, CON-12).
"""

import math

import numpy as np
import pytest

pytestmark = pytest.mark.unit


# --- helpers -------------------------------------------------------------


def _plano():
    from aisle.scenes.store import load_planogram

    return load_planogram()


def _cfg(plano, goal):
    from aisle.verifier.retail import build_retail_cfg

    return build_retail_cfg(plano, goal)


def _yaw_to_quat_xyzw(yaw: float) -> tuple[float, float, float, float]:
    half = yaw / 2
    return (0.0, 0.0, math.sin(half), math.cos(half))  # TC-1 wire order


def _state(cfg, overrides: dict) -> np.ndarray:
    """oracle_state (n*7, TC-1) with every item at its home pose, overridden
    per item_id with (x, y, z, yaw)."""

    blocks = []
    for idx, item_id in enumerate(cfg.item_ids):
        if item_id in overrides:
            x, y, z, yaw = overrides[item_id]
        else:
            x, y, z, yaw = cfg.home_poses[idx]
        blocks.extend([x, y, z, *_yaw_to_quat_xyzw(yaw)])
    return np.asarray(blocks, dtype=np.float32)


def _judge(plano, goal, overrides, t=1.0):
    from aisle.verifier.retail import judge_retail

    cfg = _cfg(plano, goal)
    return judge_retail(_state(cfg, overrides), plano, goal, t, cfg)


def _slot_pose(plano, slot_id, category=None, dx=0.0, dy=0.0, dyaw=0.0):
    """(x, y, z, yaw) resting at a slot's template, nudged in the UNIT
    frame (dx along facing, dy along the row) so tests read naturally."""
    from aisle.scenes.pharmacy import load_meds
    from aisle.scenes.store import slot_world_pose

    meds = load_meds()
    cat = category or plano["slots"][slot_id]["category"]
    world, yaw = slot_world_pose(plano, slot_id)
    cos_y, sin_y = math.cos(yaw), math.sin(yaw)
    return (
        world[0] + dx * cos_y - dy * sin_y,
        world[1] + dx * sin_y + dy * cos_y,
        world[2] + meds[cat]["size"][2] / 2,
        yaw + dyaw,
    )


def _on_counter(plano, category, k=0):
    """(x, y, z, yaw) of an item resting on the counter top."""
    from aisle.scenes.pharmacy import load_meds

    store = plano["store"]
    top = store["counter_pos"][2] + store["counter_size"][2] / 2
    h = load_meds()[category]["size"][2]
    return (store["counter_pos"][0], store["counter_pos"][1] + 0.1 * k, top + h / 2, 0.0)


def _s1_goal(plano):
    """A fixed S1 goal: 1 amoxicillin + 2 cetirizine."""
    from aisle.scenes.pharmacy import load_meds
    from aisle.scenes.store import _spec_for

    meds = load_meds()
    return {
        "scenario": "S1",
        "seed": 0,
        "order": [
            {"product": "amoxicillin", "spec": _spec_for("amoxicillin", meds), "qty": 1},
            {"product": "cetirizine", "spec": _spec_for("cetirizine", meds), "qty": 2},
        ],
    }


S2_GOAL = {
    "scenario": "S2",
    "seed": 0,
    "restock": [
        {"slot": "A1-L0-S0", "category": "amoxicillin"},
        {"slot": "B1-L0-S1", "category": "metformin"},
    ],
}

S3_GOAL = {
    "scenario": "S3",
    "seed": 0,
    "misplaced": [
        {"item": "A1-L0-S0#0", "found_in": "B1-L0-S0", "belongs_in": "A1-L0-S0"},
        {"item": "B1-L0-S0#0", "found_in": "A1-L0-S0", "belongs_in": "B1-L0-S0"},
    ],
}


def test_failure_classes_extend_the_taxonomy():
    """RS-4: the retail classes join the existing VER-3 taxonomy."""
    from aisle.verifier.retail import RETAIL_FAILURE_CLASSES

    assert set(RETAIL_FAILURE_CLASSES) >= {
        "misplaced",
        "misaligned",
        "overhang",
        "wrong_slot",
        "missing_item",
        "extra_item",
    }


def test_thresholds_live_in_placement_toml():
    """RS-4: every placement threshold comes from verifier/placement.toml,
    none inline."""
    from aisle.verifier.retail import load_placement

    placement = load_placement()["placement"]
    assert placement["pos_tol_m"] == pytest.approx(0.02)
    assert placement["yaw_tol_deg"] == pytest.approx(10.0)
    assert placement["alignment_tol_m"] == pytest.approx(0.015)
    assert "overhang_tol_m" in placement


class TestS1:
    """RS-7: order fulfilment on the counter."""

    def _full_delivery(self, plano):
        return {
            "A1-L0-S0#0": _on_counter(plano, "amoxicillin", 0),
            "A2-L0-S0#0": _on_counter(plano, "cetirizine", 1),
            "A2-L0-S1#0": _on_counter(plano, "cetirizine", 2),
        }

    def test_complete_order_succeeds(self):
        plano = _plano()
        v = _judge(plano, _s1_goal(plano), self._full_delivery(plano))
        assert v["status"] == "success" and v["penalties"] == []

    def test_partial_order_is_ongoing(self):
        plano = _plano()
        overrides = self._full_delivery(plano)
        overrides.pop("A2-L0-S1#0")  # one cetirizine short
        assert _judge(plano, _s1_goal(plano), overrides)["status"] == "ongoing"

    def test_nothing_delivered_is_ongoing(self):
        plano = _plano()
        assert _judge(plano, _s1_goal(plano), {})["status"] == "ongoing"

    def test_wrong_item_on_counter_fails_immediately(self):
        """RS-7 safety asymmetry: a NON-ordered product on the counter at
        ANY time is an immediate extra_item failure — t tiny, order empty."""
        plano = _plano()
        overrides = {"B1-L0-S0#0": _on_counter(plano, "metformin")}
        v = _judge(plano, _s1_goal(plano), overrides, t=0.5)
        assert v["status"] == "fail" and "extra_item" in v["penalties"]

    def test_wrong_item_beats_a_complete_order(self):
        plano = _plano()
        overrides = self._full_delivery(plano)
        overrides["B1-L0-S0#0"] = _on_counter(plano, "metformin", 3)
        v = _judge(plano, _s1_goal(plano), overrides)
        assert v["status"] == "fail" and "extra_item" in v["penalties"]

    def test_over_delivery_of_ordered_product_is_not_success(self):
        """qty must be exact; an extra copy of an ORDERED product is
        recoverable — ongoing, not the RS-7 immediate failure (ADR-16)."""
        plano = _plano()
        overrides = self._full_delivery(plano)
        overrides["A1-L0-S1#0"] = _on_counter(plano, "amoxicillin", 3)  # qty 2 > 1
        assert _judge(plano, _s1_goal(plano), overrides)["status"] == "ongoing"

    def test_item_beside_the_counter_does_not_count(self):
        plano = _plano()
        overrides = self._full_delivery(plano)
        x, y, z, yaw = overrides["A1-L0-S0#0"]
        overrides["A1-L0-S0#0"] = (x, y + 1.0, 0.05, yaw)  # on the floor nearby
        assert _judge(plano, _s1_goal(plano), overrides)["status"] == "ongoing"

    def test_item_floating_above_counter_does_not_count(self):
        plano = _plano()
        overrides = self._full_delivery(plano)
        x, y, z, yaw = overrides["A1-L0-S0#0"]
        overrides["A1-L0-S0#0"] = (x, y, z + 0.5, yaw)  # mid-carry
        assert _judge(plano, _s1_goal(plano), overrides)["status"] == "ongoing"

    def test_timeout_gates_success(self):
        """A completion after the deadline is a timeout, never a late
        success (TC-8 spirit, mirrors the desk judge)."""
        plano = _plano()
        goal = _s1_goal(plano)
        cfg = _cfg(plano, goal)
        v = _judge(plano, goal, self._full_delivery(plano), t=cfg.timeout_s + 1)
        assert v["status"] == "fail" and "timeout" in v["penalties"]

    def test_incomplete_at_timeout_fails(self):
        plano = _plano()
        goal = _s1_goal(plano)
        cfg = _cfg(plano, goal)
        v = _judge(plano, goal, {}, t=cfg.timeout_s + 1)
        assert v["status"] == "fail" and "timeout" in v["penalties"]

    def test_s1_has_no_placement_scores(self):
        plano = _plano()
        v = _judge(plano, _s1_goal(plano), self._full_delivery(plano))
        assert v["placement_scores"] == []

    def test_deterministic(self):
        plano = _plano()
        overrides = self._full_delivery(plano)
        assert _judge(plano, _s1_goal(plano), overrides) == _judge(
            plano, _s1_goal(plano), overrides
        )


class TestS2:
    """RS-8: both assigned slots restocked to spec, RS-4 placement.
    S2 removes the slots' items; restocking uses the bin items."""

    def _restocked(self, plano, **nudge):
        """Both assigned slots filled from the bin, first slot nudged."""
        return {
            "bin#amoxicillin": _slot_pose(plano, "A1-L0-S0", "amoxicillin", **nudge),
            "bin#metformin": _slot_pose(plano, "B1-L0-S1", "metformin"),
        }

    def _s2_judge(self, plano, overrides, t=1.0):
        # the assigned slots' own items are ABSENT (S2 de-stocked them)
        from aisle.verifier.retail import judge_retail

        goal = S2_GOAL
        cfg = _cfg(plano, goal)
        return judge_retail(_state(cfg, overrides), plano, goal, t, cfg)

    def test_perfect_restock_succeeds(self):
        plano = _plano()
        v = self._s2_judge(plano, self._restocked(plano))
        assert v["status"] == "success" and v["penalties"] == []
        assert len(v["placement_scores"]) == 2
        for score in v["placement_scores"]:
            assert set(score) >= {
                "slot",
                "item",
                "pos",
                "yaw",
                "front_face",
                "overhang",
                "alignment",
            }
            assert all(score[c] for c in ("pos", "yaw", "front_face", "overhang", "alignment"))

    def test_one_slot_still_empty_is_ongoing_then_missing(self):
        plano = _plano()
        overrides = self._restocked(plano)
        overrides.pop("bin#metformin")
        assert self._s2_judge(plano, overrides)["status"] == "ongoing"
        cfg = _cfg(plano, S2_GOAL)
        v = self._s2_judge(plano, overrides, t=cfg.timeout_s + 1)
        assert v["status"] == "fail" and "missing_item" in v["penalties"]

    def test_wrong_category_in_slot_is_wrong_slot(self):
        plano = _plano()
        overrides = {
            "bin#ibuprofen": _slot_pose(plano, "A1-L0-S0", "ibuprofen"),  # wrong cat
            "bin#metformin": _slot_pose(plano, "B1-L0-S1", "metformin"),
        }
        cfg = _cfg(plano, S2_GOAL)
        v = self._s2_judge(plano, overrides, t=cfg.timeout_s + 1)
        assert v["status"] == "fail" and "wrong_slot" in v["penalties"]

    # --- RS-4: every criterion failing ALONE -------------------------

    def test_pos_fails_alone(self):
        plano = _plano()
        v = self._s2_judge(plano, self._restocked(plano, dy=0.03))  # 3 cm > 2 cm
        score = v["placement_scores"][0]
        assert not score["pos"]
        assert score["yaw"] and score["front_face"] and score["overhang"]
        assert v["status"] == "ongoing" and "misplaced" in v["penalties"]

    def test_yaw_fails_alone(self):
        plano = _plano()
        v = self._s2_judge(plano, self._restocked(plano, dyaw=math.radians(15)))
        score = v["placement_scores"][0]
        assert not score["yaw"]
        assert score["pos"] and score["front_face"] and score["alignment"]
        assert "misaligned" in v["penalties"]

    def test_front_face_fails_alone(self):
        """Placed BACKWARD: the long axis is aligned (yaw mod 180 = 0
        passes) but the front face points into the shelf (ADR-16)."""
        plano = _plano()
        v = self._s2_judge(plano, self._restocked(plano, dyaw=math.pi))
        score = v["placement_scores"][0]
        assert not score["front_face"]
        assert score["pos"] and score["yaw"] and score["overhang"]
        assert "misaligned" in v["penalties"]

    def test_alignment_fails_alone(self):
        """Front edge off the row line by 1.8 cm: within pos tol (2 cm)
        but past alignment tol (1.5 cm) — fails alone (ADR-16)."""
        plano = _plano()
        v = self._s2_judge(plano, self._restocked(plano, dx=0.018))
        score = v["placement_scores"][0]
        assert not score["alignment"]
        assert score["pos"] and score["yaw"] and score["front_face"]
        assert "misaligned" in v["penalties"]

    def test_overhang_fails_alone(self):
        """A synthetic slot whose template sits at the shelf edge: a small
        forward nudge overhangs while every other criterion passes."""
        from copy import deepcopy

        plano = deepcopy(_plano())
        # move the slot template to the front edge (unit depth 0.30 -> 0.15)
        plano["slots"]["A1-L0-S0"]["template_pose"][0] = 0.118
        goal = S2_GOAL
        cfg = _cfg(plano, goal)
        overrides = {
            "bin#amoxicillin": _slot_pose(plano, "A1-L0-S0", "amoxicillin", dx=0.012),
            "bin#metformin": _slot_pose(plano, "B1-L0-S1", "metformin"),
        }
        from aisle.verifier.retail import judge_retail

        v = judge_retail(_state(cfg, overrides), plano, goal, 1.0, cfg)
        score = v["placement_scores"][0]
        assert not score["overhang"]
        assert score["pos"] and score["yaw"] and score["front_face"]
        assert "overhang" in v["penalties"]

    def test_all_criteria_pass_within_tolerances(self):
        """Small nudges inside every tolerance still succeed."""
        plano = _plano()
        v = self._s2_judge(plano, self._restocked(plano, dy=0.01, dx=0.005, dyaw=math.radians(5)))
        assert v["status"] == "success"

    def test_deterministic(self):
        plano = _plano()
        overrides = self._restocked(plano)
        assert self._s2_judge(plano, overrides) == self._s2_judge(plano, overrides)


class TestS3:
    """RS-9: both misplaced items back home passing RS-4, origin slots not
    newly wrong. S3 spawns the two items swapped."""

    def _swapped(self, plano):
        """The episode's initial state: each item in the OTHER slot."""
        return {
            "A1-L0-S0#0": _slot_pose(plano, "B1-L0-S0", "amoxicillin"),
            "B1-L0-S0#0": _slot_pose(plano, "A1-L0-S0", "metformin"),
        }

    def _restored(self, plano, **nudge_a):
        return {
            "A1-L0-S0#0": _slot_pose(plano, "A1-L0-S0", "amoxicillin", **nudge_a),
            "B1-L0-S0#0": _slot_pose(plano, "B1-L0-S0", "metformin"),
        }

    def test_both_restored_succeeds(self):
        plano = _plano()
        v = _judge(plano, S3_GOAL, self._restored(plano))
        assert v["status"] == "success" and v["penalties"] == []
        assert len(v["placement_scores"]) == 2

    def test_still_swapped_is_ongoing_then_fails(self):
        plano = _plano()
        assert _judge(plano, S3_GOAL, self._swapped(plano))["status"] == "ongoing"
        cfg = _cfg(plano, S3_GOAL)
        v = _judge(plano, S3_GOAL, self._swapped(plano), t=cfg.timeout_s + 1)
        assert v["status"] == "fail"
        assert "wrong_slot" in v["penalties"] or "missing_item" in v["penalties"]

    def test_one_restored_is_ongoing(self):
        plano = _plano()
        overrides = self._restored(plano)
        overrides["B1-L0-S0#0"] = _slot_pose(plano, "A1-L0-S0", "metformin")  # still away
        assert _judge(plano, S3_GOAL, overrides)["status"] == "ongoing"

    def test_origin_slot_newly_wrong_blocks_success(self):
        """RS-9: both items home, but a THIRD item was dumped into one of
        the involved slots — not success."""
        plano = _plano()
        overrides = self._restored(plano)
        overrides["A1-L0-S1#0"] = _slot_pose(plano, "B1-L0-S0", "amoxicillin")
        v = _judge(plano, S3_GOAL, overrides)
        assert v["status"] != "success"

    def test_item_dropped_on_floor_is_missing(self):
        plano = _plano()
        overrides = self._restored(plano)
        x, y, _, yaw = overrides["A1-L0-S0#0"]
        overrides["A1-L0-S0#0"] = (x, y - 0.5, 0.03, yaw)  # floor, off-shelf
        cfg = _cfg(plano, S3_GOAL)
        v = _judge(plano, S3_GOAL, overrides, t=cfg.timeout_s + 1)
        assert v["status"] == "fail" and "missing_item" in v["penalties"]

    def test_restored_but_misplaced_pos(self):
        plano = _plano()
        v = _judge(plano, S3_GOAL, self._restored(plano, dy=0.03))
        assert v["status"] == "ongoing" and "misplaced" in v["penalties"]

    def test_restored_but_misaligned_yaw(self):
        plano = _plano()
        v = _judge(plano, S3_GOAL, self._restored(plano, dyaw=math.radians(20)))
        assert "misaligned" in v["penalties"]

    def test_restored_backward_front_face(self):
        plano = _plano()
        v = _judge(plano, S3_GOAL, self._restored(plano, dyaw=math.pi))
        score = next(s for s in v["placement_scores"] if s["slot"] == "A1-L0-S0")
        assert not score["front_face"] and score["yaw"]

    def test_within_tolerance_still_succeeds(self):
        plano = _plano()
        v = _judge(plano, S3_GOAL, self._restored(plano, dy=0.008, dyaw=math.radians(4)))
        assert v["status"] == "success"

    def test_deterministic(self):
        plano = _plano()
        overrides = self._restored(plano)
        assert _judge(plano, S3_GOAL, overrides) == _judge(plano, S3_GOAL, overrides)


def test_score_episode_shape():
    """RS-6: the per-episode record is {success, t_end, penalties,
    placement_scores}."""
    from aisle.verifier.retail import score_episode

    verdict = {"status": "success", "penalties": [], "placement_scores": []}
    record = score_episode(verdict, t=42.5)
    assert record == {"success": True, "t_end": 42.5, "penalties": [], "placement_scores": []}
    verdict = {"status": "fail", "penalties": ["extra_item"], "placement_scores": []}
    record = score_episode(verdict, t=10.0)
    assert record["success"] is False and record["penalties"] == ["extra_item"]
