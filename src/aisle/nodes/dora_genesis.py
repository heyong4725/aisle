"""dora-genesis bridge node (SPEC 030, implementing SPEC 010 over SPEC 020).

Exactly one bridge owns the Genesis scene per dataflow (BRG-1). The node is
driven by dora/timer/millis/10 ticks; each tick after the first reset
advances sim by cfg.dt, services coalesced commands in arrival order
(BRG-3), and publishes topics at their contract rates (TC table) —
rendering only when a camera topic is due (BRG-2). Ticks BEFORE the first
reset are dropped (CON-5/ADR-25) unless AISLE_STEP_WITHOUT_RESET=1 opts a
reset-less bring-up graph out. Pure control-plane logic (scheduler,
coalescer, config, bridge_info) lives at module level, sim-free and
unit-tested; dora, arrow, and genesis are imported only inside main()
(CON-12).

Sim exceptions propagate: there is deliberately no try/except around
scene.step() or state injection — a physics error must crash the node
loudly as a dora ERROR event (BRG-7).
"""

from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from aisle.embodiment import (
    from_wire_joint_order,
    profile_dof_indices,
    profile_joint_names,
    to_wire_joint_order,
)

_REPO_ROOT = Path(__file__).resolve().parents[3]

# SPEC 010 §2: producer rates are contracts, not hints (TC-4)
TOPIC_RATES = {
    "rgb_overhead": 30,
    "rgb_wrist": 30,
    "depth_overhead": 15,
    "joint_state": 100,
    "gripper_state": 100,
    "oracle_state": 30,
    # non-privileged ground-truth poses for tier-T0 perception (SPEC 010,
    # issue #2 resolution); same payload as oracle_state, separate topic so
    # VAL-6 keeps oracle_state verifier-only. 15 Hz: a second 30 Hz stream
    # pushed the render wall-rate below the TC-4 band (T08 A1)
    "poses": 15,
    # TC-9 L1 only: per-pixel segmentation ids. 15 Hz — the SAME rate as
    # depth, because an L1 estimate masks the segmentation and indexes the
    # depth, so the two MUST be co-scheduled and served by one render pass.
    "seg_overhead": 15,
}
RENDER_TOPICS = ("rgb_overhead", "rgb_wrist", "depth_overhead", "seg_overhead")
# published DIRECTLY on the reset path, off the scheduler: the injected state
# IS the post-reset observation and must be snapshotted before any physics
# step (TC-A2, CON-5). Named as data so a test can assert every one of them
# still passes through the rung gate in `publish` (TC-9) — `poses` here on an
# L1 run was ground truth on the wire once per episode.
RESET_PUBLISH = ("oracle_state", "poses")


def reset_publish_topics(topic_rates: dict) -> tuple[str, ...]:
    """The reset path's direct publishes, filtered by the rung (TC-9).

    Filtering HERE as well as inside `publish` is deliberate. The gate inside
    publish() is the backstop for every call site, but a test can only reach it
    through dora and genesis, so an INVERTED guard there (`if may_publish(...):
    return`) passed every test while re-opening exactly the leak this fixes.
    Inverting it now breaks every ordinary topic loudly instead, and the leak
    that actually happened is prevented by this function, which a unit test
    calls directly."""
    return tuple(topic for topic in RESET_PUBLISH if may_publish(topic, topic_rates))


def may_publish(topic: str, topic_rates: dict) -> bool:
    """TC-9: whether this bridge may put `topic` on the wire at its rung.

    The rung's topic set is the single source of truth, and the gate is a
    NAMED predicate rather than an inline check because the reset path
    publishes directly, off the scheduler: `publish("poses")` after every
    reset put ground-truth pose on an L1 wire once per episode. Naming it
    also makes it testable -- the first version of the test for that fix
    re-derived the gate from two module constants and stayed green when the
    guard was deleted."""
    return topic in topic_rates


def rung_topic_rates(perception: str, is_mobile: bool) -> dict[str, int]:
    """TC-9: the bridge publishes only what the rung permits.

    VAL-8 rejects a graph that CONSUMES a forbidden topic; this is the other
    half — the bridge does not PUBLISH one. Belt and braces on purpose: the
    validator can be bypassed (an instrumented run copy, a hand-edited graph),
    and a topic that is never on the wire cannot be consumed by accident.
    Segmentation is rendered only at L1 because a segmentation pass costs an
    extra render on every overhead tick, so an L0 run's render budget is
    unchanged by this topic existing.
    """
    rates = dict(TOPIC_RATES)
    if perception != "L0":
        rates.pop("poses")
    if perception != "L1":
        rates.pop("seg_overhead")
    if is_mobile:
        rates.update(base_pose=50, base_scan=10)
    return rates


# ticks after a reset during which the bridge HOLDS the arm at home and
# drops incoming joint commands. A collision/timeout ends an episode
# mid-plan; the executor keeps streaming that plan's joint_cmds for the
# few ticks until it receives reset_done and clears, and those stale
# commands would drive the just-homed arm back off home — the next
# episode then starts from a bad pose and sweeps the shelf (M0 run
# t10-clearcheck, ep9 cascade). 20 ticks (0.2 s) covers the executor's
# reset_done round-trip and is far shorter than the goal->grasp latency,
# so no real command for the NEW episode is dropped.
RESET_SETTLE_TICKS = 20


def _quat_matrix_wxyz(quat: np.ndarray) -> np.ndarray:
    """Rotation matrix for a Genesis-order (w, x, y, z) quaternion."""
    w, x, y, z = np.asarray(quat, dtype=np.float64)
    return np.array(
        [
            [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
            [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
            [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
        ],
        dtype=np.float64,
    )


def frame_point_wxyz(
    link_pos: np.ndarray, link_quat: np.ndarray, local_offset: np.ndarray
) -> np.ndarray:
    """Transform a fixed-frame point from a link into world coordinates."""
    return np.asarray(link_pos, dtype=np.float64) + _quat_matrix_wxyz(link_quat) @ np.asarray(
        local_offset, dtype=np.float64
    )


def _yaw_matrix_wxyz(quat: np.ndarray) -> np.ndarray:
    """World-z rotation from a Genesis-order (w, x, y, z) quaternion."""
    w, x, y, z = np.asarray(quat, dtype=np.float64)
    yaw = float(np.arctan2(2 * (w * z + x * y), 1 - 2 * (y * y + z * z)))
    c, s = np.cos(yaw), np.sin(yaw)
    return np.array([[c, -s, 0.0], [s, c, 0.0], [0.0, 0.0, 1.0]], dtype=np.float64)


@dataclass
class CarryLatch:
    """Pure nearest-object upright kinematic carry coupling (ADR-18 pattern)."""

    close_threshold: float
    release_threshold: float
    max_distance_m: float
    held_name: str | None = None
    offset_pos: np.ndarray | None = None
    held_quat: np.ndarray | None = None

    def reset(self) -> None:
        self.held_name = None
        self.offset_pos = None
        self.held_quat = None

    def update(
        self,
        grip: float,
        hand_pos: np.ndarray,
        hand_quat: np.ndarray,
        candidates: dict[str, tuple[np.ndarray, np.ndarray]],
    ) -> tuple[str, np.ndarray, np.ndarray] | None:
        hand_pos = np.asarray(hand_pos, dtype=np.float64)
        hand_quat = np.asarray(hand_quat, dtype=np.float64)
        if self.held_name is not None and grip <= self.release_threshold:
            self.reset()
            return None
        if self.held_name is None and grip >= self.close_threshold:
            nearest = min(
                (
                    (float(np.linalg.norm(np.asarray(pos) - hand_pos)), name)
                    for name, (pos, _quat) in candidates.items()
                ),
                default=None,
            )
            if nearest is None or nearest[0] > self.max_distance_m:
                return None
            self.held_name = nearest[1]
            box_pos, box_quat = candidates[self.held_name]
            rotation = _yaw_matrix_wxyz(hand_quat)
            self.offset_pos = rotation.T @ (np.asarray(box_pos) - hand_pos)
            # Like ADR-18's store carry latch, retain the carton's upright
            # world orientation instead of inheriting hand pitch/roll.  A
            # five-axis radial transfer necessarily changes hand attitude;
            # propagating it tipped otherwise-upright cartons onto their
            # side before release.
            self.held_quat = np.asarray(box_quat, dtype=np.float64).copy()
        if self.held_name is None:
            return None
        rotation = _yaw_matrix_wxyz(hand_quat)
        return (
            self.held_name,
            (hand_pos + rotation @ self.offset_pos).astype(np.float32),
            self.held_quat.astype(np.float32),
        )


@dataclass(frozen=True)
class BridgeConfig:
    seed: int
    embodiment: str
    n_envs: int
    scene: str = "pharmacy"  # "pharmacy" (desk) | "store" (T15 retail)
    scenario: str = "S1"  # store episode scenario (RS-3)
    # AISLE_HEADLESS=0 opens Genesis's interactive viewer — a DEBUGGING
    # mode (per-frame rendering slows episodes; never for measured runs).
    # Default stays headless: every recorded pipeline is unaffected.
    headless: bool = True
    # AISLE_STEP_WITHOUT_RESET=1 lets ticks step physics before the first
    # reset — a BRING-UP mode for reset-less debug graphs. Default off
    # (CON-5/ADR-25, issue #71): the first step must not race the first
    # reset, so measured rollouts start episode 0 at sim step 0 exactly.
    step_without_reset: bool = False
    # TC-9's perception rung, declared in the GRAPH (node env) so the graph
    # hash attests which pose source a result used. L0: ground-truth `poses`.
    # L1: no `poses`, segmentation instead, pose estimated. L2: neither.
    perception: str = "L0"
    # CON-5: rollout resolves and attests this backend before launch. None is
    # the deterministic portable default for direct/debug graph execution.
    sim_backend: str | None = None
    # T2: printed med labels rendered as box textures. Graph-declared
    # (AISLE_LABELS on the bridge node) so the graph hash attests whether a
    # run's pixels carried text -- same reasoning as the rung; ambient env
    # is scrubbed by rollout. Default off: pre-T2 scenes stay byte-identical.
    labels: bool = False
    # T2 no-color-prior (AISLE_SHUFFLE_COLORS): seeded permutation of box
    # colors across meds; graph-declared and hash-attested like labels
    shuffle_colors: bool = False
    # T3 occlusion layout (AISLE_OCCLUSION): blocker parked in front of
    # the seed-designated target; graph-declared and hash-attested
    occlusion: bool = False


PERCEPTION_RUNGS = ("L0", "L1", "L2")


def parse_bridge_config(env: dict) -> BridgeConfig:
    """BRG-1: node configuration from environment variables."""
    # only an ABSENT key defaults. Membership, not `is None` and not falsiness:
    # `AISLE_PERCEPTION:` with no value parses from YAML as None, and
    # `AISLE_PERCEPTION: ""` is the empty string -- both are a graph DECLARING a
    # rung, so both must reach the refusal below rather than inherit L0. Each
    # narrower version of this test let one more shape through: `or "L0"` let
    # "" and "   " through, `is None` let YAML null through.
    perception = (
        str(env.get("AISLE_PERCEPTION") or "").strip().upper()
        if "AISLE_PERCEPTION" in env
        else "L0"
    )
    if perception not in PERCEPTION_RUNGS:
        # TC-9: an unrecognized rung must not silently fall back to L0 — that
        # would publish ground-truth pose to a graph that asked not to have it
        # and report the result under the rung it typo'd.
        raise ValueError(
            f"unknown perception rung {perception!r} (TC-9: {'|'.join(PERCEPTION_RUNGS)})"
        )
    labels_raw = (
        str(env.get("AISLE_LABELS") or "").strip().lower() if "AISLE_LABELS" in env else "0"
    )
    if labels_raw not in ("0", "1", "true", "false", "yes", "no"):
        # same refuse-don't-guess rule as the rung: a typo'd toggle must not
        # silently pick a scene whose pixels the run then attests
        raise ValueError(f"unknown AISLE_LABELS value {labels_raw!r}; use 0/1")
    shuffle_raw = (
        str(env.get("AISLE_SHUFFLE_COLORS") or "").strip().lower()
        if "AISLE_SHUFFLE_COLORS" in env
        else "0"
    )
    if shuffle_raw not in ("0", "1", "true", "false", "yes", "no"):
        raise ValueError(f"unknown AISLE_SHUFFLE_COLORS value {shuffle_raw!r}; use 0/1")
    occlusion_raw = (
        str(env.get("AISLE_OCCLUSION") or "").strip().lower() if "AISLE_OCCLUSION" in env else "0"
    )
    if occlusion_raw not in ("0", "1", "true", "false", "yes", "no"):
        raise ValueError(f"unknown AISLE_OCCLUSION value {occlusion_raw!r}; use 0/1")
    sim_backend = env.get("AISLE_SIM_BACKEND")
    if sim_backend is not None:
        sim_backend = str(sim_backend).strip().lower()
        if sim_backend not in ("cpu", "metal", "cuda"):
            raise ValueError(
                f"unknown simulation backend {sim_backend!r}; expected cpu, metal, or cuda"
            )
    return BridgeConfig(
        perception=perception,
        seed=int(env.get("AISLE_SEED", "0")),
        embodiment=env.get("AISLE_EMBODIMENT", "franka"),
        n_envs=int(env.get("AISLE_N_ENVS", "1")),
        scene=env.get("AISLE_SCENE", "pharmacy"),
        scenario=env.get("AISLE_SCENARIO", "S1"),
        headless=env.get("AISLE_HEADLESS", "1") not in ("0", "false", "no"),
        step_without_reset=env.get("AISLE_STEP_WITHOUT_RESET", "0").strip().lower()
        in ("1", "true", "yes"),
        sim_backend=sim_backend,
        labels=labels_raw in ("1", "true", "yes"),
        shuffle_colors=shuffle_raw in ("1", "true", "yes"),
        occlusion=occlusion_raw in ("1", "true", "yes"),
    )


def require_single_env_for_mobile(embodiment: str, n_envs: int) -> None:
    """SPEC 210 MOB-1 (ADR-13): the kinematic base carries ONE base_cmd /
    base_pose per bridge and batched genesis re-basing is not implemented,
    so the mobile profile refuses n_envs > 1 rather than integrate one
    global pose and mislabel it under every env_id. Single-env per bridge
    until batched re-basing lands."""
    if embodiment == "mobile" and n_envs > 1:
        raise ValueError(
            f"mobile embodiment does not support batched envs (n_envs={n_envs}); "
            "run one env per bridge (SPEC 210 MOB-1, ADR-13)"
        )


def require_usable_segmentation_ids(segmentation_ids: dict, perception: str) -> None:
    """TC-9: at L1 the id map is LOAD-BEARING, so refuse an unusable one.

    A consumer cannot derive these ids — they are genesis's own numbering — so
    an EMPTY or PARTIAL map means every L1 pose estimate refuses, one stderr
    line at a time, and the episode dies on a timeout that scores as a policy
    failure. Fail at startup instead, the way BRG-8 requires calibration rather
    than defaulting it.

    Both shapes, not just the partial one: for an empty map the partial check is
    vacuously satisfied, so the guard passed and bridge_info announced
    `"segmentation_ids": {}` at L1 — attested-looking and unusable, the same
    empty-vs-absent confusion as the rung parsing itself."""
    if perception != "L1":
        return
    blank = sorted(name for name, ids in segmentation_ids.items() if not ids)
    if not segmentation_ids or blank:
        raise ValueError(
            "perception rung L1 but no segmentation ids resolved for "
            f"{blank or 'any object (the scene declared no graspables)'} — "
            "the scene's segmentation_idx_dict did not yield entity indices for "
            "them (check VisOptions.segmentation_level); an L1 run needs this map "
            "to estimate pose at all (TC-9)"
        )


def require_supported_perception(cfg: BridgeConfig) -> None:
    """TC-9: refuse a rung this bridge cannot actually serve for the scene.

    The store scene keys its graspables by ITEM ID (`f"{slot_id}#{k}"`, plus
    `f"bin#{category}"`) while the only L1 consumer asks by MED NAME
    (segmented_pose's `seg_ids_for(id_map, target_med)`). Those namespaces never
    intersect, so an L1 store run would announce a well-formed id map and then
    refuse every pose, dying on a timeout that scores as a POLICY failure. The
    empty-entry guard cannot see it either: store ids resolve to real seg ids,
    they are just unaskable. Refuse at config time until a store L1 consumer
    exists — a loud refusal beats a run that looks like bad luck. The refusal
    reaches an operator through dora's per-node log
    (`runs/<id>/out/<dataflow>/log_dora-genesis.jsonl`, which carries
    `"stream":"stderr"` rows), not through the rollout result JSON."""
    # L2 is served since detected-pose landed (idea I7): RGB alone supplies
    # identity, while same-stamp ordinary sensor depth supplies metric
    # geometry. The STORE stays refused at both estimated
    # rungs for the same namespace reason: the estimators ask by desk MED
    # NAME (segmented_pose's id map keys; detected-pose's detector
    # vocabulary), while the store keys items by slot/category ids.
    if cfg.perception in ("L1", "L2") and cfg.scene == "store":
        raise ValueError(
            f"perception rung {cfg.perception} is not supported for the store scene: "
            "the store keys items by item id while the estimated-pose rungs ask by "
            "desk med name (L1's id map, L2's detector vocabulary), so every estimate "
            "would refuse (TC-9). Run the store at L0, or teach the estimators the "
            "store namespace first."
        )


def require_valid_store_config(cfg: BridgeConfig) -> None:
    """T15/T16 (ADR-18/ADR-19): the store scene is mobile-only (fixed-base
    robots cannot reach across aisles) and single-env. Every scenario rolls
    through teleport reset: the build set is episode-independent (full
    stock) and the reset realizes S1/S2/S3 by stash/swap teleports."""
    if cfg.scene != "store":
        return
    if cfg.embodiment != "mobile":
        raise ValueError(f"store scene requires the mobile embodiment, got {cfg.embodiment!r}")
    if cfg.n_envs != 1:
        raise ValueError("store scene is single-env (ADR-13/ADR-18)")
    if cfg.scenario not in ("S1", "S2", "S3"):
        raise ValueError(f"unknown store scenario {cfg.scenario!r} (RS-3: S1|S2|S3)")


class ResetQuarantine:
    """BRG-4: after a reset the executor keeps streaming the ended episode's
    plan for a few ticks until it receives reset_done and clears. Those
    stale joint_cmds would drive the just-teleported-home arm back off home,
    so the bridge holds the arm at home and DROPS commands while quarantined
    — `arm()` on reset, `hold()` once per tick returns True while active and
    consumes one tick (M0 run t10-clearcheck ep9 cascade).

    Fleet mode (BRG-5): quarantine is PER ENV — a reset of env k must not
    freeze the other envs' episodes. `arm(env_id)`/`held(env_id)` key a
    per-env countdown; `tick()` advances every active countdown once.
    env_id None = every env (the single-env and legacy whole-scene path)."""

    def __init__(self, ticks: int, n_envs: int = 1):
        self.ticks = int(ticks)
        self.n_envs = int(n_envs)
        self._remaining: dict[int, int] = {}

    def arm(self, env_id: int | None = None) -> None:
        if self.ticks <= 0:
            return  # settle disabled: never quarantine
        targets = range(self.n_envs) if env_id is None else (env_id,)
        for env in targets:
            self._remaining[env] = self.ticks

    def tick(self) -> None:
        for env in [e for e, r in self._remaining.items() if r > 0]:
            self._remaining[env] -= 1
            if self._remaining[env] <= 0:
                del self._remaining[env]

    def held(self, env_id: int) -> bool:
        return self._remaining.get(env_id, 0) > 0

    def any_held(self) -> bool:
        return bool(self._remaining)

    def hold(self) -> bool:
        """Legacy single-env form: advance and report (arm() with no env)."""
        active = self.any_held()
        self.tick()
        return active


class RateScheduler:
    """Integer-exact per-topic rate divider: topic fires when the count of
    contract periods elapsed exceeds the count already fired. No float
    accumulation drift (CON-5)."""

    def __init__(self, rates: dict[str, int], dt: float):
        self.rates = dict(rates)
        self.dt = dt
        self.tick = 0
        self.fired = dict.fromkeys(rates, 0)

    def due(self) -> list[str]:
        self.tick += 1
        fired = []
        for topic, rate in self.rates.items():
            target = int(self.tick * self.dt * rate + 1e-9)
            if target > self.fired[topic]:
                fired.append(topic)
                self.fired[topic] = target
        return fired


class CommandQueue:
    """BRG-1/BRG-3/BRG-5: keep only the latest command per (kind, env)
    between ticks, counting superseded ones — but preserve ARRIVAL ORDER
    across kinds when applying (joint_cmd spans all dofs incl. fingers, so
    whichever command arrived last must win). Missing env_id is an error in
    multi-env mode and defaults to 0 in single-env mode (TC-2); env_id must
    be an int within [0, n_envs)."""

    def __init__(self, n_envs: int):
        self.n_envs = n_envs
        self._arrival = 0
        self._pending: dict[tuple[str, int], tuple[object, int, int]] = {}

    def push(self, kind: str, env_id: int | None, payload) -> None:
        if env_id is None:
            if self.n_envs > 1:
                raise ValueError(f"{kind} missing env_id in multi-env mode (BRG-5)")
            env_id = 0
        # strictly integral: bool/float coercion would silently misroute
        # (0.7 -> env 0, True -> env 1)
        if isinstance(env_id, bool) or not isinstance(env_id, int):
            raise ValueError(f"{kind} env_id must be an int, got {env_id!r} (BRG-5)")
        if not 0 <= env_id < self.n_envs:
            raise ValueError(f"{kind} env_id {env_id} outside [0, {self.n_envs}) (BRG-5)")
        self._arrival += 1
        key = (kind, env_id)
        dropped = self._pending[key][1] + 1 if key in self._pending else 0
        self._pending[key] = (payload, dropped, self._arrival)

    def drain(self, env_id: int | None = None) -> list[tuple[str, int, object, int]]:
        """(kind, env_id, payload, dropped) in arrival order of each
        surviving command. With `env_id`, drains ONLY that env's pending
        commands (fleet mode: a per-env reset must not flush the other
        envs' in-flight commands)."""
        if env_id is None:
            items = sorted(self._pending.items(), key=lambda kv: kv[1][2])
            self._pending = {}
        else:
            keys = [key for key in self._pending if key[1] == env_id]
            items = sorted(((k, self._pending.pop(k)) for k in keys), key=lambda kv: kv[1][2])
        return [(kind, env, payload, dropped) for (kind, env), (payload, dropped, _) in items]


def make_bridge_info(
    embodiment: str,
    n_dof: int,
    n_envs: int,
    genesis_version: str,
    env_hash: str,
    step_without_reset: bool,
    calibration: dict,
    perception: str,
    segmentation_ids: dict,
    sim_backend: str,
) -> str:
    """BRG-6 + BRG-8: the startup contract announcement, as a JSON string.

    step_without_reset is surfaced so a run whose bridge free-ran before the
    first reset (ADR-25 bring-up mode) is auditable from its traces — the
    env var alone would leave no attestation footprint (issue #71).

    calibration is the VER-8 v1 block (SPEC 040) built from the REALIZED
    camera state — post-DR-jitter, the same values the render path uses.
    Required, not defaulted: the realistic verifier's stage 0 refuses to
    judge without it, so a bridge that forgot to wire it must fail loudly
    rather than publish a judgeable-looking run with no calibration.

    perception and segmentation_ids are REQUIRED for the same reason
    calibration is: a caller that forgot `perception=` would attest "L0" in the
    trace for a run that executed L1, which is exactly the recorded-vs-actual
    divergence the rung refusal and the env scrub exist to prevent -- and no
    test can catch it, because the defaulted value is a VALID one.

    perception is TC-9's rung, announced so a RECORDED run attests which pose
    source it used — the graph declares it, but a trace read on its own would
    otherwise not say. segmentation_ids maps med name -> the seg ids in
    `seg_overhead` (L1 only, empty otherwise): the ids are the simulator's own
    segmentation map, NOT entity indices, so a consumer that derives them
    silently selects other geometry (measured: robot links with identical
    pixel counts across different layouts). Publishing the map is what keeps
    a consumer from having to guess. sim_backend is required for the same
    recorded-vs-actual reason: CUDA and CPU traces must not look identical."""
    return json.dumps(
        {
            "contract": "v0",
            "perception": perception,
            "segmentation_ids": segmentation_ids,
            "embodiment": embodiment,
            "n_dof": n_dof,
            "n_envs": n_envs,
            "genesis_version": genesis_version,
            "platform": f"{platform.system().lower()}-{platform.machine()}",
            "env_hash": env_hash,
            "step_without_reset": step_without_reset,
            "sim_backend": sim_backend,
            "calibration": calibration,
        }
    )


def segmentation_id_map(idx_dict: dict, entity_idx: dict) -> dict[str, list[int]]:
    """TC-9: {med name: [seg ids]} from genesis's OWN segmentation map.

    `idx_dict` is `scene.segmentation_idx_dict`: seg id -> genesis's seg_key,
    with a bare -1 for background. `entity_idx` is {name: entity.idx}. The two
    are NOT the same numbering — measured on the desk scene, entity 5
    (amoxicillin) is seg id 16 — which is why a consumer must be handed this
    map rather than masking on the entity index. A multi-link entity
    contributes every one of its ids, sorted so the map is deterministic
    (CON-5).

    The seg_key SHAPE depends on `VisOptions.segmentation_level`, which the
    scene does not currently set (genesis defaults to `link`). Read from
    genesis's own construction: `geom` gives (entity, link, geom), `link`
    gives (entity, link), and `entity` gives a BARE int. All three are handled
    because the entity index is what this map needs and it is first in every
    shape — an earlier version required a tuple, so at `segmentation_level=
    "entity"` it would have returned an empty id list for every object,
    making the L1 estimator refuse every pose with only a stderr line to say
    why. A silent downgrade to "refuse everything" is the worst of the three
    possible failures."""
    by_entity: dict[int, list[int]] = {}
    for seg_id, ref in idx_dict.items():
        entity = ref[0] if isinstance(ref, (tuple, list)) and ref else ref
        if isinstance(entity, (int, np.integer)) and int(entity) >= 0:
            by_entity.setdefault(int(entity), []).append(int(seg_id))
    return {name: sorted(by_entity.get(int(idx), [])) for name, idx in entity_idx.items()}


def realized_calibration(handle, physics: dict, is_store: bool) -> dict:
    """BRG-8: the v1 calibration block from the BUILT scene — the
    overhead pose is read back from the camera transform (so DR jitter is
    reflected), converted to the v1/OpenCV conventions by VER-8's own
    module. Store scenes use their own overhead nominals."""
    from aisle.scenes.pharmacy import to_numpy, wrist_mount_transform
    from aisle.verifier.calibration import GL_TO_CV, build_calibration_v1

    cams = handle.cams
    cam_cfg = physics["cameras"]
    overhead = cams["overhead"]
    transform = np.asarray(to_numpy(overhead.transform)).reshape(4, 4)
    wrist_transform = wrist_mount_transform(cam_cfg, physics["embodiment"][handle.embodiment])
    lookat = cam_cfg["store_overhead_lookat" if is_store else "overhead_lookat"]
    return build_calibration_v1(
        # the REALIZED rotation, converted GL->CV — not a re-derivation
        # from config (PR #103 review): stage 0 must be able to catch a
        # camera that is rotated in place
        overhead_rotation_cv=transform[:3, :3] @ GL_TO_CV,
        overhead_pos=transform[:3, 3].tolist(),
        overhead_lookat=lookat,
        overhead_resolution=overhead.res,
        overhead_fov_deg=overhead.fov,
        wrist_offset_m=wrist_transform[:3, 3].tolist(),
        wrist_mount_rotation_gl=wrist_transform[:3, :3],
        wrist_resolution=cams["wrist"].res,
        wrist_fov_deg=cams["wrist"].fov,
    )


def compute_env_hash(root: Path) -> str:
    """BRG-6/CON-7: the frozen-set hash, via the canonical tool."""
    proc = subprocess.run(
        [sys.executable, str(root / "tools" / "env_hash.py"), "--root", str(root)],
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(proc.stdout)["env_hash"]


def _metadata(sim_time_ns: int, env_id: int, seq: int, **extra) -> dict:
    """TC-2: mandatory metadata on every output message."""
    return {"sim_time_ns": sim_time_ns, "env_id": env_id, "seq": seq, **extra}


def main(clock: Callable[[], float] = time.perf_counter) -> None:
    """The clock is injected (CON-5): reset timing must never reach for a
    wall clock ad hoc."""
    import genesis
    import pyarrow as pa
    from dora import Node

    from aisle.mobility.base import base_scan_ranges, integrate_base_pose
    from aisle.scenes.pharmacy import (
        SceneCfg,
        build_scene,
        desk_scan_obstacles,
        load_physics,
        oracle_state,
        resolve_layout,
        sample_placements,
        select_genesis_backend,
        to_numpy,
    )

    cfg = parse_bridge_config(os.environ)
    require_single_env_for_mobile(cfg.embodiment, cfg.n_envs)
    require_valid_store_config(cfg)
    require_supported_perception(cfg)
    root = Path(os.environ.get("AISLE_ROOT", _REPO_ROOT))
    physics = load_physics()
    profile = physics["embodiment"][cfg.embodiment]
    dt = physics["sim"]["dt"]

    # T15 (ADR-18): the store scene swaps in behind the same topic contract
    # — entities/oracle/reset/scan come from the scene adapter below; the
    # pharmacy path is byte-for-byte unchanged.
    is_store = cfg.scene == "store"
    if is_store:
        from aisle.scenes.store import (
            build_store,
            generate_episode,
            load_planogram,
            store_oracle_state,
            store_scan_obstacles,
            teleport_store_reset,
        )

        handle = build_store(
            seed=cfg.seed,
            scenario=cfg.scenario,
            embodiment=cfg.embodiment,
            headless=cfg.headless,
            sim_backend=cfg.sim_backend,
        )
    else:
        handle = build_scene(
            seed=cfg.seed,
            embodiment=cfg.embodiment,
            n_envs=cfg.n_envs,
            headless=cfg.headless,
            cfg=SceneCfg(
                labels=cfg.labels, shuffle_colors=cfg.shuffle_colors, occlusion=cfg.occlusion
            ),
            sim_backend=cfg.sim_backend,
        )
    robot = handle.robot
    n_dof = robot.n_dofs
    # carry coupling needs the hand's world position (T15, ADR-18)
    hand_link = robot.get_link("hand") if is_store else None
    held_item: str | None = None  # carry latch (T15, ADR-18)
    held_offset = (0.0, 0.0, 0.0, 0.0)
    so101_latch = None
    so101_hand_link = None
    so101_tcp_offset = None
    if profile.get("kinematic_carry_latch", False):
        if cfg.n_envs != 1 or is_store:
            raise ValueError("SO-101 carry latch requires single-env pharmacy mode")
        so101_hand_link = robot.get_link(profile["ee_link"])
        so101_tcp_offset = np.asarray(profile["ee_frame_offset_xyz"], dtype=np.float64)
        so101_latch = CarryLatch(
            close_threshold=float(profile["carry_latch_close"]),
            release_threshold=float(profile["carry_latch_release"]),
            max_distance_m=float(profile["carry_latch_max_distance_m"]),
        )

    # the graspable set is named `items` in the store scene and `boxes` on the
    # desk; the id map itself is name -> seg ids either way
    graspable = handle.items if is_store else handle.boxes
    segmentation_ids = (
        segmentation_id_map(
            handle.scene.segmentation_idx_dict,
            {name: entity.idx for name, entity in graspable.items()},
        )
        if cfg.perception == "L1"
        else {}
    )
    require_usable_segmentation_ids(segmentation_ids, cfg.perception)

    node = Node()
    node.send_output(
        "bridge_info",
        pa.array(
            [
                make_bridge_info(
                    embodiment=cfg.embodiment,
                    n_dof=n_dof,
                    n_envs=cfg.n_envs,
                    genesis_version=genesis.__version__,
                    env_hash=compute_env_hash(root),
                    step_without_reset=cfg.step_without_reset,
                    calibration=realized_calibration(handle, physics, is_store),
                    perception=cfg.perception,
                    segmentation_ids=segmentation_ids,
                    sim_backend=cfg.sim_backend or select_genesis_backend("sim", platform.system()),
                )
            ]
        ),
        metadata=_metadata(0, 0, 0),
    )

    # SPEC 210 MOB-5: the store frame is published ONCE at startup. base
    # topics are (x, y, yaw) of the base origin in the store frame; the arm
    # mounts at the base origin (base frame == store frame at pose 0).
    if cfg.embodiment == "mobile":
        node.send_output(
            "frame_info",
            pa.array(
                [
                    json.dumps(
                        {
                            "store_frame": "store",
                            "base_frame": "base",
                            "base_pose": "(x, y, yaw) of the base origin in the store frame",
                            "arm_mount": "the arm root rides the base origin (ADR-13)",
                        }
                    )
                ]
            ),
            metadata=_metadata(0, 0, 0),
        )

    # SPEC 210 (T11, ADR-13): the mobile embodiment adds the kinematic base
    # topics. base_pose is integrated from base_cmd each tick and the arm's
    # root is re-based; base_scan is a planar raycast against the scene.
    is_mobile = cfg.embodiment == "mobile"
    topic_rates = rung_topic_rates(cfg.perception, is_mobile)
    base_pose = [float(v) for v in profile.get("base_start", [0.0, 0.0, 0.0])]
    base_cmd = [0.0, 0.0]
    if is_store:
        scan_obstacles = store_scan_obstacles(load_planogram())
    else:
        scan_obstacles = desk_scan_obstacles(physics, cfg.embodiment) if is_mobile else []

    scheduler = RateScheduler(topic_rates, dt)
    commands = CommandQueue(cfg.n_envs)
    seq: dict[tuple[str, int], int] = {}
    dropped_counts: dict[str, dict[int, int]] = {"joint": {}, "gripper": {}}
    sim_time_ns = 0
    # CON-5/ADR-25 (issue #71): the first physics step must not race the
    # first reset request — ticks are dropped until it lands, so episode 0
    # always starts from the seed-injected state at sim step 0. Two attested
    # expert_s1 runs diverged on whether one settle step ran pre-reset.
    awaiting_first_reset = not cfg.step_without_reset
    if awaiting_first_reset:
        print("holding at sim step 0 until the first reset (CON-5)", file=sys.stderr)
    quarantine = ResetQuarantine(RESET_SETTLE_TICKS)  # holds arm at home post-reset
    wire_dof_indices = profile_dof_indices(robot, profile)
    configured_names = profile_joint_names(profile)
    if configured_names is None:
        # one name per DOF in native payload order: multi-dof joints repeat,
        # zero-dof (fixed) joints vanish
        joint_names = []
        for joint in robot.joints:
            joint_names += [joint.name] * int(getattr(joint, "n_dofs", 1))
        wire_dof_indices = tuple(range(n_dof))
    else:
        joint_names = list(configured_names)
    assert len(joint_names) == n_dof, (len(joint_names), n_dof)

    home_hold = (
        from_wire_joint_order(np.asarray(profile["home_qpos"], dtype=np.float32), wire_dof_indices)
        if "home_qpos" in profile
        else None
    )
    gripper_open = profile.get("gripper_open_qpos", profile.get("gripper_open_m", 0.04))
    gripper_close = profile.get("gripper_close_qpos", profile.get("gripper_close_m", 0.0))
    gripper_dofs = int(profile.get("gripper_dofs", 2))
    finger_idx = list(wire_dof_indices[-gripper_dofs:])

    def send(topic: str, env_id: int, array: np.ndarray, **extra) -> None:
        key = (topic, env_id)
        seq[key] = seq.get(key, 0) + 1
        node.send_output(
            topic,
            pa.array(np.ravel(array)),
            metadata=_metadata(sim_time_ns, env_id, seq[key], **extra),
        )

    def env_slice(tensor, env_id: int) -> np.ndarray:
        data = to_numpy(tensor)
        return data[env_id] if cfg.n_envs > 1 else data.reshape(-1)

    def render_due(due: list[str]) -> dict[str, np.ndarray]:
        """BRG-2: one overhead pass serves rgb, depth and segmentation when
        they are due; nothing renders unless a camera topic is due this tick.

        TC-9: segmentation and depth come from ONE pass, so an L1 estimate
        that masks the seg and indexes the depth reads one scene rather than
        two ticks blended (the defect class that already reached the trace
        recorder and the realistic verifier)."""
        frames: dict[str, np.ndarray] = {}
        need_rgb = "rgb_overhead" in due
        need_seg = "seg_overhead" in due
        need_depth = "depth_overhead" in due
        if need_rgb or need_depth or need_seg:
            out = handle.cams["overhead"].render(rgb=True, depth=need_depth, segmentation=need_seg)
            frames["rgb_overhead"] = np.asarray(out[0], dtype=np.uint8)
            if need_depth:
                frames["depth_overhead"] = np.asarray(out[1], dtype=np.float32)
            if need_seg:
                # TC-1: the WIRE type is the contract. Genesis renders int64;
                # narrowing here (ids are ~21 in the desk scene) halves a
                # 640x480 payload at 15 Hz. A passthrough would be a TC-1
                # violation, not an optimization left on the table.
                frames["seg_overhead"] = np.asarray(out[2], dtype=np.int32)
        if "rgb_wrist" in due:
            frames["rgb_wrist"] = np.asarray(handle.cams["wrist"].render()[0], dtype=np.uint8)
        return frames

    def publish(topic: str, frames: dict[str, np.ndarray] | None = None) -> None:
        # TC-9: the rung's topic set is the SINGLE source of truth for what
        # this bridge may put on the wire, and the gate belongs here rather
        # than in the scheduler. The reset path publishes directly, off the
        # scheduler (RESET_PUBLISH below), so gating the scheduler alone let
        # ground-truth `poses` reach an L1 wire once per reset — once per
        # episode, at the freshest possible moment, and into the trace the
        # recorder keeps. Every future direct call is gated by construction.
        if not may_publish(topic, topic_rates):
            return
        oracle_cache = None
        frames = frames if frames is not None else render_due([topic])
        qpos = robot.get_qpos() if topic in ("joint_state", "gripper_state") else None
        # camera topics: genesis batched scenes render ONE view; publishing
        # it per env would mislabel pixels (ADR-7) — env 0 only
        n_targets = 1 if topic in RENDER_TOPICS else cfg.n_envs
        for env_id in range(n_targets):
            if topic == "joint_state":
                send(
                    topic,
                    env_id,
                    to_wire_joint_order(env_slice(qpos, env_id), wire_dof_indices),
                    names=joint_names,
                    dropped=dropped_counts["joint"].pop(env_id, 0),
                )
            elif topic == "gripper_state":
                finger = env_slice(qpos, env_id)[finger_idx[0]]
                width = np.float32((gripper_open - finger) / (gripper_open - gripper_close or 1.0))
                send(
                    topic,
                    env_id,
                    np.clip(width, 0.0, 1.0),
                    dropped=dropped_counts["gripper"].pop(env_id, 0),
                )
            elif topic in ("oracle_state", "poses"):
                if oracle_cache is None:
                    oracle_cache = store_oracle_state(handle) if is_store else oracle_state(handle)
                send(topic, env_id, oracle_cache[env_id] if cfg.n_envs > 1 else oracle_cache)
            elif topic in ("rgb_overhead", "rgb_wrist"):
                rgb = frames[topic]
                send(topic, env_id, rgb, h=rgb.shape[0], w=rgb.shape[1], enc="rgb8")
            elif topic == "depth_overhead":
                depth = frames[topic]
                send(topic, env_id, depth, h=depth.shape[0], w=depth.shape[1], enc="depth32f")
            elif topic == "seg_overhead":
                seg = frames[topic]
                send(topic, env_id, seg, h=seg.shape[0], w=seg.shape[1], enc="seg_i32")
            elif topic == "base_pose":
                # report the PHYSICAL root, not the integrator (PR #21): a
                # path that moves one but not the other (e.g. a reset that
                # only re-homed the variable) must be visible on the wire,
                # never an invisible reported-vs-physical divergence
                p = to_numpy(robot.get_pos()).reshape(-1)[:3]
                q = to_numpy(robot.get_quat()).reshape(-1)[:4]
                yaw = float(
                    np.arctan2(2 * (q[0] * q[3] + q[1] * q[2]), 1 - 2 * (q[2] * q[2] + q[3] * q[3]))
                )
                send(topic, env_id, np.array([p[0], p[1], yaw], dtype=np.float32))
            elif topic == "base_scan":
                ranges = base_scan_ranges(
                    base_pose,
                    scan_obstacles,
                    n=int(profile["base_scan_n"]),
                    angle_min=float(profile["base_scan_angle_min"]),
                    angle_max=float(profile["base_scan_angle_max"]),
                    range_max=float(profile["base_scan_range_max_m"]),
                )
                send(
                    topic,
                    env_id,
                    np.asarray(ranges, dtype=np.float32),
                    angle_min=float(profile["base_scan_angle_min"]),
                    angle_max=float(profile["base_scan_angle_max"]),
                    n=int(profile["base_scan_n"]),
                )

    def apply_commands() -> None:
        # BRG-1: apply in arrival order across kinds — the last-arrived
        # command owns any overlapping dofs
        for kind, env_id, payload, dropped in commands.drain():
            if kind == "joint":
                target = from_wire_joint_order(
                    np.asarray(payload, dtype=np.float32), wire_dof_indices
                )
                if cfg.n_envs > 1:
                    robot.control_dofs_position(target[None, :], envs_idx=[env_id])
                else:
                    robot.control_dofs_position(target)
            else:
                width = float(np.asarray(payload).reshape(-1)[0])
                finger = gripper_open - width * (gripper_open - gripper_close)
                # ONLY the embodiment's gripper dofs (so101 has one, franka
                # two): an all-dof write would cancel the arm trajectory
                finger_target = np.full(len(finger_idx), finger, dtype=np.float32)
                if cfg.n_envs > 1:
                    robot.control_dofs_position(
                        finger_target[None, :], dofs_idx_local=finger_idx, envs_idx=[env_id]
                    )
                else:
                    robot.control_dofs_position(finger_target, dofs_idx_local=finger_idx)
            dropped_counts[kind][env_id] = dropped_counts[kind].get(env_id, 0) + dropped

    def teleport_reset(seed: int, env_id: int | None = None) -> None:
        """BRG-4: state injection — no process restart, no scene rebuild.
        Desk: a fresh placement sample per seed. Store: the SEED's episode
        layout for the configured scenario (T16, ADR-19) — the same
        generator that produces the goal drives the physical state, so the
        two can never disagree (RS-3, CON-5).

        Fleet mode (BRG-5): `env_id` teleports ONLY that env's slice —
        boxes, arm home, command drain and quarantine are all per-env, so
        one agent's reset never perturbs its neighbours' episodes.
        env_id None keeps the legacy whole-scene semantics bit for bit."""
        if is_store:
            teleport_store_reset(handle, generate_episode(seed, cfg.scenario))
        else:
            layout = resolve_layout(physics, cfg.embodiment)
            placements = sample_placements(seed, list(handle.boxes), layout)
            if cfg.occlusion:
                # T3: the reset path is where EPISODE scenes come from —
                # the build-time layout is overwritten on the first
                # teleport, so occlusion applied only at build measured
                # a plain-T1 run while attesting a T3 graph (first
                # baseline: pass@1 1.0, the giveaway)
                from aisle.scenes.pharmacy import apply_occlusion

                placements = apply_occlusion(placements, seed, list(handle.boxes), layout)
            for placement in placements:
                entity = handle.boxes[placement.name]
                pos = np.array([placement.x, placement.y, placement.z], dtype=np.float32)
                quat = np.array([1.0, 0.0, 0.0, 0.0], dtype=np.float32)  # genesis wxyz
                if cfg.n_envs > 1 and env_id is not None:
                    entity.set_pos(pos[None, :], envs_idx=[env_id])
                    entity.set_quat(quat[None, :], envs_idx=[env_id])
                elif cfg.n_envs > 1:
                    entity.set_pos(np.tile(pos, (cfg.n_envs, 1)))
                    entity.set_quat(np.tile(quat, (cfg.n_envs, 1)))
                else:
                    entity.set_pos(pos)
                    entity.set_quat(quat)
                # velocity zeroing is env-sliced too: the global zero
                # froze the OTHER agent's carried box mid-swing (fleet
                # probe: phantom 'dropped')
                entity.zero_all_dofs_velocity(
                    envs_idx=[env_id] if cfg.n_envs > 1 and env_id is not None else None
                )
        if "home_qpos" in profile:
            home = from_wire_joint_order(
                np.asarray(profile["home_qpos"], dtype=np.float32), wire_dof_indices
            )
            if cfg.n_envs > 1 and env_id is not None:
                robot.set_qpos(home[None, :], envs_idx=[env_id])
                robot.control_dofs_position(home[None, :], envs_idx=[env_id])
            else:
                batched_home = home if cfg.n_envs == 1 else np.tile(home, (cfg.n_envs, 1))
                robot.set_qpos(batched_home)
                # re-latch the PD controller: a stale pre-reset target would
                # drive the arm away from home on the first post-reset tick
                robot.control_dofs_position(batched_home)
        robot.zero_all_dofs_velocity(
            envs_idx=[env_id] if cfg.n_envs > 1 and env_id is not None else None
        )
        # pre-reset commands must not leak into the new episode (CON-5);
        # per-env resets drain only their own env's pending commands
        commands.drain(env_id)
        if env_id is None:
            for counts in dropped_counts.values():
                counts.clear()
        else:
            for counts in dropped_counts.values():
                counts.pop(env_id, None)
        # hold the arm at home for the next few ticks: the executor keeps
        # streaming the ended episode's plan until it sees reset_done, and
        # those in-flight joint_cmds would otherwise drive the arm off home
        if home_hold is not None:
            quarantine.arm(env_id)

    for event in node:
        if event["type"] != "INPUT":
            continue
        input_id = event["id"]
        metadata = event.get("metadata") or {}
        if input_id == "tick":
            if awaiting_first_reset:
                continue
            held_envs = (
                [env for env in range(cfg.n_envs) if quarantine.held(env)]
                if home_hold is not None
                else []
            )
            quarantine.tick()
            # mobile keeps the whole-scene semantics (single-env by spec)
            settling = bool(held_envs) and len(held_envs) == cfg.n_envs
            if settling:
                # every env settling (single-env case unchanged): hold the
                # arm(s) at home and DROP stale in-flight joint_cmds so
                # they cannot drive the just-homed arm off home
                commands.drain()
                batched = home_hold if cfg.n_envs == 1 else np.tile(home_hold, (cfg.n_envs, 1))
                robot.control_dofs_position(batched)
            elif held_envs:
                # fleet mode: only the quarantined envs hold and drain —
                # the others' episodes keep executing
                for env in held_envs:
                    commands.drain(env)
                    robot.control_dofs_position(home_hold[None, :], envs_idx=[env])
                apply_commands()
            else:
                apply_commands()
            if is_mobile:
                # KINEMATIC GRASP ATTACH (T15 rounds 14-18, ADR-18): the
                # kinematic base teleports the arm, and repeated pinning
                # destroyed the physical pinch (round 18: the box dropped
                # the moment physics resumed). Standard sim solution: from
                # grip close to finger open the held box rides the HAND
                # LINK every tick — physics never needs to hold it. Latch
                # captures the hand-frame offset; release hands the box
                # back to physics at the drop hover.
                if is_store and hand_link is not None:
                    fingers = float(np.mean(to_numpy(robot.get_qpos()).reshape(-1)[-2:]))
                    hand_pos = to_numpy(hand_link.get_pos()).reshape(-1)[:3]
                    hq = to_numpy(hand_link.get_quat()).reshape(-1)[:4]
                    # yaw of the wrist-down flange (w,x,y,z quat)
                    hand_yaw = float(
                        np.arctan2(
                            2 * (hq[0] * hq[3] + hq[1] * hq[2]),
                            1 - 2 * (hq[2] * hq[2] + hq[3] * hq[3]),
                        )
                    )
                    if held_item is None and fingers < 0.025:
                        best_id, best_d = None, 0.15
                        for item_id, entity in handle.items.items():
                            p = to_numpy(entity.get_pos()).reshape(-1)[:3]
                            d = float(np.linalg.norm(p - hand_pos))
                            if d < best_d:
                                best_id, best_d = item_id, d
                        if best_id is not None:
                            p = to_numpy(handle.items[best_id].get_pos()).reshape(-1)[:3]
                            q = to_numpy(handle.items[best_id].get_quat()).reshape(-1)[:4]
                            item_yaw = 2.0 * float(np.arctan2(float(q[3]), float(q[0])))
                            cos_h, sin_h = np.cos(-hand_yaw), np.sin(-hand_yaw)
                            dx, dy = p[0] - hand_pos[0], p[1] - hand_pos[1]
                            held_item = best_id
                            held_offset = (
                                float(dx * cos_h - dy * sin_h),
                                float(dx * sin_h + dy * cos_h),
                                float(p[2] - hand_pos[2]),
                                float(item_yaw - hand_yaw),
                            )
                            print(f"carry latch: {best_id}", file=sys.stderr)
                    elif held_item is not None and fingers > 0.035:
                        print(f"carry release: {held_item}", file=sys.stderr)
                        held_item = None
                    if held_item is not None:
                        off = held_offset
                        cos_h, sin_h = np.cos(hand_yaw), np.sin(hand_yaw)
                        held_entity = handle.items[held_item]
                        held_entity.set_pos(
                            np.array(
                                [
                                    hand_pos[0] + off[0] * cos_h - off[1] * sin_h,
                                    hand_pos[1] + off[0] * sin_h + off[1] * cos_h,
                                    hand_pos[2] + off[2],
                                ],
                                dtype=np.float32,
                            )
                        )
                        hh = (hand_yaw + off[3]) / 2
                        held_entity.set_quat(
                            np.array([np.cos(hh), 0.0, 0.0, np.sin(hh)], dtype=np.float32)
                        )
                        held_entity.zero_all_dofs_velocity()
                # MOB-1/ADR-13: integrate the base from the latest base_cmd
                # (held at rest during the post-reset settle) and re-base the
                # arm's root before stepping
                cmd = [0.0, 0.0] if settling else base_cmd
                new_pose = integrate_base_pose(base_pose, cmd, dt)
                # re-base ONLY when the base actually moved (T15 round 13):
                # an every-tick set_pos/set_quat perturbs the solver state
                # each step and the gravity-loaded wrist joints chronically
                # lagged ~0.1-0.7 rad — the fingers plowed instead of
                # pinching. A stationary base leaves the arm's PD untouched,
                # matching the (proven) desk behavior.
                if new_pose != base_pose:
                    base_pose = new_pose
                    half = base_pose[2] / 2
                    robot.set_pos(np.array([base_pose[0], base_pose[1], 0.0], dtype=np.float32))
                    robot.set_quat(
                        np.array([np.cos(half), 0.0, 0.0, np.sin(half)], dtype=np.float32)
                    )
            if so101_latch is not None:
                finger = float(to_numpy(robot.get_qpos()).reshape(-1)[finger_idx[0]])
                grip = float(
                    np.clip((gripper_open - finger) / (gripper_open - gripper_close), 0.0, 1.0)
                )
                hand_pos = to_numpy(so101_hand_link.get_pos()).reshape(-1)[:3]
                hand_quat = to_numpy(so101_hand_link.get_quat()).reshape(-1)[:4]
                tcp_pos = frame_point_wxyz(hand_pos, hand_quat, so101_tcp_offset)
                candidates = {
                    name: (
                        to_numpy(entity.get_pos()).reshape(-1)[:3],
                        to_numpy(entity.get_quat()).reshape(-1)[:4],
                    )
                    for name, entity in handle.boxes.items()
                }
                before = so101_latch.held_name
                attached = so101_latch.update(grip, tcp_pos, hand_quat, candidates)
                if before != so101_latch.held_name:
                    action = "latch" if so101_latch.held_name is not None else "release"
                    print(
                        f"so101 carry {action}: {before or so101_latch.held_name}", file=sys.stderr
                    )
                if attached is not None:
                    name, pos, quat = attached
                    entity = handle.boxes[name]
                    entity.set_pos(pos)
                    entity.set_quat(quat)
                    entity.zero_all_dofs_velocity()
            handle.scene.step()  # BRG-7: exceptions crash the node loudly
            sim_time_ns += int(dt * 1e9)
            due = scheduler.due()
            frames = render_due(due)
            for topic in due:
                publish(topic, frames)
        elif input_id == "joint_cmd":
            payload = np.asarray(
                event["value"].to_numpy(zero_copy_only=False), dtype=np.float32
            ).reshape(-1)
            if payload.shape[0] != n_dof:
                raise ValueError(
                    f"joint_cmd must be Float32[{n_dof}], got length {payload.shape[0]} (TC-5)"
                )
            commands.push("joint", metadata.get("env_id"), payload)
        elif input_id == "gripper_cmd":
            payload = np.asarray(
                event["value"].to_numpy(zero_copy_only=False), dtype=np.float32
            ).reshape(-1)
            if payload.shape[0] != 1 or not 0.0 <= float(payload[0]) <= 1.0:
                raise ValueError(
                    f"gripper_cmd must be Float32[1] in [0, 1], got {payload!r} (TC table)"
                )
            commands.push("gripper", metadata.get("env_id"), payload)
        elif input_id == "base_cmd":
            # MOB-1: latest diff-drive command [v, omega]; integrated each
            # tick (the guard has already clamped it, MOB-3)
            payload = np.asarray(
                event["value"].to_numpy(zero_copy_only=False), dtype=np.float32
            ).reshape(-1)
            if payload.shape[0] != 2:
                raise ValueError(f"base_cmd must be Float32[2] (v, omega), got {payload!r} (MOB-1)")
            base_cmd = [float(payload[0]), float(payload[1])]
        elif input_id == "reset":
            started = clock()
            payload = np.asarray(event["value"].to_numpy(zero_copy_only=False)).reshape(-1)
            if payload.shape[0] != 2:
                raise ValueError(f"reset payload must be UInt32[2], got {payload.shape} (TC-6)")
            reset_seed, mode = int(payload[0]), int(payload[1])
            if mode not in (0, 1):
                raise ValueError(f"reset mode must be 0 or 1, got {mode} (TC-6)")
            if not metadata.get("request_id"):
                raise ValueError("reset request missing request_id metadata (TC-6)")
            # TC-6: no observation may interleave reset -> reset_done; the
            # loop is single-threaded, so replying before returning to the
            # event loop guarantees ordering
            if mode == 1:
                raise NotImplementedError("behavioral reset lands with SPEC 040 (T06)")
            # fleet mode (BRG-5): a reset carrying env_id teleports only
            # that env's slice; the shared publish grid is NOT re-anchored
            # (it belongs to every env — fleet cadence is anchored to the
            # first whole-scene reset, a documented fleet-mode difference
            # from ADR-25's per-episode anchoring)
            reset_env = metadata.get("env_id") if cfg.n_envs > 1 else None
            if reset_env is not None:
                if isinstance(reset_env, bool) or not isinstance(reset_env, int):
                    raise ValueError(f"reset env_id must be an int, got {reset_env!r} (BRG-5)")
                if not 0 <= reset_env < cfg.n_envs:
                    raise ValueError(f"reset env_id {reset_env} outside [0, {cfg.n_envs}) (BRG-5)")
            teleport_reset(reset_seed, reset_env)
            awaiting_first_reset = False
            if reset_env is None:
                # CON-5/ADR-25: re-anchor the publish-cadence grid to the
                # reset, so which ticks fire the sub-100 Hz topics (poses,
                # oracle_state, base_pose...) is a function of the episode,
                # not of the wall tick the request happened to land on
                scheduler = RateScheduler(topic_rates, dt)
            if so101_latch is not None:
                if so101_latch.held_name is not None:
                    print(
                        f"so101 carry release: {so101_latch.held_name} (reset)",
                        file=sys.stderr,
                    )
                so101_latch.reset()
            if is_mobile:
                # MOB-1/ADR-13: re-home the base to the store-frame start and
                # drop the in-flight base command (mirrors the arm re-home).
                # The robot ROOT moves too (PR #21): the tick handler re-bases
                # only when the integrated pose CHANGES, so a variable-only
                # re-home would leave the physical base at the pre-reset pose
                base_pose = [float(v) for v in profile.get("base_start", [0.0, 0.0, 0.0])]
                base_cmd = [0.0, 0.0]
                half = base_pose[2] / 2
                robot.set_pos(np.array([base_pose[0], base_pose[1], 0.0], dtype=np.float32))
                robot.set_quat(np.array([np.cos(half), 0.0, 0.0, np.sin(half)], dtype=np.float32))
                if held_item is not None:
                    # a mid-carry reset hands the item back to physics: the
                    # latch would otherwise pin the respawned item to the hand
                    print(f"carry release: {held_item} (reset)", file=sys.stderr)
                    held_item = None
            reply_env = reset_env if reset_env is not None else 0
            node.send_output(
                "reset_done",
                pa.array(np.array([1], dtype=np.uint32)),
                metadata=_metadata(
                    sim_time_ns,
                    reply_env,
                    seq.update(
                        {("reset_done", reply_env): seq.get(("reset_done", reply_env), 0) + 1}
                    )
                    or seq[("reset_done", reply_env)],
                    request_id=metadata.get("request_id", ""),
                    seed=reset_seed,
                    mode=mode,
                    t_reset_ms=int((clock() - started) * 1000),
                ),
            )
            # the injected state IS the post-reset observation: snapshot it
            # before any physics step so the first oracle_state after reset
            # is a pure function of the seed (TC-A2, CON-5); reset_done was
            # already sent, so nothing interleaves the service pair (TC-6)
            for topic in reset_publish_topics(topic_rates):
                publish(topic)


if __name__ == "__main__":
    main()
