# Nexus vs Genesis World: a deep technical parity analysis for AISLE

**Prepared 2026-08-24 for discussion with Sébastien Crozet (dimforge).**
Sources: dimforge/nexus @ v0.5.0 (full issue/PR sweep), haixuanTao/zealot @ HEAD
(2026-08-24), Genesis World v1.3.3 docs + issue tracker, and the AISLE
requirement base (specs/, docs/decisions/, measured spike ADRs). Every claim
carries its source; effort judgments are marked as estimates.

---

## 0. Executive summary

1. **ADR-42's desk/retail NO-GO still stands, but its strongest sentence has
   weakened.** The ADR said sensor simulation is "a different product surface"
   that won't arrive "by waiting for a version bump." Since then Nexus merged
   headless off-screen RGB capture at 92 fps (PR #11), a Python path-tracer
   API (PR #8), and PR #7's stated motivation was literally *"as
   mujoco.Renderer.render() or a Genesis camera.render() would give."* The
   trajectory toward a simulator is real. What remains missing is precisely
   enumerable: **depth, segmentation, camera intrinsics/mount model, and
   multi-camera** — four items, not a product pivot (§7.2).
2. **zealot is the existence proof that the Nexus stack can be a *training
   simulator* at competitive scale** — a deployable humanoid policy (flown on
   a real G1 via LeRobot), 0.7–1.2× NVIDIA's shipped G1 pipeline throughput on
   one RTX 5090 — but it is also proof of the boundary: its only exteroception
   is a 5-float oracle "step cue" designed explicitly to avoid vision. No
   pixel-observation path exists anywhere in the ecosystem (§4).
3. **For AISLE's bench/powder family the case has inverted.** AISLE's own
   powder spike measured Genesis granular simulation failing on three axes at
   once — nondeterministic *and* ~10% crashy on Metal, bit-exact but 20×
   too slow on CPU, and no angle of repose at any friction setting. Nexus's
   new GPU MPM (merged 2026-08-15) with sparse grids and dynamic particle
   add/remove is a strong structural fit. The blocker is coupling: Nexus MPM
   is **one-way** (rigid→particle), and AISLE's SPEC 310 makes wrist
   force/torque the *primary safety mechanism* for this family. A minimal
   "per-collider particle-impulse readback" (sensing without dynamics) would
   unblock it ahead of full two-way coupling (§8).
4. **Determinism is Nexus's sleeper advantage.** AISLE's CON-5 had to be
   *weakened* (ADR-26) because Genesis-on-Metal cannot produce bit-exact
   replicates (single-ULP divergences that flip episode outcomes). zealot
   demonstrates same-source kernels compiled to CUDA and WebGPU running
   **bit-exact against each other**. A bit-exact GPU physics engine would let
   AISLE *tighten* its reproducibility constitution — an argument Genesis
   cannot currently answer (§9).
5. **Honest blockers on the Nexus side today**: macOS Metal multibody
   contacts are broken upstream (naga MSL miscompile, nexus issue #5 —
   and macOS arm64 is AISLE's constitutionally primary platform, CON-1);
   14 open unreviewed contributor PRs including three correctness fixes to
   articulated dynamics (#25 dt bug, #26 friction leak, #27 Jacobian lever
   arm); a one-maintainer review bottleneck; no documentation site content.
   Any adoption conversation is also a maintenance-bandwidth conversation
   (§3.4, §12).

---

## 1. What AISLE requires of its simulator

AISLE's requirements were extracted from the frozen specs and measured ADRs
into 76 numbered items (R1–R76; the full list lives in the extraction record).
The load-bearing structure is four requirement clusters.

### 1.1 The sensor chain is fail-closed, so it is all-or-nothing

The topic contract (SPEC 010) obliges the bridge to publish:

| Topic | Schema | Rate | Consumer |
|---|---|---|---|
| `rgb_overhead` | `UInt8[h·w·3]`, 640×480 | 30 Hz | VER-9 identity (OWLv2), L2 pose |
| `rgb_wrist` | `UInt8[h·w·3]`, 320×240, **EE-link-mounted** | 30 Hz | VER-9 corroborating vote |
| `depth_overhead` | `Float32[h·w]`, metric metres | 15 Hz | VER-10 containment (depth-**only** by ADR), L1/L2 pose |
| `seg_overhead` | `Int32[h·w]` + published id map | 15 Hz (L1 rung only) | L1 segmented-pose |

Three properties make this non-negotiable rather than nice-to-have:

- **VER-8 refuses to judge without an attested calibration block** — versioned
  v1 schema in OpenCV conventions: per-camera `{resolution, fov_deg,
  intrinsics{fx,fy,cx,cy}, cam_to_base/cam_to_ee, depth_scale_m}`, with exact
  pinned numerics (overhead: `fx = fy = 240/tan(27.5°) ≈ 461.04`,
  `cx = 319.5`). The block must come from the *realized* built scene
  (post-jitter read-back of the camera transform), not from config. Even the
  camera roll convention is pinned — AISLE reproduces Genesis's
  degenerate-lookat epsilon branch exactly because a tighter epsilon once
  produced a 90°-wrong roll.
- **VER-13 fuses fail-closed**: a stage that cannot produce a verdict makes
  the episode FAIL — "never skip-and-fuse." No cameras ⇒ no calibration ⇒
  VER-8 refusal ⇒ every episode of every run scores FAIL. There is no
  degraded mode.
- **The perception ladder (TC-9) collapses without rendering**: L1 requires
  same-stamp segmentation+depth from one render pass; L2 requires RGB
  identity plus same-stamp depth. A render-less engine hosts only L0 (oracle
  poses) — "the rung that measures the least."

One further sensor subtlety: RGB, depth, and segmentation must come from **one
render pass carrying one `sim_time_ns`**; rendering must be rate-limited
independently of physics; and T2 label-reading requires UV-mapped meshes with
image textures (primitive boxes carry no UVs).

### 1.2 The stepping contract is lockstep, single-step, and honest

- **Single-step-on-demand** (ADR-30): apply queued inputs in canonical order,
  advance physics by exactly `dt`, once. No internal wall clock, no
  uncontrolled substepping, no async stepping.
- **Reset is a service**: teleport re-injection of all entity poses, zeroed
  velocities, re-latched PD targets, **without process restart**, in < 2 s;
  the first post-reset snapshot must be a pure function of the seed and is
  published before any physics step.
- **Failure honesty**: the bridge deliberately wraps no try/except around
  `scene.step()` — sim exceptions must crash loudly (BRG-7).
- Per-message `sim_time_ns` / `env_id` / `seq`; batched envs need per-env
  state addressing on *every* write (a global velocity-zero once froze another
  agent's carried box mid-swing in a fleet probe).

### 1.3 Determinism: what CON-5 demands and what Genesis forced AISLE to concede

CON-5 is layered: (a) seed-derived artifacts bit-identical; (b) trace timing
exact; (c) physics state within 1e-6 over the first 1.0 s after each reset;
(d) episode *outcomes* only statistical. Layer (d) exists **because of
Genesis**: ADR-26 measured identical cold runs on Metal diverging by a single
joint-state ULP at unpredictable steps (GPU parallel-reduction ordering),
which chaos amplifies into episode-level outcome flips. Quote: *"No
configuration of this stack yields bit-exact replicates on Metal."* The CPU
backend is bit-exact but ~10× slower than needed at campaign scale — Genesis
makes AISLE choose between reproducibility and evidence throughput.

### 1.4 The bench family (SPEC 300/310) asks different questions

- **PW-3**: the oracle is *mass* — particle count in the receiving vessel ×
  particle mass, exact by construction. Needs per-particle positions and
  stable identity, not cameras. The realistic channel is a simulated balance
  (`balance_mass` + noise/settling model), not a detector.
- **PW-4** caps sim claims at *control strategy and architecture* — milligram
  fidelity is hardware-only. So qualitative granular behaviour + repeatability
  is the bar, which is exactly what a solver swap can clear.
- **FT-1/FT-3 (SPEC 310)**: wrist F/T `Float32[6]` at ≥200 Hz, and force
  limits as *the primary safety mechanism* near glass vessels. A loaded scoop
  exerting reaction forces **is** particle→rigid coupling, at least at the
  sensing level.
- **PW-0** gates the family on a measured spike: particle throughput at
  5k/20k/50k, scripted-scoop repeatability (std/mean over 20 seeded scoops),
  pile/pour sanity. Any replacement engine re-runs the same spike harness.

### 1.5 Integration fence

`env_hash` today is one sha256 over 59 frozen files; the rollout gate
(`resolve_sim_identity`) is Genesis-hardcoded with a literal
`{cpu, metal, cuda}` backend enum; `bridge_info` carries a field literally
named `genesis_version`. Issue #283 (composite env_hash, sim-backend protocol,
per-sim layout, guard relocation) is the pre-work any second engine triggers,
with a required bridging measurement (re-run the M0 gate + one tier curve at
the new hash, show identity). It was deliberately parked as "rescoped, not
closed" when ADR-42 landed.

Also constitutional: macOS arm64 / Metal is the **primary platform** (CON-1 —
CUDA-only deps forbidden in default extras), and the sim must be a Python
package installable under uv (CON-2). Nexus's WebGPU portability and PyO3
bindings fit both — *if* Metal correctness holds (§3.4).

---

## 2. Nexus as of v0.5.0: what exists, with evidence

Timeline for calibration of expectations: repo created 2025-09-08; **v0.4.0
(2026-07-04) was a complete rewrite** ("a full GPU physics engine written in
rust-gpu"); v0.5.0 (2026-08-16) added MPM. The engine in its current
architecture is under two months old. 82 stars, 3 contributors, effectively
one maintainer (sebcrozet) plus one prolific external contributor
(haixuanTao, dora-rs maintainer and zealot author).

### 2.1 Engine

- "Rapier on the GPU": worlds are authored as rapier `PhysicsWorld`s and baked
  to GPU buffers on `finalize` — **one rapier world per environment**, stepped
  in parallel. Batching is first-class and explicitly aimed at RL.
- Rigid bodies: full collider set, LBVH GPU broad phase (brute-force path for
  ≤64 colliders), TGS-soft solver with warmstart/speculative contacts.
- **Both** joint models: maximal-coordinate impulse joints *and*
  reduced-coordinate multibodies (3D) with per-multibody mass matrix + dense
  LU on GPU; PR #31 brought "multibody features and stability on par with
  rapier 0.35"; v0.5.0 added self-contacts, per-link external wrenches,
  runtime motor retargeting.
- URDF/MJCF import via rapier3d-urdf/-mjcf (MuJoCo Menagerie load); full MJCF
  *actuator semantics* (`<position>` kp/kv servos inside the GPU solver) exist
  only in open PR #12.
- Backends: WebGPU (default) / Metal / CPU / CUDA features; wasm32 browser
  builds; native-CUDA via cuda-oxide is draft PR #13 (blocked on upstream),
  though contributor benchmarks already run it.

### 2.2 Rendering surface (the decisive axis)

What exists, all merged in July 2026:

- **PR #7**: `NexusViewer.render()` → `(H, W, 3) uint8` NumPy RGB. The PR's own
  motivation text benchmarks against `Genesis camera.render()`.
- **PR #11**: true headless mode (no window, no swapchain, off-screen texture,
  no vsync throttle) with pipelined async capture — **92 fps at 640×480** on
  an RTX 5080. This supersedes the 365 ms/frame naive readback measured in
  the #7 thread; AISLE's 30 Hz overhead + 30 Hz wrist budget is comfortably
  within reach on paper.
- **PR #8**: Python access to kiss3d's GPU path tracer (`raytrace_frame()`,
  samples/bounces/denoise) — pretty RGB, not a sensor model.

What does not exist (verified by code search, not just absence of docs):

- **No depth readback of any kind.** The only "depth" in the tree is path-tracer
  bounce depth.
- **No segmentation.**
- **No camera intrinsics model** — the bound camera API is look-at
  (`set_camera(eye, target)`); no FOV setter is exposed in the Python
  bindings, no principal-point/fx/fy concept anywhere.
- **No link-mounted cameras, no multi-camera scenes** — and the viewer renders
  **environment 0 only** ("batch environments are physics-only", PR #16).
- **No raycast/scene-query public API** — a shader-side `Ray` type exists but
  nothing is exported host-side, so even a "depth via raycasts" shortcut is
  new engine work.
- No open issue, PR, or discussion mentions cameras, depth, or a sensor
  roadmap (discussions are disabled).

### 2.3 MPM (v0.5.0, merged 2026-08-15)

- Materials: corotated-linear + Neo-Hookean elasticity, Drucker-Prager sand,
  Stomakhin snow, weakly compressible fluid. 2D and 3D. Sparse grid (open
  domain — no bounding-box clamp), **dynamic particle add/remove** — both
  called out as differentiators vs typical open-source GPU MPM, and both
  directly useful for AISLE's scoop-and-pour episodes (Genesis requires a
  bounded domain).
- Rigid-boundary handling: grid-based (≥1.5 cell thickness) or **CPIC** for
  thin plates — currently **limited to 16 colliders**.
- **Coupling, exact statement**: "rigid-bodies … can push particles, but
  particles cannot push rigid-bodies … Full two-ways coupling will be
  implemented in the future." The `RbdCoupling::MpmTwoWay` variant hands a
  body *entirely* to MPM (rigid pipeline treats it as static) — useful for a
  free-floating object in powder, useless for a robot-held scoop.
- No published particle-throughput numbers anywhere (the sand example builds
  202,500 particles). PW-0 would produce the first real measurement.

### 2.4 Correctness backlog and project health — the honest part

Fourteen open PRs by haixuanTao, **none reviewed** as of 2026-08-24 (oldest
open since 2026-07-09), include three that contest articulated-dynamics
correctness at humanoid scale:

- **#25**: every multibody kernel integrates substeps with the wrong dt
  whenever the configured substep count ≠ 4 (NaN in ~4 steps at 16 iters).
- **#26**: contact compliance applied to friction rows makes μ = 1.0 behave
  like μ ≈ 0.03.
- **#27**: contact lever arms computed about the wrong point make an
  ankle-pitch Jacobian column 8× too small — "a quiet stand is impossible at
  any motor stiffness."

Plus **issue #5 (open)**: on macOS, a naga MSL loop miscompile makes multibody
contact/joint solves produce **zero impulses** — CUDA/Vulkan unaffected, fix
upstream in wgpu, workaround known (while→for loops), one instance already
fixed in v0.5.0. Until this is closed, **Nexus multibody is effectively broken
on AISLE's primary platform.** zealot ships a vendored naga fix to work around
it.

Batching scale limits on upstream main (fixed only in open PRs #16/#24):
single-env robot packing is quadratic in DOFs (~58 GiB requested at 2,048
robots); the 4096-pairs/env default hits wgpu's 4 GiB buffer wall near 512
envs. With #24 applied: 5.67M env-steps/s at 16,384 cube envs.

No docs-site content (landing page + demos + docs.rs only), no GitHub
releases, MIT OR Apache-2.0, sponsorship via GitHub Sponsors. None of this is
disqualifying for a research adoption; all of it belongs in a candid meeting.

---

## 3. zealot: the existence proof, and the boundary it marks

zealot (haixuanTao, created 2026-05-26, 420 commits in 13 weeks, 3
contributors) is a complete WBC humanoid training stack on Nexus — env + MDP
layer, PPO/GAE/Adam hand-rolled in Rust on the same portable GPU layer,
browser-deployable, and **deployed to a real Unitree G1** (checkpoint v21,
via LeRobot's `ZealotLocomotionController`).

**What it proves:**

- The Nexus stack sustains serious RL scale: full training iteration
  (rollout + PPO update) at **91.4k env-steps/s (N=4,096) / 99.5k (N=8,192)**
  on one RTX 5090 with native CUDA + cuTile — vs Isaac Lab's 126k/201k on the
  same box (≈2× gap at the top end), and **1.23× ahead of NVIDIA's shipped
  WBC-AGILE G1 pipeline at N=2,048** in a like-for-like realism+terrain
  configuration.
- Isaac-Lab-tier MDP machinery is reproducible on this stack: terrain
  curriculum ported from WBC-AGILE, extensive DR (including structural
  foot-shape DR sampling box/capsule feet per template — an engine-overfit
  countermeasure), delayed-actuator model, asymmetric actor-critic, per-env
  GPU reset from spawn-template snapshots.
- **Cross-backend bit-exactness is achievable** (§9).
- Sim-to-sim discipline: every policy validated against MuJoCo (the named
  reference), plus rapier.js, Isaac, and — notably — a
  `sim2sim_g1_genesis.py` harness. The cross-engine methodology AISLE would
  want already exists in this ecosystem.

**What it equally proves by omission:**

- Observations are proprioceptive (53→79 dims + 5-frame history). The single
  exteroceptive input is a **5-float oracle step cue** with sensor-shaped
  noise, explicitly designed as "a SCRIPTED perception step … rather than a
  CNN on raw depth." There is no camera, depth, or height-map pipeline in the
  repo, and the visible roadmap is entirely solver throughput.
- Rigid-body only; `MAX_MB_DOFS = 32` (the G1's wrists are welded to fit);
  no torque-speed actuator saturation; box (uncoupled) friction; Metal
  training path broken (the naga bug).
- Build fragility: ~6 patched sibling forks are load-bearing.
- **Zero dora-rs integration** — despite the author. The dora angle is an
  opportunity, not an existing artifact.

---

## 4. Genesis World as of v1.3.3: the incumbent, honestly assessed

AISLE pins `genesis-world>=1.2.3`. Current upstream: v1.3.3 (2026-08-13),
Apache-2.0, 29.8k stars, 106 contributors, backed since 2025 by Genesis AI
($105M seed, Jul 2025). Renamed from "Genesis" at v1.0 (May 2026); the
renderer is now **Nyx** (in-house path tracer — distributed from an *internal
package index*, not yet public), the compiler **Quadrants** (a Taichi fork);
LuisaRender is deprecated; the CUDA-only Madrona batch renderer serves RL.

**Why Genesis won the AISLE design in the first place (all still true):**
`cam.render(rgb, depth, segmentation, normal)` in one pass from a
visualization camera; link-mountable camera sensors; IMU/contact/tactile/
lidar/depth-raycaster sensor suite with noise models; MuJoCo-lineage rigid
solver with documented algorithms; MJCF/URDF/USD import; MPM/FEM/PBD/SPH/SF
solvers with **genuine two-way coupling** (three coupler implementations —
impulse-based legacy, Drake-style SAP, IPC via libuipc); batched +
heterogeneous envs; four-platform matrix including Apple Metal; pip/uv
install.

**Where it has genuinely hurt AISLE (measured, not anecdotal):**

- **Determinism**: opt-in (`use_deterministic_algorithms=True`), documented as
  per-machine bit-exactness at best, "only partially" on GPU; the default
  runtime **perf-dispatch** system live-times interchangeable kernels, so "a
  rollout also depends on everything the process ran before it." ADR-26's
  Metal ULP divergence forced CON-5's outcome layer to go statistical.
- **Throughput vs the contract**: BRG-2 targets ≥5× realtime with rendering;
  measured on M3 Metal: `scene.step()` 4.4 ms + 5.6 ms overhead render +
  3.4 ms wrist ⇒ sustained **~0.77× realtime**. The named remedy in the ADR
  is "batched offline stepping or a faster physics backend."
- **Granular media**: the powder-spike ADR measured Metal MPM nondeterministic
  (GPU atomics) *and* ~10% crashy (NaN "invalid constraint forces"); CPU MPM
  bit-exact but ~7 steps/s at 5k particles; scooped-mass CV 87.9%; and **no
  angle of repose emerges at any friction setting** (flank fits 2.7–6.3° vs
  ~30–40° physical) — AISLE had to rule that "scenes and verifiers must not
  depend on heap geometry."
- **Contact/grasp fidelity carried as config hacks**: SO-101's pinch cannot
  survive horizontal motion (`kinematic_carry_latch = true` — the carry is
  faked kinematically); Franka finger gains had to be raised 4× over
  defaults to stop in-grip pitching.
- **Ecosystem friction**: the rendering/EGL setup breakage cluster is the
  largest issue category; 200 GB URDF-load memory spikes; MPM tunneling
  (#600); depth camera does not render MPM bodies in parallel envs (#2044,
  open); the team's own #3207 "Stability Initiative" candidly tracks
  unreproducible numerical failures. Headline performance claims ("10–80×
  faster than Isaac/MJX") were publicly contested (issue #181, Stone Tao's
  corrected benchmarks showing 3–10× *slower* than ManiSkill on manipulation
  under example-default settings) and later revised.

The honest frame for the meeting: Genesis is a funded, fast-moving,
full-surface simulator whose weaknesses for AISLE are concentrated exactly
where Nexus's strengths are (determinism, granular GPU physics, lean
portability) — and vice versa.

---

## 5. Axis-by-axis parity matrix

Severity: 🔴 = hard-blocking for desk/retail attested runs, 🟠 = blocks a tier
or family, 🟡 = friction/upside, ✅ = parity or better.

| Axis | AISLE requirement | Genesis v1.3.3 | Nexus v0.5.0 (+ ecosystem) | Nexus status |
|---|---|---|---|---|
| Programmatic RGB | 640×480 + 320×240 @30 Hz, one pass w/ depth/seg | `cam.render()` multi-channel | headless `snap_rgb` 92 fps, RGB **only**, single camera | 🟠 partial |
| Metric depth | Float32 m @15 Hz (VER-10 is depth-only) | yes (viz camera + raycaster sensor) | **none** | 🔴 |
| Segmentation | Int32 id map @15 Hz (L1 rung) | yes, + seg-id dict | **none** | 🔴 (L1) |
| Camera intrinsics / calibration | v1 OpenCV block from realized state, pinned numerics | intrinsics + transform read-back (AISLE conformance-tests against it) | look-at only; no FOV/intrinsics binding | 🔴 |
| Link-mounted camera | wrist cam rides EE every step | `cam.attach(link, offset_T)` / sensor `entity_idx` | not supported | 🔴 |
| Multi-camera, rate-scheduled | overhead+wrist, independent rates | yes | one viewer camera, env 0 only | 🔴 |
| UV/texture rendering (T2 labels) | image-textured mesh boxes | yes | kiss3d mesh support (unverified for this use) | 🟠 verify |
| Rigid solver + articulations | Franka 7+2 / SO-101 5+1, PD control, stable grasp | MuJoCo-lineage; grasp needed gain hacks + carry latch | reduced-coord multibody, TGS-soft; correctness PRs #25/#26/#27 pending; ≤32–64 DOF | 🟠→✅ with PR review |
| URDF/MJCF import | both, with decomposition control | yes (+USD) | yes (rapier3d-urdf/mjcf); actuator semantics in open PR #12 | 🟡 |
| Build-time IK | multi-start, rot_mask, TCP offset | yes (used at build only; runtime IK is AISLE's own) | none exposed (AISLE's runtime chain is engine-independent — only SCN-3 needs a solve) | 🟠 |
| Single-step lockstep + injectable reset | ADR-30; teleport <2 s, no restart | yes (AISLE-shaped) | `simulate()` per-call stepping; zealot does per-env snapshot resets on GPU | ✅ likely |
| Batched envs | `n_envs` + per-env addressing | yes; heterogeneous | first-class (world-per-env); scale fixes in open PRs #16/#24 | ✅ (post-merge) |
| Deterministic replicates | CON-5; Genesis failed (c)→(d) on Metal | opt-in, per-machine, GPU partial, perf-dispatch nondeterminism by design | engine makes no claims; zealot demonstrates CUDA↔WebGPU **bit-exact**, deterministic LCG, CPU reference to float-ε | ✅ potential — the differentiator |
| macOS Metal (CON-1 primary platform) | required, deterministic preferred | works; float64 unavailable; ULP nondeterminism | **broken multibody contacts** (naga #5); Vulkan/CUDA fine | 🔴 today, fixable |
| Granular media (bench) | PW-0/PW-3: throughput, repeatability, pile sanity, particle readback | measured: crashy/nondet. on Metal, 20× slow on CPU, no repose angle | GPU MPM w/ Drucker-Prager sand, sparse grid, dynamic emit; unmeasured; Python particle-position readback **to verify** | 🟠 promising |
| Particle↔rigid coupling (bench) | wrist F/T ≥200 Hz; force-limit safety; tool drag | two-way (3 couplers) but see spike failures | **one-way**; `MpmTwoWay` = MPM-owned bodies ≤16 (CPIC limit) | 🔴 (bench) — see §8.2 |
| Wrist F/T sensing | FT-1 (bench family) | no dedicated F/T API used yet (contact/tactile sensors exist) | per-link *applied* wrenches exist; contact-wrench readback partial (zealot: "nexus host API doesn't expose" true contact pairs) | 🟠 |
| Throughput | BRG-2 ≥5× realtime w/ rendering (Genesis: 0.77×) | 0.77× measured (M3) | physics far faster at batch scale; un-benchmarked in AISLE's single-env + render regime | 🟡 upside |
| Python/uv packaging | CON-2 | pip `genesis-world` | PyO3/maturin, PyPI `dimforge-nexus3d` | ✅ |
| Browser/WASM | (not required; demo value) | no | GPU sim in-browser, live demos | ✅ bonus |
| License | permissive required | Apache-2.0 | MIT OR Apache-2.0 | ✅ |
| Maturity/staffing | frozen-fence stability expectations | 106 contributors, corporate backing, weekly releases | 1 maintainer + 1 contributor; 14 unreviewed PRs; rewrite <2 months old | 🟠 |

---

## 6. The desk/retail verdict, re-examined

### 6.1 The cascade that makes it binary

Missing depth alone (never mind RGB details) ⇒ no VER-10 containment ⇒
VER-13 fail-closed ⇒ every episode FAILs. Missing intrinsics ⇒ no VER-8
calibration block ⇒ stage-0 refusal ⇒ same outcome. This is why ADR-42 is
right that partial rendering doesn't help: **the verifier consumes the sensor
chain atomically.** PR #7/#11's RGB capture, real progress though it is,
changes nothing for an attested desk run until depth + intrinsics + wrist
mount exist.

### 6.2 The concrete parity list (this is the ask, if Sébastien wants desk workloads reachable)

In dependency order, with effort as estimated from the current tree (all
kiss3d/viewer-layer except item 5):

1. **Camera model**: perspective camera with settable vertical FOV (or full
   fx/fy/cx/cy), pose read-back, multiple named cameras per scene, render
   target per camera. (kiss3d has FOV internally; it is simply not bound.)
2. **Depth channel**: linearized metric z-buffer readback alongside RGB from
   the same pass. The pipelined-readback machinery from PR #11 already
   exists; this adds a second attachment, not a new pipeline.
3. **Segmentation channel**: flat per-object id render (no lighting) + an id
   map API. Same machinery again.
4. **Link-mounted cameras**: camera pose slaved to a body/link transform each
   step (AISLE composes the mount transform itself; it needs only
   "camera follows link X with offset T").
5. **(Engine, alternative to 2)** a GPU raycast query API would allow a
   depth-camera-as-raycaster (the route Genesis's sensor path takes) — but
   given the render pipeline exists, z-buffer depth is the cheaper route.
6. **Metal correctness**: close issue #5 (while→for sweep or the wgpu fix) —
   independent of sensors but gating for AISLE's primary platform.

Items 1–4 are exactly what "a calibration block convertible to VER-8's v1
conventions" (ADR-42's stated verdict-changer) decomposes into. None of them
touches the physics engine. The honest engineering framing for the meeting:
**this is a viewer/sensor subsystem of perhaps a few thousand lines, not a
product pivot — but it is also unowned, unroadmapped work in a
one-maintainer project**, and AISLE should neither assume it nor demand it;
the right question is whether it aligns with where Sébastien wants Nexus to
go (zealot-style RL stacks will eventually want camera observations too —
today's step-cue oracle is explicitly a placeholder for a RealSense).

### 6.3 What changed since ADR-42 — a candid re-scoring

ADR-42 (2026-08-18) said: physics engine, not simulator; no programmatic RGB.
That was already slightly stale when written: PR #7 (RGB export) merged
2026-07-16, PR #11 (headless, 92 fps) 2026-07. The ADR's *conclusion* survives
— depth/seg/intrinsics/multi-cam remain absent and the fail-closed cascade
holds — but its *rationale* ("different product surface", "not a maturity
gap") is now contestable: the maintainer merged frame export whose PR text
benchmarks against Genesis by name. Recommended posture: keep the NO-GO,
replace the rationale with the enumerated gap list (§6.2), and record what
would reopen #284.

---

## 7. The bench family: where the case inverts

### 7.1 Genesis's measured granular failure (the bar Nexus has to beat)

From AISLE's own powder-spike ADR — all measured, all committed:

| Axis | Genesis measured result |
|---|---|
| Metal MPM determinism | nondeterministic (GPU atomics) **and** ~10% of scoop cases crash with NaN constraint forces |
| CPU MPM | bit-exact but ~7 steps/s at 5k particles (~20× short) |
| Scoop repeatability | CV 87.9% transferred mass; spill ~8× payload |
| Pile realism | **no angle of repose** — 2.7–6.3° flank vs ~30–40° physical; `friction_angle` knob ineffective |
| Domain | bounded box required; escaping particles clamped |

Consequence already absorbed into AISLE: "scenes and verifiers must not
depend on heap geometry." PW-4's honest-scope rule means the replacement bar
is *qualitative granular behaviour + repeatability + throughput*, not
milligrams.

### 7.2 Nexus MPM fit — and the one blocker

Structurally strong fit: GPU Drucker-Prager sand, **sparse open-domain grid**
(no clamp box), **dynamic particle add/remove** (emitters — pouring), CPIC
thin-plate boundaries (a scoop *is* a thin plate; the spike showed CPIC-class
handling is required, since near-surface spawns NaN'd Genesis without it).
Erosion/emitter/heightfield demos exist. Unknown: throughput (no published
numbers — PW-0 measures it) and whether per-particle positions are readable
from Python (needed for the PW-3 mass oracle; `add_particles` is bound,
readback needs verification — a specific question for the meeting).

The blocker is **coupling direction**. AISLE's bench needs, in increasing
order of difficulty:

1. **Rigid→particle** (scoop pushes powder, with true tool velocity): ✅
   exists (one-way coupling; the spike's kinematic-tool-velocity lesson
   applies identically).
2. **Particle→rigid *sensing*** (wrist F/T while the scoop is loaded; FT-1,
   and FT-3's force-limit safety clamp): ❌ — but this does **not** require
   full two-way dynamics. The MPM grid already computes particle–collider
   interaction impulses to enforce boundaries; *accumulating and exposing the
   net wrench per collider* (without applying it) would satisfy FT-1/FT-3 for
   a position/impedance-controlled arm. This is the minimal, well-scoped ask.
3. **Particle→rigid dynamics** (powder mass loading the arm, balance pans
   deflecting): the "full two-ways coupling … in the future" item. Nice for
   fidelity; not required by PW-3 (mass oracle is particle-count) nor BAL-1
   (the balance is a simulated instrument: oracle mass + noise + settling
   model — no force sim involved).

That ordering is the single most useful technical content for the meeting:
**AISLE's bench family needs (2), not (3), and (2) is a readback, not a
solver redesign.**

### 7.3 A concrete PW-0 spike shape for Nexus

Reuse AISLE's existing spike harness semantics: N ∈ {5k, 20k, 50k} sand
particles, steps/s on Metal-WebGPU and CUDA; 20 seeded scripted scoops →
std/mean of transferred particle count; pour into vessel; pile and measure
flank angle. Pass thresholds already exist in SPEC 300. Deliverable: an
ADR mirroring ADR-powder-spike, directly comparable row-for-row against
Genesis's numbers. Estimated effort: days, not weeks, once particle readback
is confirmed — the harness design is already written.

---

## 8. Determinism: the sleeper argument

Line up the three stories:

- **AISLE's need**: CON-5(c) — physics within 1e-6 over a 1 s post-reset
  window; bit-exact replicates *desired* (the constitution had to be amended
  down to statistical outcomes specifically because of the engine).
- **Genesis**: honest docs, but: determinism is opt-in, costs throughput,
  holds per-machine at best, "GPU backends support it only partially," and
  the default perf-dispatch design makes a rollout depend on process history.
  On Metal: unfixable ULP divergence (ADR-26).
- **Nexus/zealot**: the engine itself claims nothing — but the architecture
  (single Rust kernel source compiled by three backends, no runtime kernel
  switching) is what let zealot demonstrate **CUDA ↔ WebGPU bit-exact
  physics** (pose-fingerprint identical; full-buffer gather err 0.0), CPU
  reference verified to float-ε, deterministic injected RNG. Same-backend
  run-to-run bit-identity is repeatedly verified in the nexus PR stream.
  Caveats: these are contributor-verified claims, not engine guarantees;
  Metal is currently outside the club (naga bug); AISLE would want the
  guarantee *documented and tested upstream*.

If Nexus adopted "cross-backend bit-exactness is a supported, CI-tested
property" as a headline feature, it would hold a determinism position that
neither Genesis nor Isaac occupies — and for AISLE specifically it would mean
CON-5 could *tighten* (bit-exact GPU replicates ⇒ layer (c) everywhere,
possibly (d) becoming exact), which strengthens every evidence-integrity claim
the project makes. This is the argument most likely to interest a maintainer
whose brand is correctness-first physics.

---

## 9. Throughput, briefly

- Genesis in AISLE's actual regime (single env + 2 cameras, M3 Metal):
  **0.77× realtime** vs a ≥5× contract target. The store scene runs ~0.1×.
- Nexus at batch scale (contributor benchmarks, RTX 5090): 138k env-steps/s
  at 2,048 robot envs upstream; 5.67M at 16,384 cube envs with PR #24;
  zealot full *training* iterations at 91–99k env-steps/s — 2× behind Isaac
  at N=8,192, ahead at N=2,048.
- Caution against Genesis's own headline numbers in any slide: the "430,000×
  realtime" / "10–80×" claims were publicly corrected (issue #181); use
  AISLE's measured numbers and zealot's like-for-like tables instead.
- Un-benchmarked and decision-relevant: Nexus in AISLE's regime (1 env,
  Franka/SO-101 multibody, 30 Hz dual-camera rendering, Metal). If the §6.2
  gaps ever close, this measurement comes first.

---

## 10. AISLE-side integration cost (either family, any engine)

Issue #283's four items are the entry fee, sized once for any second engine:
composite `env_hash` (core + per-sim), a sim-backend protocol replacing the
Genesis-hardcoded `resolve_sim_identity` and the `cpu|metal|cuda` enum,
per-sim `src/aisle/sims/{genesis,nexus}/` layout, `budget_guard.py`
relocation — landed as **one** attestation discontinuity with the bridging
measurement (re-run M0 + one tier curve, show identity). Plus: `bridge_info`'s
literal `genesis_version` field becomes `sim_version` (contract change),
ADR-24's attested-dist set gains the nexus package, and ADR-33's open question
("should the bridge be inside the fence?") gets sharper with a second bridge.
For the bench family specifically, SPEC 310's FT-1/FT-2 topics are DRAFT and
already engine-agnostic.

---

## 11. Roadmap: Nexus as a physical-AI simulation platform

The analysis above points at a clear direction: a pure-Rust, thin,
genuinely open core with a **Genesis-compatible Python API by profile** —
leveraging the Genesis ecosystem's conventions without chasing its full
surface. This section turns that into a development roadmap. Phases are
ordered so each one is the *credibility precondition* of the next; every
exit criterion is a runnable test, not a demo, so progress is verifiable.

### 11.0 Positioning principles (what "worthy of physical AI" means)

1. **Correctness-first, determinism as brand.** CI-tested bit-exact
   replicates, same-backend and CUDA↔WebGPU — the position neither Genesis
   (perf-dispatch nondeterminism by design) nor Isaac holds. For RL this is
   replayable rollouts and debuggable training; for evidence-driven projects
   like AISLE it is constitutional.
2. **Sensors are simulator citizens.** Cameras, depth, segmentation, and
   force/torque as engine features with calibrated conventions — not viewer
   afterthoughts. This is the line between "physics engine" and "simulator."
3. **Batched-first.** Every feature works at `n_envs = 4096` and is per-env
   addressable (reset, sensors, actuation). RL is the design center, not a
   use case.
4. **Portable, thin, open — the anti-Genesis differentiators.** One wheel, no
   torch dependency, WebGPU/Metal/CUDA/browser from a single kernel source,
   and **no closed tiers** (Genesis's recommended renderer ships from an
   internal package index; Nexus should make openness a stated guarantee).
5. **Compatibility by profile, not by chase.** A Genesis-compatible Python
   layer scoped to a *pinned, conformance-tested* call surface (§11.5), grown
   only when a real consumer needs a call. Full-surface compatibility with a
   weekly-release, VC-funded project is an unwinnable race; a profile with a
   test suite is a deliverable.
6. **Honest benchmarks.** Like-for-like methodology, published configs,
   iteration-equivalent comparisons — explicitly avoiding the failure mode of
   Genesis's contested launch numbers (issue #181).

### 11.1 Phase 0 — Foundations: trust the engine

The bottleneck is review bandwidth, not code: most of this phase exists as
unreviewed PRs.

- Triage the contributor backlog: correctness first (#25 substep-dt, #26
  friction-compliance leak, #27 contact lever arms, #28 prediction distance),
  then batching/perf (#14, #16, #17, #19–#24).
- Close the Metal miscompile (issue #5): the while→for sweep or the wgpu/naga
  fix — plus a macOS CI job so it stays closed. (Metal is AISLE's
  constitutional platform and every Mac laptop's.)
- Determinism CI: same-backend bit-exact replicate tests and CUDA↔WebGPU
  golden-fingerprint tests, promoted from contributor claims to documented
  engine guarantees. AISLE contributes its CON-5 replicate harness.
- Release discipline: tagged GitHub releases, changelog, PyPI wheels for
  `dimforge-nexus3d` on all three platforms.
- Upstream zealot's vendored forks (naga fix, khal/vortx patches) so the
  flagship consumer builds on released crates.

**Exit criteria:** biped training example green on WebGPU + CUDA + Metal;
determinism CI green; zealot builds without sibling patched forks.

### 11.2 Phase 1 — The RL-environment substrate

Generalize what zealot proved from a hand-built stack into engine surface:

- MJCF actuator semantics upstreamed (PR #12): position/motor actuators with
  kp/kv inside the GPU solver, control-vector API, batched link-state
  readback.
- Contact sensing API: true contact pairs and per-link net wrench readback
  (zealot currently synthesizes foot contact from height thresholds because
  "the nexus host API doesn't expose them").
- First-class per-env snapshot/reset (zealot's spawn-template pattern as an
  engine API, not app code).
- GPU raycast scene queries — height probes and lidar-style observations for
  locomotion; also the fallback depth path for §11.3.
- Articulation headroom: lift the effective 32-DOF multibody cap (welded-wrist
  workarounds) toward full humanoids with hands.
- The solver megakernel / env-per-lane work (the stated "closes most of the
  remaining large-N Isaac gap" item, salvaging the #15 tree-sparse finding).
- A reference environment layer: either bless zealot-env as the rsl_rl-tier
  reference or extract a minimal `nexus-env` (obs/action/reset/step over
  batches), plus gym / LeRobot adapters.

**Exit criteria:** a WBC-AGILE-class humanoid task trains at ≥ Isaac
throughput parity for N ≤ 4,096 on upstream releases only, with a documented
RL quickstart.

### 11.3 Phase 2 — Sensor simulation: crossing the simulator threshold

The §6.2 list, promoted from "ask" to roadmap:

- Camera model: settable vertical FOV (or fx/fy/cx/cy), realized pose
  read-back, multiple named cameras per scene, render target per camera.
- Metric z-buffer depth and flat-id segmentation rendered in the **same pass**
  as RGB, one timestamp, using PR #11's existing pipelined readback.
- Link-mounted cameras (pose slaved to a body/link with a fixed offset).
- Calibration export in OpenCV conventions — VER-8 v1 convertible by
  construction.
- Per-env render selection (render env *i*, not just env 0); later, batched
  tiled rendering (Madrona-class) for pixel-observation RL at scale.

**Exit criteria:** AISLE's VER-8/calibration conformance suite passes;
dual-camera 30 Hz at ≥5× realtime single-env (the BRG-2 bar Genesis misses at
0.77×); AISLE reopens desk feasibility (#284) — the recorded reopening
condition is exactly this feature set.

### 11.4 Phase 3 — Granular & manipulation multiphysics (overlaps Phase 2)

- Per-collider MPM **wrench readback** — accumulated particle-boundary
  impulses exposed as F/T *sensing* without dynamics coupling (§7.2). This
  alone unblocks AISLE's bench family (FT-1 wrist F/T, FT-3 force-limit
  safety).
- Python per-particle position/velocity readback (the PW-3 mass oracle).
- CPIC collider-limit lift (16 → scene-scale).
- Full two-way RBD↔MPM coupling (already stated as planned) — fidelity tier,
  not the unblocker.
- Impedance/torque command hooks at the joint level (SPEC 310 FT-2 shape).

**Exit criteria:** AISLE runs PW-0 on Nexus and publishes the ADR
row-for-row against Genesis's measured numbers (throughput at 5k/20k/50k
particles, scoop repeatability CV vs 87.9%, angle of repose emerging where
Genesis produced 2.7–6.3°); the bench-family bridge lands as the first
production Nexus workload.

### 11.5 Phase 4 — Ecosystem & the Genesis-compatible profile

- **`nexus-gs-compat`** (neutral name — API reimplementation of Apache-2.0
  software is fine; implied affiliation is not): a pure-Python shim over
  `nexus3d` implementing the **pinned genesis-world 1.2.x profile** — the
  ~60-call surface AISLE's bridge and scene builder actually touch (init/
  backend, Scene/options, morphs, materials/surfaces/textures, cameras with
  `render(rgb, depth, segmentation)`, entity state get/set with the wxyz→xyzw
  convention, `control_dofs_position`, `build(n_envs)` + `envs_idx`
  addressing, build-time IK). Semantic parity is where the bodies are buried
  — quaternion order, the camera-roll epsilon branch, neutral-pose collision
  pruning — and AISLE's conformance tests already encode exactly these traps.
- dora-rs bridge node — the first dora↔nexus integration (AISLE is
  dora-native; zealot's author maintains dora; natural three-way glue).
- Documentation site with a real user guide; examples gallery; the browser
  demos as the marketing surface.
- An honest cross-engine benchmark suite (MuJoCo, MJX, Isaac, Genesis) with
  published methodology, extending zealot's sim2sim harness pattern.

**Exit criteria:** AISLE's bench bridge runs on Nexus *via the shim* in CI;
docs site live; benchmark report published.

### 11.6 Sequencing rationale

Correctness → RL substrate → sensors → multiphysics → compat: each phase is
worthless without the previous one (a compat shim over an engine with a
friction leak helps no one), and each phase ends in a test someone else can
run. Given dimforge's one-maintainer reality, prioritize **review bandwidth
before features** — the 14-PR backlog is the cheapest unlock in the entire
roadmap, possibly including a second maintainer. AISLE's contributions
(conformance suites, the PW-0 spike, the requirements extraction, a
production workload with published verdicts) are exactly what a young engine
most needs: an adversarial, evidence-publishing user.

---

## 12. What to bring to the meeting

**Asks (ordered by value-per-effort):**

1. *Bench*: per-collider particle-impulse **wrench readback** from the MPM
   grid pass (sensing, not dynamics — §7.2 item 2), and confirmation of
   Python per-particle position readback. These two unblock PW-0.
2. *Bench*: CPIC collider limit (16) — headroom or config?
3. *Platform*: plan for issue #5 (Metal MSL miscompile) — the while→for sweep
   or tracking the wgpu fix; AISLE is constitutionally macOS-first.
4. *Desk (only if Sébastien volunteers interest)*: the §6.2 sensor list —
   FOV+pose camera API, z-buffer depth, flat-id segmentation, link-mounted
   cameras. Framed as "what VER-8 v1 convertibility decomposes into," not as
   a demand.
5. *Determinism*: would dimforge accept cross-backend bit-exactness as a
   documented, CI-tested engine property? (AISLE can contribute the replicate
   harness — it already exists as CON-5 tooling.)
6. *Health*: the 14-PR review backlog — three of them are correctness fixes
   any articulated-robotics adopter needs merged (or refuted) first.

**Offers (what AISLE brings):**

- A spec-grade requirements document (the R1–R76 extraction) and frozen
  conformance tests (calibration-vs-engine, determinism replicates, PW-0
  spike harness) — i.e., a real, demanding, *measured* workload with
  published verdicts, which is what a young engine needs more than stars.
- The PW-0 Nexus spike run and a public ADR directly comparable to the
  Genesis numbers — a credible third-party benchmark of nexus_mpm.
- The dora-rs angle: AISLE is already a dora-native robotics stack; zealot's
  author maintains dora; a nexus-backed AISLE bench bridge would be the first
  dora↔nexus integration — Switzerland-position glue for both projects.

**Recommended AISLE posture going in:** desk/retail stays NO-GO (unchanged
verdict, updated rationale, reopening condition = roadmap Phase 2); bench
family moves from "live option" to "PW-0 spike scheduled, conditional on the
two Phase-3 unblockers (MPM wrench readback, particle readback)"; issue #283
gets re-scoped into the PW-0 plan rather than waiting for it. The meeting's
frame shifts from "asks" to a **shared roadmap proposal** (§11) — the open
question for Sébastien is roadmap appetite and maintenance bandwidth, not
whether the work is worth doing.

---

## Appendix: primary sources

- AISLE: `specs/000,010,020,030,040,300,310`; `docs/decisions/ADR-7, -24,
  -25, -26, -30, -33, -42, ADR-powder-spike`; `tools/env_hash.py`;
  `src/aisle/nodes/dora_genesis.py`; `src/aisle/scenes/pharmacy.py`,
  `physics.toml`; issues #71, #281, #283, #284.
- Nexus: README/CHANGELOG @ v0.5.0; issues #5, #18; PRs #7, #8, #11, #12,
  #13, #15, #16, #24, #25, #26, #27, #28, #31, #32; crates/nexus_python3d/
  README; nexus.dimforge.com.
- zealot: README; docs/explanation.md, benchmarks.md, getting-started.md,
  train-on-5090.md, metal-contact-bug-proposal.md; zealot-env/src/tasks/
  velocity_flat.rs; HF model card (zealot-g1-locomotion).
- Genesis World: docs v1.3.3 (rendering, sensing, coupling, initialization,
  installation); issues #181, #600, #653, #1015, #2044, #3207, #3229;
  Genesis AI 1.0 blog; Stone Tao's benchmark critique (Dec 2024).
