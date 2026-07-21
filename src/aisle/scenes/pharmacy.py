"""Pharmacy scene builder (SPEC 020).

`build_scene` is a pure function of its arguments (SCN-1, CON-5): all
randomness flows from the injected seed through explicit `random.Random`
instances (genesis's own RNG is pinned and never relied on), every physical
constant lives in meds.toml / physics.toml (SCN-2), and genesis is imported
lazily so unit tests and the validator never pay for sim dependencies.
An embodiment is a scene+driver profile swap (M0-5): shelf/tray placement
and scale come from the per-embodiment layout sections in physics.toml.
"""

from __future__ import annotations

import math
import platform
import random
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

_SCENES_DIR = Path(__file__).parent
_REPO_ROOT = Path(__file__).resolve().parents[3]
SO101_URDF = _REPO_ROOT / "assets" / "so101" / "so101.urdf"
FRANKA_MJCF = "xml/franka_emika_panda/panda.xml"
FRANKA_EE_LINK = "hand"
# genesis quaternions are (w, x, y, z); gripper pointing straight down
DOWNWARD_QUAT = (0.0, 1.0, 0.0, 0.0)

_MAX_PLACEMENT_TRIES = 1000


def load_meds() -> dict:
    with open(_SCENES_DIR / "meds.toml", "rb") as f:
        return tomllib.load(f)


def load_physics() -> dict:
    with open(_SCENES_DIR / "physics.toml", "rb") as f:
        return tomllib.load(f)


def resolve_layout(physics: dict, embodiment: str) -> dict:
    """Merge shared geometry with the embodiment's layout profile: shelf
    position/levels/size, tray position/size, reach, and the ik section."""
    profiles = physics["embodiment"]
    if embodiment not in profiles:
        raise ValueError(
            f"unknown embodiment {embodiment!r}; add [embodiment.{embodiment}] to physics.toml"
        )
    profile = profiles[embodiment]
    return {
        "shelf": {
            **physics["shelf"],
            "pos": profile["shelf_pos"],
            "level_heights": profile["shelf_level_heights"],
            "level_depths": profile["shelf_level_depths"],
            "level_size": profile["shelf_level_size"],
            # per-embodiment override (finger-sweep clearance scales with
            # the gripper): same pattern as pregrasp_height_m
            **(
                {"min_separation": profile["min_separation"]} if "min_separation" in profile else {}
            ),
        },
        "tray": {
            **physics["tray"],
            "pos": profile["tray_pos"],
            "size": profile["tray_size"],
        },
        "reach_m": profile["reach_m"],
        "ik": {
            **physics["ik"],
            **(
                {"pregrasp_height_m": profile["pregrasp_height_m"]}
                if "pregrasp_height_m" in profile
                else {}
            ),
        },
    }


MED_NAMES = list(load_meds())


@dataclass(frozen=True)
class DRToggle:
    """One domain-randomization axis: off by default, independently seeded
    (SCN-6)."""

    enabled: bool = False
    seed: int = 0


@dataclass(frozen=True)
class SceneCfg:
    # SCN-3's build-time reachability assert is unconditional by spec —
    # deliberately NOT a toggle here
    lighting: DRToggle = field(default_factory=DRToggle)
    textures: DRToggle = field(default_factory=DRToggle)
    friction_jitter: DRToggle = field(default_factory=DRToggle)
    camera_jitter: DRToggle = field(default_factory=DRToggle)


@dataclass(frozen=True)
class Placement:
    name: str
    level: int
    x: float
    y: float
    z: float


@dataclass
class SceneHandle:
    scene: Any
    robot: Any
    boxes: dict[str, Any]
    tray: Any
    cams: dict[str, Any]
    embodiment: str
    seed: int
    med_sizes: dict[str, list[float]]
    dr_applied: dict[str, Any] = field(default_factory=dict)
    reachability_errors: list[str] = field(default_factory=list)


# how much of the open band the hand column must keep clear of a higher
# board's FRONT plane: hand half-extent (~0.045 incl. the wrist-cam mount)
# plus tracking transient (~0.05 measured in the T10 physics replay — the
# hand landed ON the board's front edge at the band's rear limit)
HAND_CLEARANCE_M = 0.10
# vertical room the hand column needs above the grasp line for a top-down
# descent (fingers + hand + wrist; measured in the T08 live runs) — kept
# beside HAND_CLEARANCE_M so the hand-geometry constants share one home
# (both franka-measured, conservative for so101; revisit per-embodiment
# when so101 support lands)
HAND_COLUMN_M = 0.35


def level_x_span(shelf: dict, level: int) -> tuple[float, float]:
    """A level board's x-span. Boards are REAR-ALIGNED within the shelf
    footprint (staggered shelving, ADR-12) — the single source of truth
    for that convention, shared by the sampler, the scene builder, and
    the grasp planner's needs_front safety net."""
    rear_x = shelf["pos"][0] + shelf["level_size"][0] / 2
    return rear_x - shelf["level_depths"][level], rear_x


def open_band(shelf: dict, level: int) -> tuple[float, float]:
    """The level's x-band with OPEN SKY: its board span, ending
    HAND_CLEARANCE_M before any higher (shallower, rear-aligned) board's
    front plane — top-down grasps need the hand column clear (ADR-12)."""
    x_min, x_max = level_x_span(shelf, level)
    for higher in range(level + 1, len(shelf["level_depths"])):
        x_max = min(x_max, level_x_span(shelf, higher)[0] - HAND_CLEARANCE_M)
    return x_min, x_max


def sample_placements(seed: int, med_names: list[str], layout: dict) -> list[Placement]:
    """Rejection-sample per-seed box placements on the shelf levels
    (SCN-3): inside the level bounds minus edge margins, per-axis AABB
    separation of min_separation, and a geometric reach pre-filter so the
    IK backstop cannot abort the build on corner placements. Pure function
    of the seed."""
    rng = random.Random(seed)
    shelf = layout["shelf"]
    ik = layout["ik"]
    max_target = layout["reach_m"] * ik["reach_margin_frac"]
    meds = load_meds()
    width = shelf["level_size"][1]

    # levels whose nearest-point candidates can never pass the reach filter
    # (e.g. so101's top level) are excluded up front, not burned as tries
    tallest = max(spec["size"][2] for spec in meds.values())
    usable_levels = [
        lvl
        for lvl, height in enumerate(shelf["level_heights"])
        if math.hypot(
            abs(open_band(shelf, lvl)[0]) + shelf["edge_margin"],
            0.0,
            shelf["pos"][2]
            + height
            + shelf["board_thickness"] / 2
            + tallest / 2
            + ik["pregrasp_height_m"],
        )
        <= max_target
    ]
    if not usable_levels:
        raise AssertionError("no shelf level is inside the reach envelope (check layout profile)")
    placed: list[Placement] = []
    for name in med_names:
        size = meds[name]["size"]
        half_x, half_y = size[0] / 2, size[1] / 2
        for _ in range(_MAX_PLACEMENT_TRIES):
            level = usable_levels[rng.randrange(len(usable_levels))]
            band_min, band_max = open_band(shelf, level)
            x_lo = band_min - shelf["pos"][0] + shelf["edge_margin"] + half_x
            x_hi = band_max - shelf["pos"][0] - shelf["edge_margin"] - half_x
            if x_hi < x_lo:
                continue  # this med cannot fit the level's open band
            local_x = rng.uniform(x_lo, x_hi)
            local_y = rng.uniform(
                -width / 2 + shelf["edge_margin"] + half_y,
                width / 2 - shelf["edge_margin"] - half_y,
            )
            candidate = Placement(
                name=name,
                level=level,
                x=shelf["pos"][0] + local_x,
                y=shelf["pos"][1] + local_y,
                z=shelf["pos"][2]
                + shelf["level_heights"][level]
                + shelf["board_thickness"] / 2
                + size[2] / 2,
            )
            pregrasp_distance = math.hypot(
                candidate.x, candidate.y, candidate.z + ik["pregrasp_height_m"]
            )
            if pregrasp_distance > max_target:
                continue
            if _separated(candidate, half_x, half_y, placed, meds, shelf["min_separation"]):
                placed.append(candidate)
                break
        else:
            raise AssertionError(f"could not place {name!r} after {_MAX_PLACEMENT_TRIES} tries")
    return placed


def _separated(
    candidate: Placement,
    half_x: float,
    half_y: float,
    placed: list[Placement],
    meds: dict,
    min_separation: float,
) -> bool:
    """AABBs overlap iff BOTH axis gaps are below their half-extent sums, so
    separation requires at least one axis to clear its sum plus margin."""
    for other in placed:
        if other.level != candidate.level:
            continue
        required_x = half_x + meds[other.name]["size"][0] / 2 + min_separation
        required_y = half_y + meds[other.name]["size"][1] / 2 + min_separation
        clear_x = abs(candidate.x - other.x) >= required_x
        clear_y = abs(candidate.y - other.y) >= required_y
        if not (clear_x or clear_y):
            return False
    return True


def _ensure_genesis():
    import genesis as gs

    expected = gs.metal if platform.system() == "Darwin" else gs.cpu
    if not getattr(gs, "_initialized", False):
        # fixed seed: genesis's internal RNG must never be an input to build
        # outcomes (CON-5); reachability IK is additionally made
        # deterministic via explicit init_qpos and max_samples=1
        # performance_mode is deliberately OFF: it recompiles kernels for
        # minutes in every fresh process (measured >5 min), wrecking test
        # and node startup; substeps=1 alone keeps the step budget (ADR-7)
        gs.init(backend=expected, logging_level="warning", seed=0)
    elif gs.backend != expected:
        # a foreign pre-initialization would silently change build results
        # for identical arguments (CON-5) — refuse loudly instead
        raise RuntimeError(
            f"genesis already initialized with backend {gs.backend}; "
            f"build_scene requires {expected}"
        )
    return gs


def to_numpy(tensor) -> np.ndarray:
    if hasattr(tensor, "cpu"):
        tensor = tensor.cpu()
    return np.asarray(tensor, dtype=np.float32)


def build_scene(
    seed: int,
    embodiment: str = "franka",
    n_envs: int = 1,
    headless: bool = True,
    cfg: SceneCfg | None = None,
) -> SceneHandle:
    cfg = cfg or SceneCfg()
    gs = _ensure_genesis()
    meds = load_meds()
    physics = load_physics()
    layout = resolve_layout(physics, embodiment)
    shelf, tray_cfg = layout["shelf"], layout["tray"]
    dr_cfg = physics["domain_randomization"]

    for label, target in (("tray", tray_cfg["pos"]), ("shelf", shelf["pos"])):
        distance = math.hypot(*target)
        assert distance <= layout["reach_m"], (
            f"{label} at {target} outside {embodiment} workspace (SCN-4)"
        )

    lighting_rng = random.Random(cfg.lighting.seed)
    textures_rng = random.Random(cfg.textures.seed)
    friction_rng = random.Random(cfg.friction_jitter.seed)
    camera_rng = random.Random(cfg.camera_jitter.seed)

    ambient = (dr_cfg["ambient_default"],) * 3
    if cfg.lighting.enabled:
        ambient = tuple(
            min(1.0, dr_cfg["ambient_min"] + lighting_rng.random() * dr_cfg["ambient_range"])
            for _ in range(3)
        )

    scene = gs.Scene(
        sim_options=gs.options.SimOptions(
            dt=physics["sim"]["dt"],
            substeps=physics["sim"]["substeps"],
            gravity=tuple(physics["sim"]["gravity"]),
        ),
        # keep ALL self-collision pairs: genesis filters pairs that collide
        # at the (invalid, all-zeros) neutral pose during build, which would
        # permanently disable those checks; we move to home_qpos before any
        # step, so the transient neutral contacts never simulate
        rigid_options=gs.options.RigidOptions(enable_neutral_collision=True),
        vis_options=gs.options.VisOptions(ambient_light=ambient),
        renderer=gs.renderers.Rasterizer(),  # SCN-5: Metal-safe default path
        show_viewer=not headless,
    )
    scene.add_entity(gs.morphs.Plane())

    shelf_material = gs.materials.Rigid(friction=physics["materials"]["shelf"]["friction"])
    width = shelf["level_size"][1]
    for level, (level_height, level_depth) in enumerate(
        zip(shelf["level_heights"], shelf["level_depths"], strict=True)
    ):
        # boards are REAR-ALIGNED (level_x_span): upper (shallower) boards
        # leave the lower level's front band open to the sky
        x_min, x_max = level_x_span(shelf, level)
        scene.add_entity(
            gs.morphs.Box(
                size=(level_depth, width, shelf["board_thickness"]),
                pos=((x_min + x_max) / 2, shelf["pos"][1], shelf["pos"][2] + level_height),
                fixed=True,
            ),
            material=shelf_material,
        )

    tray_material = gs.materials.Rigid(friction=physics["materials"]["tray"]["friction"])
    tray = scene.add_entity(
        gs.morphs.Box(size=tuple(tray_cfg["size"]), pos=tuple(tray_cfg["pos"]), fixed=True),
        material=tray_material,
    )

    if embodiment == "franka":
        robot = scene.add_entity(gs.morphs.MJCF(file=FRANKA_MJCF))
    else:
        if not SO101_URDF.exists():
            raise FileNotFoundError(
                f"so101 asset missing: {SO101_URDF} (acquisition pending, ADR-6)"
            )
        robot = scene.add_entity(gs.morphs.URDF(file=str(SO101_URDF), fixed=True))

    box_physics = physics["materials"]["box"]
    applied_frictions: dict[str, float] = {}
    applied_colors: dict[str, list[float]] = {}
    boxes: dict[str, Any] = {}
    for placement in sample_placements(seed, list(meds), layout):
        friction = box_physics["friction"]
        if cfg.friction_jitter.enabled:
            friction *= 1.0 + (friction_rng.random() - 0.5) * dr_cfg["friction_jitter_frac"]
        applied_frictions[placement.name] = friction
        color = list(meds[placement.name]["color"])
        if cfg.textures.enabled:
            scale_min, scale_range = dr_cfg["texture_scale_min"], dr_cfg["texture_scale_range"]
            color = [
                min(1.0, c * (scale_min + textures_rng.random() * scale_range)) for c in color[:3]
            ] + [color[3]]
        applied_colors[placement.name] = color
        boxes[placement.name] = scene.add_entity(
            gs.morphs.Box(
                size=tuple(meds[placement.name]["size"]),
                pos=(placement.x, placement.y, placement.z),
            ),
            material=gs.materials.Rigid(friction=friction, rho=box_physics["density_kg_m3"]),
            surface=gs.surfaces.Default(color=tuple(color)),
        )

    cam_cfg = physics["cameras"]
    overhead_pos = list(cam_cfg["overhead_pos"])
    if cfg.camera_jitter.enabled:
        jitter = dr_cfg["camera_jitter_m"]
        overhead_pos = [p + (camera_rng.random() - 0.5) * jitter for p in overhead_pos]
    cams = {
        "overhead": scene.add_camera(
            res=(640, 480),
            pos=tuple(overhead_pos),
            lookat=tuple(cam_cfg["overhead_lookat"]),
            fov=55,
            GUI=False,
        ),
        "wrist": scene.add_camera(res=(320, 240), fov=70, GUI=False),
    }

    if n_envs == 1:
        scene.build()
    else:
        scene.build(n_envs=n_envs)

    # start the robot AT its home pose: the qpos0 zeros pose violates franka
    # joint limits and self-collides (T05 control would inherit that state)
    profile = physics["embodiment"][embodiment]
    if "home_qpos" in profile:
        home = np.asarray(profile["home_qpos"], dtype=np.float32)
        robot.set_qpos(home if n_envs == 1 else np.tile(home, (n_envs, 1)))
    # finger-dof gains: without these the tendon-approximated gripper
    # actuator ignores position control and the fingers fall closed
    if "gripper_dofs" in profile and "gripper_kp" in profile:
        # gripper_dofs is a COUNT; the finger dofs are the last N
        count = int(profile["gripper_dofs"])
        finger_dofs = list(range(robot.n_dofs - count, robot.n_dofs))
        robot.set_dofs_kp(
            np.asarray(profile["gripper_kp"], dtype=np.float32), dofs_idx_local=finger_dofs
        )
        robot.set_dofs_kv(
            np.asarray(profile["gripper_kv"], dtype=np.float32), dofs_idx_local=finger_dofs
        )

    ee_link = robot.get_link(FRANKA_EE_LINK) if embodiment == "franka" else robot.links[-1]
    offset = np.eye(4, dtype=np.float32)
    offset[:3, 3] = cam_cfg["wrist_offset_m"]
    cams["wrist"].attach(ee_link, offset_T=offset)

    handle = SceneHandle(
        scene=scene,
        robot=robot,
        boxes=boxes,
        tray=tray,
        cams=cams,
        embodiment=embodiment,
        seed=seed,
        med_sizes={name: list(meds[name]["size"]) for name in meds},
        dr_applied={
            "ambient": ambient,
            "overhead_pos": overhead_pos,
            "frictions": applied_frictions,
            "colors": applied_colors,
        },
    )

    # SCN-3: asserted at build time, unconditionally; placements are seed-
    # identical across batched envs, so env 0 witnesses reachability for all
    _assert_reachable(handle, ee_link, layout["ik"], n_envs)
    return handle


def _assert_reachable(handle: SceneHandle, ee_link, ik_cfg: dict, n_envs: int = 1) -> None:
    """SCN-3: every box placement must admit an IK solution to its pre-grasp
    pose. Deterministic multi-start (CON-5): explicit seeded init_qpos
    perturbations with max_samples=1, so genesis's global RNG never
    influences the outcome; position AND rotation error are both checked."""
    rng = random.Random(handle.seed)
    profile = load_physics()["embodiment"][handle.embodiment]
    if "home_qpos" in profile:
        home = np.asarray(profile["home_qpos"], dtype=np.float32)
    else:
        home = to_numpy(handle.robot.get_qpos()).reshape(-1)[: handle.robot.n_dofs]
    failures: list[str] = []
    for name, entity in handle.boxes.items():
        target = to_numpy(entity.get_pos()).reshape(-1)[:3] + np.array(
            [0.0, 0.0, ik_cfg["pregrasp_height_m"]], dtype=np.float32
        )
        best = None
        for attempt in range(ik_cfg["max_starts"]):
            if attempt == 0:
                init_qpos = home
            else:
                perturbation = np.array(
                    [
                        (rng.random() - 0.5) * 2 * ik_cfg["init_perturbation_rad"]
                        for _ in range(home.shape[0])
                    ],
                    dtype=np.float32,
                )
                init_qpos = home + perturbation
            if n_envs > 1:  # genesis requires batch-shaped inputs
                pos_arg = np.tile(target, (n_envs, 1))
                quat_arg = np.tile(np.asarray(DOWNWARD_QUAT, dtype=np.float32), (n_envs, 1))
                init_arg = np.tile(init_qpos, (n_envs, 1))
            else:
                pos_arg, quat_arg, init_arg = target, DOWNWARD_QUAT, init_qpos
            _, error = handle.robot.inverse_kinematics(
                link=ee_link,
                pos=pos_arg,
                quat=quat_arg,
                init_qpos=init_arg,
                max_samples=1,
                max_solver_iters=ik_cfg["max_solver_iters"],
                return_error=True,
            )
            # env 0 witnesses all envs: placements are seed-identical
            error = to_numpy(error).reshape(-1)[:6]
            pos_error = float(np.linalg.norm(error[:3]))
            rot_error = float(np.linalg.norm(error[3:6]))
            best = min(best or (pos_error, rot_error), (pos_error, rot_error))
            if pos_error <= ik_cfg["pos_tol_m"] and rot_error <= ik_cfg["rot_tol_rad"]:
                break
        else:
            failures.append(f"{name}: best ik error pos {best[0]:.4f} m rot {best[1]:.4f} rad")
    handle.reachability_errors = failures
    assert not failures, f"unreachable placements (SCN-3): {failures}"


def oracle_state(handle: SceneHandle) -> np.ndarray:
    """Ground-truth state: per box (meds.toml order) position (3) then
    quaternion in TC-1 (x, y, z, w) order — genesis returns (w, x, y, z),
    reordered here so the wire format matches the topic contract. Shape
    (n_obj*7,) for a single env, (n_envs, n_obj*7) for batched builds."""
    parts = []
    for entity in handle.boxes.values():
        pos = np.atleast_2d(to_numpy(entity.get_pos()))
        quat_wxyz = np.atleast_2d(to_numpy(entity.get_quat()))
        quat_xyzw = np.roll(quat_wxyz, -1, axis=-1)
        parts.extend((pos, quat_xyzw))
    state = np.concatenate(parts, axis=-1).astype(np.float32)
    return state[0] if state.shape[0] == 1 else state
