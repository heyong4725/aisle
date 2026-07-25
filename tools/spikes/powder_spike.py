#!/usr/bin/env python3
"""T20 powder spike (SPEC 300 PW-0, Class A): measure whether Genesis
particle solvers on THIS machine can carry the powder family.

(a) steps/sec for a powder-in-vessel scene, N in {5k, 20k, 50k}, solver in
    {MPM sand, PBD particles, SPH liquid} — SPH is a liquid model, included
    as the "as available" data point PW-0 names, not a powder candidate;
(b) a scripted scoop with a simple spatula (lipped blade, kinematically
    driven with TRUE velocity), seeded repetitions: transferred-mass
    mean/std and identity-tracked spill accounting;
(c) pile/pour sanity: a column slump AND a true pour from a tilting
    vessel — a pile must form, nothing may explode;
(d) determinism: the same seed run twice per backend, compared by particle
    -state digest (CON-5: aggregate statistics are only sound on a backend
    where same seed => same result; divergence is REPORTED and the scoop
    stats are computed per backend).

All dynamic cases run at dt 1 ms / substeps 4: substep_dt 0.25 ms sits
under genesis's suggested stability bound (0.3125 ms at default grid
density) — the earlier 0.5 ms substep drew an instability warning, and
dt 2 ms / substeps 1 explodes a plain settle on BOTH metal and cpu.

Outputs runs/spike-powder/results.json + PNG plots. Numbers feed
docs/decisions/ADR-powder-spike.md (DRAFT; go/no-go is human, PW-0).

Isolation: every case runs in its OWN subprocess (`--case ...`) — genesis
initializes once per process, and one failure must not kill the sweep.
The sweep exits 0 IFF every case succeeded (CON-8); partial results are
reported with ok=false and the failed case list.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import platform
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = REPO_ROOT / "runs" / "spike-powder"

# one shared particle size: counts are then set by block edge length.
# 4 mm is coarse for real powder but the honest spike scale (PW-4: sim
# validates control strategy, not milligram fidelity)
PARTICLE_SIZE = 0.004
RHO = 1500.0  # kg/m^3, loose-powder-ish bulk density
PARTICLE_MASS_KG = RHO * PARTICLE_SIZE**3  # regular-sampler cell mass proxy

DT = 1e-3
SUBSTEPS = 4  # substep_dt 0.25 ms < genesis's suggested 0.3125 ms bound

BENCH_COUNTS = (5_000, 20_000, 50_000)
BENCH_SOLVERS = ("mpm", "pbd", "sph")
WARMUP_STEPS = 20
TIMED_STEPS = 100
SCOOP_REPS = 20
# out-of-vessel particles ABOVE this height at measurement time are
# "airborne_or_on_tool" — reported as their own bucket, never silently
# dropped from the accounting
TOOL_ZONE_Z = 0.2


def _edge_for(n_target: int) -> float:
    """Cube edge that the regular sampler fills with ~n_target particles."""
    return round(PARTICLE_SIZE * round(n_target ** (1 / 3)), 4)


def _init_genesis(backend: str):
    import genesis as gs

    gs.init(backend=getattr(gs, backend), logging_level="warning", seed=0)
    return gs


def _sim_options(gs):
    return gs.options.SimOptions(dt=DT, substeps=SUBSTEPS)


def _vessel(gs, scene, center, inner=0.16, wall_h=0.14, wall_t=0.01):
    """An open-top FIXED vessel from five boxes; returns its interior AABB."""
    cx, cy = center
    half = inner / 2 + wall_t
    scene.add_entity(
        gs.morphs.Box(
            size=(inner + 2 * wall_t, inner + 2 * wall_t, wall_t),
            pos=(cx, cy, wall_t / 2),
            fixed=True,
        )
    )
    for dx, dy, sx, sy in (
        (half, 0, wall_t, inner + 2 * wall_t),
        (-half, 0, wall_t, inner + 2 * wall_t),
        (0, half, inner + 2 * wall_t, wall_t),
        (0, -half, inner + 2 * wall_t, wall_t),
    ):
        scene.add_entity(
            gs.morphs.Box(
                size=(sx, sy, wall_h), pos=(cx + dx, cy + dy, wall_t + wall_h / 2), fixed=True
            )
        )
    lo = (cx - inner / 2, cy - inner / 2, wall_t)
    hi = (cx + inner / 2, cy + inner / 2, wall_t + wall_h)
    return lo, hi


def _powder_material(gs, solver: str, friction_angle: float | None = None):
    if solver == "mpm":
        kwargs = {"rho": RHO}
        if friction_angle is not None:
            kwargs["friction_angle"] = friction_angle
        return gs.materials.MPM.Sand(**kwargs)
    if solver == "pbd":
        return gs.materials.PBD.Particle(rho=RHO)
    if solver == "sph":
        return gs.materials.SPH.Liquid(rho=RHO)
    raise ValueError(f"unknown solver {solver!r}")


def _solver_options(gs, solver: str, cpic: bool = False):
    # MPM pads the domain inward (~5%): the physical floor (z=0) must sit
    # comfortably INSIDE the padded boundary, so the domain floor goes lower
    bounds = {"lower_bound": (-0.6, -0.6, -0.2), "upper_bound": (0.6, 0.6, 0.8)}
    if solver == "mpm":
        return {
            "mpm_options": gs.options.MPMOptions(
                dt=DT, particle_size=PARTICLE_SIZE, enable_CPIC=cpic, **bounds
            )
        }
    if solver == "pbd":
        return {"pbd_options": gs.options.PBDOptions(dt=DT, particle_size=PARTICLE_SIZE, **bounds)}
    return {"sph_options": gs.options.SPHOptions(dt=DT, particle_size=PARTICLE_SIZE, **bounds)}


def _np(x):
    """Torch (incl. MPS) tensor or array -> numpy on host."""
    import numpy as np

    if hasattr(x, "cpu"):
        x = x.cpu().numpy()
    return np.asarray(x)


def _drive(entity, pos, quat, vel=None):
    """Kinematic drive with a TRUE velocity: the MPM coupler reads the
    rigid body's velocity for momentum exchange — a teleported tool with
    zeroed velocity neither drags nor carries particles (measured: a
    swept blade came up empty)."""
    import numpy as np

    entity.set_pos(np.asarray(pos, dtype=np.float32))
    entity.set_quat(np.asarray(quat, dtype=np.float32))
    if vel is not None and hasattr(entity, "set_dofs_velocity"):
        entity.set_dofs_velocity(np.asarray([*vel, 0.0, 0.0, 0.0], dtype=np.float32))
    else:
        entity.zero_all_dofs_velocity()


def _yaw_pitch_quat(pitch: float) -> tuple:
    """w-x-y-z quat for a rotation of `pitch` about the y axis."""
    return (math.cos(pitch / 2), 0.0, math.sin(pitch / 2), 0.0)


def _in_aabb(pos, lo, hi):
    import numpy as np

    return np.all((pos >= np.asarray(lo)) & (pos <= np.asarray(hi)), axis=1)


def _state_digest(pos) -> str:
    """Order-preserving digest of particle positions at 0.1 mm resolution —
    the determinism comparator (CON-5)."""
    import numpy as np

    q = np.round(np.asarray(pos, dtype=np.float64) * 1e4).astype(np.int64)
    return hashlib.sha256(q.tobytes()).hexdigest()


def case_bench(backend: str, solver: str, n_target: int) -> dict:
    """(a) steps/sec for a powder block settling inside a vessel, in the
    stability-bounded regime (module header)."""
    gs = _init_genesis(backend)
    scene = gs.Scene(sim_options=_sim_options(gs), **_solver_options(gs, solver), show_viewer=False)
    scene.add_entity(gs.morphs.Plane())
    _vessel(gs, scene, (0.0, 0.0))
    edge = _edge_for(n_target)
    powder = scene.add_entity(
        material=_powder_material(gs, solver),
        morph=gs.morphs.Box(size=(edge, edge, edge), pos=(0.0, 0.0, 0.02 + edge / 2)),
    )
    t0 = time.perf_counter()
    scene.build()
    build_s = time.perf_counter() - t0
    for _ in range(WARMUP_STEPS):
        scene.step()
    t0 = time.perf_counter()
    for _ in range(TIMED_STEPS):
        scene.step()
    elapsed = time.perf_counter() - t0
    return {
        "case": "bench",
        "solver": solver,
        "n_target": n_target,
        "n_particles": int(getattr(powder, "n_particles", -1)),
        "dt": DT,
        "substeps": SUBSTEPS,
        "build_s": round(build_s, 2),
        "steps_per_sec": round(TIMED_STEPS / elapsed, 2),
        "sim_seconds_per_wall_second": round(TIMED_STEPS * DT / elapsed, 4),
    }


def case_scoop(backend: str, seed: int) -> dict:
    """(b) one seeded scripted scoop: source vessel -> lipped spatula ->
    receiver, with IDENTITY-TRACKED accounting: every particle lands in
    exactly one bucket (source, receiver, spilled_new, out_at_baseline,
    airborne_or_on_tool) — nothing is silently excluded."""
    import numpy as np

    rng = __import__("random").Random(seed)
    gs = _init_genesis(backend)
    scene = gs.Scene(
        sim_options=_sim_options(gs),
        # CPIC is REQUIRED here (measured): without it the MPM-rigid
        # coupling ejects the pile against the thin vessel walls during
        # plain settling
        **_solver_options(gs, "mpm", cpic=True),
        show_viewer=False,
    )
    scene.add_entity(gs.morphs.Plane())
    src_lo, src_hi = _vessel(gs, scene, (-0.15, 0.0))
    rcv_lo, rcv_hi = _vessel(gs, scene, (0.15, 0.0))
    edge = _edge_for(5_000)
    jx, jy = rng.uniform(-0.005, 0.005), rng.uniform(-0.005, 0.005)
    powder = scene.add_entity(
        material=_powder_material(gs, "mpm"),
        morph=gs.morphs.Box(size=(edge, edge, edge), pos=(-0.15 + jx, jy, 0.02 + edge / 2)),
    )
    # "simple spatula": a blade with a back wall and side rails — a flat
    # blade retains nothing through the lift (measured). Parts are separate
    # rigid boxes driven in LOCKSTEP with pitch-rotated offsets.
    rigid = gs.materials.Rigid(friction=0.8)
    tool = []
    for size, off in (
        ((0.05, 0.09, 0.004), (0.0, 0.0, 0.0)),  # blade
        ((0.006, 0.09, 0.024), (-0.028, 0.0, 0.012)),  # back wall
        ((0.05, 0.006, 0.024), (0.0, 0.048, 0.012)),  # left rail
        ((0.05, 0.006, 0.024), (0.0, -0.048, 0.012)),  # right rail
    ):
        part = scene.add_entity(
            gs.morphs.Box(size=size, pos=(-0.35, 0.4, 0.5 + off[2])), material=rigid
        )
        tool.append((part, off))
    scene.build()
    for _ in range(150):  # settle the pile
        scene.step()
    pos0 = _np(powder.get_particles_pos()).reshape(-1, 3)
    out_at_baseline = ~_in_aabb(pos0, src_lo, src_hi)  # settle splash, by IDENTITY

    # the settled pile is SHALLOW (~1.2 cm over the vessel floor at z=0.01):
    # the blade must skim the floor to gather anything
    z_dip = 0.016 + rng.uniform(-0.001, 0.001)
    x0, x1 = -0.20, -0.12  # sweep across the source vessel, clear of the wall
    x_carry, z_carry = 0.15, 0.25
    flat = 0.0
    nose_down = math.radians(-4.0)  # leading edge digs
    cup = math.radians(12.0)  # slight tilt back to hold the load

    def seg(p_from, p_to, pitch_from, pitch_to, steps):
        vel = [(p_to[i] - p_from[i]) / (steps * DT) for i in range(3)]
        for k in range(steps):
            a = (k + 1) / steps
            pos = [p_from[i] + a * (p_to[i] - p_from[i]) for i in range(3)]
            pitch = pitch_from + a * (pitch_to - pitch_from)
            quat = _yaw_pitch_quat(pitch)
            c, sn = math.cos(pitch), math.sin(pitch)
            for part, (dx, dy, dz) in tool:
                rot = (dx * c + dz * sn, dy, -dx * sn + dz * c)
                _drive(part, [pos[0] + rot[0], pos[1] + rot[1], pos[2] + rot[2]], quat, vel)
            scene.step()

    dump = math.radians(120.0)
    # per-step displacement stays well under one particle size (~0.5 mm/step)
    seg((x0, 0.0, 0.25), (x0, 0.0, z_dip), flat, nose_down, 200)  # descend at the wall
    seg((x0, 0.0, z_dip), (x1, 0.0, z_dip), nose_down, nose_down, 240)  # sweep/gather
    seg((x1, 0.0, z_dip), (x1, 0.0, 0.08), nose_down, flat, 160)  # straight out
    seg((x1, 0.0, 0.08), (x1, 0.0, z_carry), flat, cup, 160)  # then cup
    seg((x1, 0.0, z_carry), (x_carry, 0.0, z_carry), cup, cup, 240)  # carry
    seg((x_carry, 0.0, z_carry), (x_carry, 0.0, z_carry), cup, dump, 200)  # tilt/dump
    for _ in range(250):  # let everything land and settle
        scene.step()

    pos = _np(powder.get_particles_pos()).reshape(-1, 3)
    vel = _np(powder.get_particles_vel()).reshape(-1, 3)
    n = pos.shape[0]
    in_src = _in_aabb(pos, src_lo, src_hi)
    in_rcv = _in_aabb(pos, rcv_lo, rcv_hi)
    out_both = ~in_src & ~in_rcv
    airborne = out_both & (pos[:, 2] >= TOOL_ZONE_Z)
    grounded_out = out_both & (pos[:, 2] < TOOL_ZONE_Z)
    spilled_new = grounded_out & ~out_at_baseline
    spilled_pre = grounded_out & out_at_baseline
    buckets = {
        "in_source": int(in_src.sum()),
        "in_receiver": int(in_rcv.sum()),
        "spilled_new": int(spilled_new.sum()),
        "out_at_baseline": int(spilled_pre.sum()),
        "airborne_or_on_tool": int(airborne.sum()),
    }
    total = sum(buckets.values())
    if total != n:  # a particle escaped the partition: report, never hide
        buckets["unaccounted"] = n - total
    return {
        "case": "scoop",
        "backend": backend,
        "seed": seed,
        "n_particles": n,
        **buckets,
        "transferred_mass_g": round(buckets["in_receiver"] * PARTICLE_MASS_KG * 1000, 3),
        "spilled_mass_g": round(buckets["spilled_new"] * PARTICLE_MASS_KG * 1000, 3),
        "max_speed_end": round(float(np.linalg.norm(vel, axis=1).max()), 3),
        "state_digest": _state_digest(pos),
    }


def _pile_metrics(pos, vel):
    import numpy as np

    center = pos[:, :2].mean(axis=0)
    r = np.linalg.norm(pos[:, :2] - center, axis=1)
    z = pos[:, 2]
    peak_h = float(np.percentile(z, 98))
    base_r = float(np.percentile(r, 95))
    bins = np.linspace(0, base_r, 12)
    idx = np.digitize(r, bins)
    surf = [(bins[i - 1], z[idx == i].max()) for i in range(1, len(bins)) if (idx == i).any()]
    rs, zs = np.array([s[0] for s in surf]), np.array([s[1] for s in surf])
    flank = rs > base_r * 0.3
    slope = float(np.polyfit(rs[flank], zs[flank], 1)[0]) if flank.sum() >= 3 else float("nan")
    angle_fit = math.degrees(math.atan(abs(slope))) if not math.isnan(slope) else float("nan")
    max_speed = float(np.linalg.norm(vel, axis=1).max())
    return {
        "peak_height_m": round(peak_h, 4),
        "base_radius_m": round(base_r, 4),
        "angle_simple_deg": round(math.degrees(math.atan(peak_h / base_r)), 1),
        "angle_fit_deg": round(angle_fit, 1),
        "max_speed_end": round(max_speed, 3),
        "stable": bool(max_speed < 0.05),
        "surface_profile": [[round(float(a), 4), round(float(b), 4)] for a, b in surf],
    }


def case_slump(backend: str, friction_angle: float | None = None) -> dict:
    """(c1) column slump: release a low column and let it spread — the
    frictional lower bound on pile formation (no pour dynamics)."""
    gs = _init_genesis(backend)
    scene = gs.Scene(sim_options=_sim_options(gs), **_solver_options(gs, "mpm"), show_viewer=False)
    scene.add_entity(gs.morphs.Plane())
    powder = scene.add_entity(
        material=_powder_material(gs, "mpm", friction_angle),
        morph=gs.morphs.Box(size=(0.09, 0.09, 0.12), pos=(0.0, 0.0, 0.075)),
    )
    scene.build()
    for _ in range(1200):
        scene.step()
    pos = _np(powder.get_particles_pos()).reshape(-1, 3)
    vel = _np(powder.get_particles_vel()).reshape(-1, 3)
    return {
        "case": "slump",
        "friction_angle": friction_angle,
        "n_particles": int(pos.shape[0]),
        **_pile_metrics(pos, vel),
    }


def case_pour(backend: str, friction_angle: float | None = None) -> dict:
    """(c2) TRUE pour (PW-0's pour half): a driven vessel holding settled
    powder tilts 130 degrees at height; the powder must STREAM over the
    lip and form a pile on the plane below, without explosion."""

    gs = _init_genesis(backend)
    scene = gs.Scene(
        sim_options=_sim_options(gs), **_solver_options(gs, "mpm", cpic=True), show_viewer=False
    )
    scene.add_entity(gs.morphs.Plane())
    # driven vessel: floor + 4 walls as lockstep rigid parts (inner 0.12)
    inner, wall_h, wall_t = 0.12, 0.10, 0.008
    rigid = gs.materials.Rigid(friction=0.6)
    hold = (0.0, 0.0, 0.20)  # vessel-floor center at pour height
    zc = wall_t / 2 + wall_h / 2
    parts = []
    for size, off in (
        ((inner + 2 * wall_t, inner + 2 * wall_t, wall_t), (0.0, 0.0, 0.0)),
        ((wall_t, inner + 2 * wall_t, wall_h), (inner / 2 + wall_t / 2, 0.0, zc)),
        ((wall_t, inner + 2 * wall_t, wall_h), (-inner / 2 - wall_t / 2, 0.0, zc)),
        ((inner + 2 * wall_t, wall_t, wall_h), (0.0, inner / 2 + wall_t / 2, zc)),
        ((inner + 2 * wall_t, wall_t, wall_h), (0.0, -inner / 2 - wall_t / 2, zc)),
    ):
        part = scene.add_entity(
            gs.morphs.Box(size=size, pos=(hold[0] + off[0], hold[1] + off[1], hold[2] + off[2])),
            material=rigid,
        )
        parts.append((part, off))
    powder = scene.add_entity(
        material=_powder_material(gs, "mpm", friction_angle),
        morph=gs.morphs.Box(size=(0.10, 0.10, 0.08), pos=(0.0, 0.0, hold[2] + wall_t + 0.05)),
    )
    scene.build()

    def drive_vessel(pitch, omega=0.0):
        quat = _yaw_pitch_quat(pitch)
        c, sn = math.cos(pitch), math.sin(pitch)
        for part, (dx, dy, dz) in parts:
            rot = (dx * c + dz * sn, dy, -dx * sn + dz * c)
            _drive(part, [hold[0] + rot[0], hold[1] + rot[1], hold[2] + rot[2]], quat)

    for _ in range(300):  # settle powder inside the held vessel
        drive_vessel(0.0)
        scene.step()
    tilt_steps = 600  # 130 degrees over 0.6 sim s
    for k in range(tilt_steps):
        drive_vessel(math.radians(130.0) * (k + 1) / tilt_steps)
        scene.step()
    for _ in range(1000):  # let the stream land and the pile settle
        drive_vessel(math.radians(130.0))
        scene.step()

    pos = _np(powder.get_particles_pos()).reshape(-1, 3)
    vel = _np(powder.get_particles_vel()).reshape(-1, 3)
    on_floor = pos[:, 2] < 0.12  # below the vessel: actually poured out
    poured_frac = float(on_floor.mean())
    metrics = (
        _pile_metrics(pos[on_floor], vel[on_floor])
        if int(on_floor.sum()) > 100
        else {"stable": False, "max_speed_end": float("nan"), "angle_fit_deg": float("nan")}
    )
    return {
        "case": "pour",
        "friction_angle": friction_angle,
        "n_particles": int(pos.shape[0]),
        "poured_fraction": round(poured_frac, 3),
        **metrics,
    }


def run_case(spec: str, backend: str) -> dict:
    kind, *rest = spec.split(":")
    if kind == "bench":
        return case_bench(backend, rest[0], int(rest[1]))
    if kind == "scoop":
        return case_scoop(backend, int(rest[0]))
    if kind == "slump":
        return case_slump(backend, float(rest[0]) if rest else None)
    if kind == "pour":
        return case_pour(backend, float(rest[0]) if rest else None)
    raise ValueError(f"unknown case {spec!r}")


def _subprocess_case(spec: str, backend: str, timeout_s: float = 2400.0) -> dict:
    try:
        proc = subprocess.run(
            [sys.executable, __file__, "--case", spec, "--backend", backend],
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
    except subprocess.TimeoutExpired:
        return {"case": spec, "backend": backend, "ok": False, "error": f"timeout {timeout_s}s"}
    if proc.returncode != 0:
        lines = (proc.stderr or proc.stdout or "").strip().splitlines()
        # prefer the actual failure over warning noise in the tail
        errors = [ln for ln in lines if any(k in ln for k in ("Error", "Exception", "nan"))]
        tail = (errors or lines)[-4:]
        return {"case": spec, "backend": backend, "ok": False, "error": " | ".join(tail)}
    # genesis prints sampler noise to stdout before our JSON: take the
    # LAST line that parses (CON-8 discipline is ours, not genesis's)
    for line in reversed(proc.stdout.splitlines()):
        if line.startswith("{"):
            return {**json.loads(line), "ok": True}
    return {"case": spec, "backend": backend, "ok": False, "error": "no JSON line in child stdout"}


def _determinism(backend: str) -> dict:
    """(d) CON-5 check: the identical seed, twice, in fresh processes."""
    a = _subprocess_case("scoop:0", backend)
    b = _subprocess_case("scoop:0", backend)
    if not (a.get("ok") and b.get("ok")):
        return {"backend": backend, "ok": False, "error": a.get("error") or b.get("error")}
    fields = ("in_source", "in_receiver", "spilled_new", "airborne_or_on_tool")
    return {
        "backend": backend,
        "ok": True,
        "digests_equal": a["state_digest"] == b["state_digest"],
        "buckets_equal": all(a[f] == b[f] for f in fields),
        "run_a": {f: a[f] for f in fields},
        "run_b": {f: b[f] for f in fields},
    }


def _scoop_stats(scoops: list[dict]) -> dict | None:
    ok = [s for s in scoops if s.get("ok")]
    if not ok:
        return None
    masses = [s["transferred_mass_g"] for s in ok]
    mean = sum(masses) / len(masses)
    std = math.sqrt(sum((m - mean) ** 2 for m in masses) / len(masses))
    return {
        "reps_ok": len(ok),
        "transferred_mass_g_mean": round(mean, 3),
        "transferred_mass_g_std": round(std, 3),
        "cv_percent": round(100 * std / mean, 1) if mean else None,
        "spilled_mass_g_mean": round(sum(s["spilled_mass_g"] for s in ok) / len(ok), 3),
        "airborne_mean": round(sum(s["airborne_or_on_tool"] for s in ok) / len(ok), 1),
    }


def _plots(results: dict, out: Path) -> list[str]:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    written = []
    bench = [b for b in results["bench"] if b.get("ok")]
    if bench:
        fig, ax = plt.subplots(figsize=(7, 4.5))
        for solver in BENCH_SOLVERS:
            rows = [b for b in bench if b["solver"] == solver]
            if rows:
                ax.plot(
                    [b["n_particles"] for b in rows],
                    [b["steps_per_sec"] for b in rows],
                    marker="o",
                    label=solver.upper(),
                )
        ax.set(xlabel="particles", ylabel="steps/sec", title="Solver throughput (Metal)")
        ax.set_xscale("log")
        ax.set_yscale("log")
        ax.grid(True, which="both", alpha=0.3)
        ax.legend()
        fig.tight_layout()
        fig.savefig(out / "bench_steps_per_sec.png", dpi=120)
        written.append("bench_steps_per_sec.png")
    for backend, scoops in results["scoop"].items():
        ok = [s for s in scoops if s.get("ok")]
        if not ok:
            continue
        fig, ax = plt.subplots(figsize=(7, 4.5))
        masses = [s["transferred_mass_g"] for s in ok]
        mean = sum(masses) / len(masses)
        ax.bar(range(len(ok)), masses, color="tab:blue", alpha=0.8, label="transferred")
        ax.bar(
            range(len(ok)),
            [s["spilled_mass_g"] for s in ok],
            color="tab:red",
            alpha=0.6,
            label="spilled (new, grounded)",
        )
        ax.axhline(mean, color="k", ls="--", lw=1, label=f"mean {mean:.1f} g")
        ax.set(
            xlabel="seeded repetition",
            ylabel="mass (g)",
            title=f"Scripted scoop repeatability ({backend})",
        )
        ax.legend()
        fig.tight_layout()
        fig.savefig(out / f"scoop_repeatability_{backend}.png", dpi=120)
        written.append(f"scoop_repeatability_{backend}.png")
    for key in ("slump", "pour"):
        ok = [p for p in results.get(key, []) if p.get("ok") and p.get("surface_profile")]
        if not ok:
            continue
        fig, ax = plt.subplots(figsize=(6.5, 4.5))
        for p in ok:
            rs = [q[0] for q in p["surface_profile"]]
            zs = [q[1] for q in p["surface_profile"]]
            ax.plot(
                rs, zs, marker="o", ms=3, label=f"fa={p['friction_angle']} → {p['angle_fit_deg']}°"
            )
        ax.set(xlabel="radius (m)", ylabel="height (m)", title=f"{key} pile profiles")
        ax.grid(alpha=0.3)
        ax.legend(fontsize=8)
        fig.tight_layout()
        fig.savefig(out / f"{key}_pile_profiles.png", dpi=120)
        written.append(f"{key}_pile_profiles.png")
    return written


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", default="metal", help="genesis backend (metal|cpu|gpu)")
    parser.add_argument("--case", default=None, help="internal: run ONE case in this process")
    parser.add_argument("--out", type=Path, default=OUT_DIR)
    parser.add_argument("--scoop-reps", type=int, default=SCOOP_REPS)
    args = parser.parse_args()

    if args.case:  # child mode: one genesis init, one case, JSON out
        print(json.dumps(run_case(args.case, args.backend)))
        return 0

    args.out.mkdir(parents=True, exist_ok=True)
    import genesis

    results: dict = {
        "spike": "T20 powder (SPEC 300 PW-0)",
        "backend": args.backend,
        "platform": platform.platform(),
        "machine": platform.machine(),
        "genesis_version": genesis.__version__,
        "particle_size_m": PARTICLE_SIZE,
        "rho_kg_m3": RHO,
        "particle_mass_g": round(PARTICLE_MASS_KG * 1000, 6),
        "dt": DT,
        "substeps": SUBSTEPS,
        "determinism": [],
        "bench": [],
        "scoop": {},
        "slump": [],
        "pour": [],
    }

    # (d) determinism first: it decides how to read every scoop statistic
    backends = (args.backend, "cpu") if args.backend != "cpu" else ("cpu",)
    for backend in backends:
        print(f"determinism check on {backend} ...", file=sys.stderr)
        results["determinism"].append(_determinism(backend))

    for solver in BENCH_SOLVERS:
        for n in BENCH_COUNTS:
            spec = f"bench:{solver}:{n}"
            print(f"running {spec} ...", file=sys.stderr)
            results["bench"].append(_subprocess_case(spec, args.backend))

    # scoops per backend: the throughput backend AND cpu (deterministic
    # reference) so the CV is read against the determinism result
    for backend in dict.fromkeys(backends):
        results["scoop"][backend] = []
        for seed in range(args.scoop_reps):
            print(f"running scoop:{seed} on {backend} ...", file=sys.stderr)
            results["scoop"][backend].append(_subprocess_case(f"scoop:{seed}", backend))

    for fa in (None, 35.0, 55.0):
        suffix = "" if fa is None else f":{fa}"
        print(f"running slump{suffix} ...", file=sys.stderr)
        results["slump"].append(_subprocess_case(f"slump{suffix}", args.backend))
        print(f"running pour{suffix} ...", file=sys.stderr)
        results["pour"].append(_subprocess_case(f"pour{suffix}", args.backend))

    results["scoop_summary"] = {
        backend: _scoop_stats(scoops) for backend, scoops in results["scoop"].items()
    }
    results["plots"] = _plots(results, args.out)

    # CON-8: ok IFF every case succeeded — a partial sweep must not
    # masquerade as a result
    all_cases = (
        results["determinism"]
        + results["bench"]
        + [s for scoops in results["scoop"].values() for s in scoops]
        + results["slump"]
        + results["pour"]
    )
    failed = [c for c in all_cases if not c.get("ok")]
    results["ok"] = not failed
    results["failed_cases"] = [
        {"case": c.get("case") or c.get("backend"), "error": c.get("error")} for c in failed
    ]
    (args.out / "results.json").write_text(json.dumps(results, indent=1))
    print(
        json.dumps(
            {
                "ok": results["ok"],
                "results": str(args.out / "results.json"),
                "failed_cases": results["failed_cases"],
                "determinism": results["determinism"],
                "scoop_summary": results["scoop_summary"],
            }
        )
    )
    return 0 if results["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
