#!/usr/bin/env python3
"""T20 powder spike (SPEC 300 PW-0, Class A): measure whether Genesis
particle solvers on THIS machine can carry the powder family.

(a) steps/sec for a powder-in-vessel scene, N in {5k, 20k, 50k}, solver in
    {MPM sand, PBD particles, SPH liquid} — SPH is a liquid model, included
    as the "as available" data point PW-0 names, not a powder candidate;
(b) a scripted scoop with a simple spatula (thin rigid blade, kinematically
    driven), 20 seeded repetitions: transferred-mass mean/std and spill;
(c) qualitative pour: a dropped column must settle into a stable pile with
    a plausible angle of repose and no explosion.

Outputs runs/spike-powder/results.json + PNG plots. Numbers feed
docs/decisions/ADR-powder-spike.md (DRAFT; go/no-go is human, PW-0).

Isolation: every case runs in its OWN subprocess (`--case ...`) — genesis
initializes once per process, and a 50k Metal failure must not kill the
sweep. JSON to stdout, logs to stderr, exit 0 iff ok (CON-8).
"""

from __future__ import annotations

import argparse
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

BENCH_COUNTS = (5_000, 20_000, 50_000)
BENCH_SOLVERS = ("mpm", "pbd", "sph")
WARMUP_STEPS = 20
TIMED_STEPS = 100
SCOOP_REPS = 20


def _edge_for(n_target: int) -> float:
    """Cube edge that the regular sampler fills with ~n_target particles."""
    return round(PARTICLE_SIZE * round(n_target ** (1 / 3)), 4)


def _init_genesis(backend: str):
    import genesis as gs

    gs.init(backend=getattr(gs, backend), logging_level="warning", seed=0)
    return gs


def _vessel(gs, scene, center, inner=0.16, wall_h=0.14, wall_t=0.01):
    """An open-top vessel from five fixed boxes; returns its interior AABB."""
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


def _solver_options(gs, solver: str, dt: float, cpic: bool = False):
    # MPM pads the domain inward (~5%): the physical floor (z=0) must sit
    # comfortably INSIDE the padded boundary, so the domain floor goes lower
    bounds = {"lower_bound": (-0.6, -0.6, -0.2), "upper_bound": (0.6, 0.6, 0.8)}
    if solver == "mpm":
        return {
            "mpm_options": gs.options.MPMOptions(
                dt=dt, particle_size=PARTICLE_SIZE, enable_CPIC=cpic, **bounds
            )
        }
    if solver == "pbd":
        return {"pbd_options": gs.options.PBDOptions(dt=dt, particle_size=PARTICLE_SIZE, **bounds)}
    return {"sph_options": gs.options.SPHOptions(dt=dt, particle_size=PARTICLE_SIZE, **bounds)}


def case_bench(backend: str, solver: str, n_target: int) -> dict:
    """(a) steps/sec for a powder block settling inside a vessel — in the
    STABLE integration regime this spike measured (dt 1 ms, substeps 2;
    dt 2 ms / substeps 1 explodes the settle on BOTH metal and cpu)."""
    gs = _init_genesis(backend)
    dt = 1e-3
    scene = gs.Scene(
        sim_options=gs.options.SimOptions(dt=dt, substeps=2),
        **_solver_options(gs, solver, dt),
        show_viewer=False,
    )
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
        "dt": dt,
        "build_s": round(build_s, 2),
        "steps_per_sec": round(TIMED_STEPS / elapsed, 2),
        "sim_seconds_per_wall_second": round(TIMED_STEPS * dt / elapsed, 4),
    }


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


def _count_in(pos, lo, hi) -> int:
    import numpy as np

    inside = np.all((pos >= np.asarray(lo)) & (pos <= np.asarray(hi)), axis=1)
    return int(inside.sum())


def case_scoop(backend: str, seed: int) -> dict:
    """(b) one seeded scripted scoop: source vessel -> spatula -> receiver.

    The spatula is a thin rigid blade driven KINEMATICALLY (set_pos/set_quat
    per step — the same rigid-coupling pattern the store bridge uses): dip
    near the source wall, sweep across the powder, lift, carry to the
    receiver, tilt 120 degrees to dump."""
    import numpy as np

    rng = __import__("random").Random(seed)
    gs = _init_genesis(backend)
    dt = 1e-3
    scene = gs.Scene(
        sim_options=gs.options.SimOptions(dt=dt, substeps=2),
        # CPIC is REQUIRED here (measured, not assumed): without it the
        # MPM-rigid coupling ejects the pile against the thin vessel walls
        # during plain settling on Metal; the earlier NaN was a spawn-in-
        # contact bug, not CPIC
        **_solver_options(gs, "mpm", dt, cpic=True),
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
    # blade retains nothing through the lift (measured: 0 or bulldozed).
    # Parts are separate rigid boxes driven in LOCKSTEP with pitch-rotated
    # offsets in the tool frame.
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
    vel0 = _np(powder.get_particles_vel()).reshape(-1, 3)
    base_in_src = _count_in(pos0, src_lo, src_hi)
    base_out = pos0.shape[0] - base_in_src  # settle splash, not scoop spill
    import numpy as _n

    print(
        f"settle: in_src={base_in_src} out={base_out} "
        f"max_v={float(_n.linalg.norm(vel0, axis=1).max()):.2f} "
        f"z=[{pos0[:, 2].min():.3f},{pos0[:, 2].max():.3f}]",
        file=sys.stderr,
    )

    # the settled pile is SHALLOW (~1.2 cm over the vessel floor at z=0.01):
    # the blade must skim the floor to gather anything
    # blade center height: floor top 0.010 + half thickness + the pitched
    # leading edge's drop (25 mm * sin 6 deg ~ 2.6 mm) + ~2 mm clearance
    z_dip = 0.016 + rng.uniform(-0.001, 0.001)
    x0, x1 = -0.20, -0.12  # sweep across the source vessel, clear of the wall
    x_carry, z_carry = 0.15, 0.25
    flat = 0.0
    nose_down = math.radians(-4.0)  # leading edge digs
    cup = math.radians(12.0)  # slight tilt back to hold the load

    def seg(p_from, p_to, pitch_from, pitch_to, steps):
        vel = [(p_to[i] - p_from[i]) / (steps * dt) for i in range(3)]
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
    # per-step displacement stays well under one particle size (~0.5 mm/step
    # at dt=2 ms): a teleport-driven tool that outruns the grid explodes
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
    in_src = _count_in(pos, src_lo, src_hi)
    in_rcv = _count_in(pos, rcv_lo, rcv_hi)
    on_blade = int((pos[:, 2] > 0.2).sum())  # still airborne/carried
    # net of the settle splash: only powder the SCOOP put outside counts
    spilled = max(n - in_src - in_rcv - on_blade - base_out, 0)
    return {
        "case": "scoop",
        "seed": seed,
        "n_particles": n,
        "in_source": in_src,
        "in_receiver": in_rcv,
        "spilled": spilled,
        "transferred_mass_g": round(in_rcv * PARTICLE_MASS_KG * 1000, 3),
        "spilled_mass_g": round(max(spilled, 0) * PARTICLE_MASS_KG * 1000, 3),
        "max_speed_end": round(float(np.linalg.norm(vel, axis=1).max()), 3),
    }


def case_pour(backend: str, friction_angle: float | None = None) -> dict:
    """(c) qualitative: drop a tall column onto the plane; a stable pile
    with a plausible angle of repose must form (sand ~30-40 deg), with no
    explosion (bounded end-state speeds)."""
    import numpy as np

    gs = _init_genesis(backend)
    dt = 1e-3
    scene = gs.Scene(
        sim_options=gs.options.SimOptions(dt=dt, substeps=2),
        **_solver_options(gs, "mpm", dt),
        show_viewer=False,
    )
    scene.add_entity(gs.morphs.Plane())
    # LOW drop: a tall column smashed from height tests splatter, not
    # repose (the first sweep's 24 cm drop made a pancake regardless of
    # friction) — release just above the floor and let it slump
    powder = scene.add_entity(
        material=_powder_material(gs, "mpm", friction_angle),
        morph=gs.morphs.Box(size=(0.09, 0.09, 0.12), pos=(0.0, 0.0, 0.075)),
    )
    scene.build()
    for _ in range(1200):
        scene.step()
    pos = _np(powder.get_particles_pos()).reshape(-1, 3)
    vel = _np(powder.get_particles_vel()).reshape(-1, 3)
    center = pos[:, :2].mean(axis=0)
    r = np.linalg.norm(pos[:, :2] - center, axis=1)
    z = pos[:, 2]
    peak_h = float(np.percentile(z, 98))
    base_r = float(np.percentile(r, 95))
    # flank slope: max height per radial bin, fit over the outer flank
    bins = np.linspace(0, base_r, 12)
    idx = np.digitize(r, bins)
    surf = [(bins[i - 1], z[idx == i].max()) for i in range(1, len(bins)) if (idx == i).any()]
    rs, zs = np.array([s[0] for s in surf]), np.array([s[1] for s in surf])
    flank = rs > base_r * 0.3
    slope = float(np.polyfit(rs[flank], zs[flank], 1)[0]) if flank.sum() >= 3 else float("nan")
    angle_fit = math.degrees(math.atan(abs(slope))) if not math.isnan(slope) else float("nan")
    return {
        "case": "pour",
        "friction_angle": friction_angle,
        "n_particles": int(pos.shape[0]),
        "peak_height_m": round(peak_h, 4),
        "base_radius_m": round(base_r, 4),
        "angle_simple_deg": round(math.degrees(math.atan(peak_h / base_r)), 1),
        "angle_fit_deg": round(angle_fit, 1),
        "max_speed_end": round(float(np.linalg.norm(vel, axis=1).max()), 3),
        "stable": bool(np.linalg.norm(vel, axis=1).max() < 0.05),
        "surface_profile": [[round(float(a), 4), round(float(b), 4)] for a, b in surf],
    }


def run_case(spec: str, backend: str) -> dict:
    kind, *rest = spec.split(":")
    if kind == "bench":
        return case_bench(backend, rest[0], int(rest[1]))
    if kind == "scoop":
        return case_scoop(backend, int(rest[0]))
    if kind == "pour":
        return case_pour(backend, float(rest[0]) if rest else None)
    raise ValueError(f"unknown case {spec!r}")


def _subprocess_case(spec: str, backend: str, timeout_s: float = 1800.0) -> dict:
    proc = subprocess.run(
        [sys.executable, __file__, "--case", spec, "--backend", backend],
        capture_output=True,
        text=True,
        timeout=timeout_s,
    )
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-4:]
        return {"case": spec, "ok": False, "error": " | ".join(tail)}
    # genesis prints sampler noise to stdout before our JSON: take the
    # LAST line that parses (CON-8 discipline is ours, not genesis's)
    for line in reversed(proc.stdout.splitlines()):
        if line.startswith("{"):
            return {**json.loads(line), "ok": True}
    return {"case": spec, "ok": False, "error": "no JSON line in child stdout"}


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
    scoops = [s for s in results["scoop"] if s.get("ok")]
    if scoops:
        fig, ax = plt.subplots(figsize=(7, 4.5))
        masses = [s["transferred_mass_g"] for s in scoops]
        mean = sum(masses) / len(masses)
        ax.bar(range(len(masses)), masses, color="tab:blue", alpha=0.8, label="transferred")
        ax.bar(
            range(len(masses)),
            [s["spilled_mass_g"] for s in scoops],
            color="tab:red",
            alpha=0.6,
            label="spilled",
        )
        ax.axhline(mean, color="k", ls="--", lw=1, label=f"mean {mean:.1f} g")
        ax.set(xlabel="seeded repetition", ylabel="mass (g)", title="Scripted scoop repeatability")
        ax.legend()
        fig.tight_layout()
        fig.savefig(out / "scoop_repeatability.png", dpi=120)
        written.append("scoop_repeatability.png")
    pour = results.get("pour")
    if pour and pour.get("ok") and pour.get("surface_profile"):
        fig, ax = plt.subplots(figsize=(6, 4.5))
        rs = [p[0] for p in pour["surface_profile"]]
        zs = [p[1] for p in pour["surface_profile"]]
        ax.plot(rs, zs, marker="o", label="pile surface")
        ax.set(
            xlabel="radius (m)",
            ylabel="height (m)",
            title=f"Pour pile — angle ~{pour['angle_fit_deg']} deg (fit), "
            f"{pour['angle_simple_deg']} deg (peak/base)",
        )
        ax.grid(alpha=0.3)
        ax.legend()
        fig.tight_layout()
        fig.savefig(out / "pour_pile_profile.png", dpi=120)
        written.append("pour_pile_profile.png")
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
    results: dict = {
        "spike": "T20 powder (SPEC 300 PW-0)",
        "backend": args.backend,
        "platform": platform.platform(),
        "machine": platform.machine(),
        "particle_size_m": PARTICLE_SIZE,
        "rho_kg_m3": RHO,
        "particle_mass_g": round(PARTICLE_MASS_KG * 1000, 6),
        "bench": [],
        "scoop": [],
        "pour": None,
    }
    import genesis

    results["genesis_version"] = genesis.__version__

    for solver in BENCH_SOLVERS:
        for n in BENCH_COUNTS:
            spec = f"bench:{solver}:{n}"
            print(f"running {spec} ...", file=sys.stderr)
            results["bench"].append(_subprocess_case(spec, args.backend))
    for seed in range(args.scoop_reps):
        print(f"running scoop:{seed} ...", file=sys.stderr)
        results["scoop"].append(_subprocess_case(f"scoop:{seed}", args.backend))
    print("running pour ...", file=sys.stderr)
    results["pour"] = _subprocess_case("pour", args.backend)
    # friction sweep: does the material knob move the repose angle at all?
    results["pour_friction"] = [
        {**_subprocess_case(f"pour:{fa}", args.backend), "friction_angle": fa}
        for fa in (15.0, 35.0, 55.0)
    ]

    scoops = [s for s in results["scoop"] if s.get("ok")]
    if scoops:
        masses = [s["transferred_mass_g"] for s in scoops]
        mean = sum(masses) / len(masses)
        std = math.sqrt(sum((m - mean) ** 2 for m in masses) / len(masses))
        results["scoop_summary"] = {
            "reps_ok": len(scoops),
            "transferred_mass_g_mean": round(mean, 3),
            "transferred_mass_g_std": round(std, 3),
            "cv_percent": round(100 * std / mean, 1) if mean else None,
            "spilled_mass_g_mean": round(sum(s["spilled_mass_g"] for s in scoops) / len(scoops), 3),
        }
    results["plots"] = _plots(results, args.out)
    (args.out / "results.json").write_text(json.dumps(results, indent=1))
    print(
        json.dumps(
            {
                "ok": True,
                "results": str(args.out / "results.json"),
                **{k: results[k] for k in ("scoop_summary",) if k in results},
            }
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
