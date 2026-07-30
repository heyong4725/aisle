# Physical AI: World Models, World Action Models, and the NVIDIA Stack

**Unified technical architecture guide — v2, July 2026**
*(Merges the January 2026 report "Physical AI and NVIDIA COSMOS" with the July 2026 progress survey. Superseded claims from v1 are updated in place and logged in the changelog at the end.)*

---

## Executive summary

The robotics industry has converged on **world models** as the critical infrastructure for Physical AI — and in the first half of 2026, that convergence produced a second, sharper consensus: the frontier is moving from world models used as *tools* (synthetic data, evaluation) to **World Action Models (WAMs)** used as *policies*, trained on **egocentric human video at scale** and post-trained inside **learned neural simulators**.

The fundamental insight driving the field is unchanged: rather than hand-coding physics or relying purely on simulation, learn predictive models of the world from video at internet scale, then adapt with small amounts of robot interaction data. What changed in H1 2026 is that this thesis now has quantitative teeth:

- **DreamZero** (NVIDIA, Feb 2026) defined the WAM paradigm — jointly predicting future video and robot actions in one model — with roughly 2x the task progress of state-of-the-art VLAs in unseen environments, at real-time 7 Hz control.
- **EgoScale** (NVIDIA, Feb 2026) demonstrated a near-perfect **log-linear scaling law for dexterity (R² = 0.998)** against egocentric human video hours, with less than 0.1% robot data in the training mix.
- **DreamDojo** (NVIDIA, Feb 2026) delivered an open, action-conditioned neural simulator whose policy rankings correlate with real-world outcomes at **Pearson r = 0.995**.
- **ENPIRE** (NVIDIA GEAR + CMU + UC Berkeley, June 2026) demonstrated **physical auto-research**: fleets of frontier coding agents (Codex, Claude Code, Kimi Code) given only a high-level goal, autonomously running the full research loop — reset, rollout, verify, analyze, rewrite code — on real robots, reaching 99% success on contact-rich tasks like GPU insertion.
- **ASPIRE** (NVIDIA, July 2026) added a complementary agentic axis: coding agents that debug robot programs and distill fixes into a compounding skill library.
- **Cosmos 3** (May 2026) unified NVIDIA's previously separate model families into a single open **omnimodel** spanning text, image, video, audio, and action.

For software architects, the NVIDIA stack (Cosmos + Isaac + GR00T) remains the most integrated, production-oriented starting point, with permissive commercial licensing — and for the first time the open path is arguably *ahead of* closed alternatives. Viable open ecosystems also exist around Hugging Face LeRobot (now with native NVIDIA integration), Meta's V-JEPA lineage (whose champion, Yann LeCun, left Meta to found AMI Labs), and a fast-growing Chinese open-model ecosystem (Unitree, AgiBot, Ant Group).

---

## 1. Why world models matter for robotics

Physical AI faces a fundamental data problem that world models address. Unlike language models trained on internet-scale text, robot interaction data is dangerous and expensive to collect — exploratory actions can cause real damage, and supervised training requires interleaved observation-action sequences that don't exist naturally. NVIDIA processed **20 million hours of video into 9 trillion tokens** to train the original Cosmos models, a feat that took 14 days on Blackwell GPUs versus an estimated 3.4 years on CPUs.

World models serve as differentiable simulators that predict future environmental states given past observations and proposed actions. This creates four capabilities across the robotics pipeline: **synthetic data generation** without real-world risk; **policy evaluation** before physical deployment; **model-predictive control** that simulates candidate futures to select actions; and **domain transfer** through learned representations rather than hand-tuned simulator parameters.

**What H1 2026 added:** a fifth capability — the world model *as* the policy. Where v1 of this report described world models feeding separate policy models, the WAM paradigm (Section 3) collapses that boundary. Jim Fan (NVIDIA GEAR) frames this as the **second pretraining paradigm**: next-token prediction gave us LLMs; next-world-state prediction is giving us robot foundation models. His "Great Parallel" roadmap deliberately copies the LLM playbook: (1) pretraining on video, (2) action fine-tuning, (3) reinforcement learning — but with RL running inside neural simulators rather than hand-built engines ("Compute = Environment = Data").

NVIDIA's strategic positioning still reflects hardware-software vertical integration: models run optimally on NVIDIA GPUs, integrate natively with Isaac Sim/Lab, and deploy via NIM microservices to Jetson edge devices — now extended downward into open reference *hardware* (Section 5).

---

## 2. The Cosmos platform: from three families to one omnimodel

### 2.1 The Cosmos 2.x generation (2025 baseline — still deployed, now legacy-track)

Through early 2026, Cosmos comprised three complementary model families:

- **Cosmos-Predict** — future world-state generation from text/image/video; 2B–14B parameters in diffusion (Latent DiT with 3D patchification) and autoregressive variants; up to 121 frames at 720p; autoregressive streaming at 806 tokens/s on 8x H100.
- **Cosmos-Transfer** — sim-to-real domain transfer via ControlNet-style conditioning, converting segmentation/depth/edge maps into photorealistic video; Transfer 2.5 is 3.5x smaller than its predecessor with better physics accuracy.
- **Cosmos-Reason** — vision-language physical reasoning (Qwen3-VL-based, 2B/8B), used for annotation, planning critique, and video search; available as NIM microservices.
- **Cosmos Tokenizer** — the architecturally significant substrate: 3D causal convolutions with causal temporal attention; continuous (diffusion) and discrete FSQ (autoregressive) variants; compression up to 2048x (8×16×16) with 64K vocabulary — 8x more compression than prior SOTA at +4 dB PSNR.

A May 2026 refresh (Predict 2.5, Transfer 2.5, Reason 2) shipped with Agility, Figure, Skild, Uber, and World Labs as launch partners. The Cosmos platform has surpassed **2 million downloads**. Cosmos-Predict 2.5 also became the backbone of DreamDojo (Section 4).

### 2.2 Cosmos 3: the open omnimodel (May 31, 2026)

At GTC Taipei, NVIDIA released **Cosmos 3**, its first fully open *omnimodel*: a single architecture handling **text, images, video, ambient audio, and action sequences natively, in both directions**. Two variants shipped immediately on Hugging Face under the OpenMDW 1.1 license (commercial use permitted):

- **Cosmos 3 Nano** — 16B (8B reasoner + 8B generator), for real-time robotics inference.
- **Cosmos 3 Super** — 64B (32B reasoner + 32B generator), for datacenter-scale synthetic data generation, physical-reasoning research, and post-training smaller robot models.
- **Cosmos 3 Edge** (2B) announced for later release.

Architecturally, the reasoner+generator split absorbs the old Predict/Reason division into one model line and telegraphs the industry's likely endpoint: video-native dynamics fused with language-native reasoning. Domain verticals extend the family — **Cosmos-H** (healthcare/surgical world models, used by CMR Surgical and J&J MedTech) launched at GTC in March.

### 2.3 What's actually open on GitHub / Hugging Face

The nvidia-cosmos organization's repositories remain Apache 2.0 (code) with weights under NVIDIA open licenses; the 2.x inventory described in v1 (predict2.5, transfer2.5, reason2, cosmos-rl, cosmos-curate, tokenizers, cookbook) remains available, now joined by Cosmos 3 weights, **DreamDojo** (2B and 14B), and **ASPIRE**. Hardware guidance from v1 still holds for the 2.x line: ~32GB VRAM for 2B Predict inference, 64GB+ for 14B, multi-GPU (4–8x 80GB) for post-training; Python 3.10, CUDA 12.8+, Docker via NVIDIA Container Toolkit as the production path. Cosmos 3 Super targets Hopper/Blackwell datacenter deployment.

---

## 3. The architecture shift: World Action Models

The defining conceptual development of 2026 is the **World Action Model**, introduced in NVIDIA's February paper *"World Action Models are Zero-shot Policies"* (arXiv 2602.15922) with **DreamZero**.

**The VLA critique.** Generalist robot systems of 2024–25 (GR00T N1.x, π0.x, Figure Helix, Gemini Robotics) are Vision-Language-Action models: VLMs with action heads. They inherit internet-scale *semantic* knowledge but are trained to map observations and instructions to actions without explicitly modeling spatiotemporal physical dynamics — "head-heavy" architectures, in Jim Fan's phrase, good at nouns and weak at verbs. He has argued publicly that VLM pretraining objectives (VQA-style benchmarks) are misaligned with control, and that there is "no reason to believe VLA performance will scale with VLM parameters."

**The WAM formulation.** DreamZero is a 14B **Joint Video-Action Diffusion Transformer** built on a pretrained video diffusion backbone, trained with a flow-matching objective to predict future latent video tokens *and* robot actions jointly in a single forward pass. World modeling and action prediction are not sequential stages; the joint objective forces the policy to inherit the video backbone's physics priors.

**Results:**
- **62.2%** average task progress on seen tasks in unseen environments vs. **27.4%** for SOTA pretrained VLAs.
- **39.5%** average progress on fully unseen tasks (zero-shot).
- **42%** relative improvement in cross-embodiment transfer using video-only demonstrations — no action labels for the new robot.
- **DreamZero-Flash**: decoupled noise schedules and asynchronous execution yield **7 Hz real-time closed-loop control**, retiring the "world models are too slow to act" objection (v1 cited V-JEPA 2 at 16 s per planning step as the fast end of the field; that comparison is now obsolete).

**Trajectory.** GR00T N2 (previewed at GTC March 2026, shipping end of 2026) is built on the DreamZero WAM architecture and ranks #1 on the MolmoSpaces and RoboArena generalist-policy leaderboards; NVIDIA projects it will more than double VLA success rates on novel tasks in unfamiliar environments. A fast-growing academic literature (OA-WAM, EA-WM, Cosmos-Policy, and others) is extending the paradigm. Hybrid WAM+VLA designs are widely expected to dominate — Cosmos 3's reasoner+generator architecture is the clearest signal.

---

## 4. Data and training: scaling laws arrive in robotics

### 4.1 The v1 baseline: video-scale training infrastructure

The engineering fundamentals from v1 remain valid. Open-Sora 2.0's documented **$200K** commercial-grade video-model training run (200 H200s, three stages, 38% MFU) is still the reference cost point. Data curation still follows hierarchical filtering (duration/bitrate/fps/aspect preprocessing; aesthetic, motion, blur, OCR, and jitter scoring) with dense VLM captioning across six aspects. Diffusion-vs-autoregressive trade-offs stand: diffusion for fidelity at 20–100 denoising steps; autoregressive for streaming with error accumulation; hybrids like CausVid distill bidirectional diffusion into 4-step autoregressive generation (9.4 FPS single-GPU, 84.27 VBench). Memory optimization patterns (ZeRO-2 + context parallelism, mixed precision through FP8, activation checkpointing) are unchanged. DreamZero-Flash's decoupled noise schedules extend this hybrid-acceleration lineage to action models.

### 4.2 What changed: EgoScale and the dexterity scaling law

v1 identified the **data collection bottleneck** as the field's fundamental constraint, contrasting V-JEPA 2's 62 hours of robot data with LLM-scale corpora, and identified Tesla's fleet and Figure's manufacturing scale as strategic moats. **EgoScale (Feb 2026) materially changed this analysis.**

The recipe: pre-train on **20,854 hours of in-the-wild egocentric human video** (zero robot data), predicting hand joints and wrist poses; mid-train on ~50 hours of high-precision mocap plus **4 hours** of robot teleoperation with Sharpa dexterous hands — under 0.1% of the training mix. The unified action space is relative wrist motion plus retargeted 22-DoF finger actions; human-humanoid kinematic similarity makes direct retargeting work without learned embeddings.

Results: a humanoid with 22-DoF hands assembling model cars, operating syringes, sorting cards, and folding shirts — including one-shot learning of new folding strategies from a single test-time demonstration. The headline: a **near-perfect log-linear scaling law (R² = 0.998)** between human-video volume and action-prediction loss, where loss directly predicts real-robot success. 1–2K hours overfits; 10–20K hours yields stable, continuing improvement.

**Strategic consequence:** the scaling axis moves from robot-hours to human-hours. Teleoperation — capped by the 24-hour day and unreliable robot hardware — is being displaced as the primary data source by **sensorized human data**: UMI-style handheld grippers, head-mounted cameras, and prospectively consumer wearables. v1's claim that Tesla's fleet and Figure's factories were the decisive data moats needs revision: the manipulation-domain flywheel is egocentric capture, and it's an open race that consumer-hardware companies could enter overnight. (v1's observation about 1X learning from human internet video is vindicated — EgoScale supplied the measurement 1X's thesis lacked.)

---

## 5. Simulation: from Isaac Sim to Simulation 2.0

### 5.1 Isaac ecosystem (updated)

The classical sim-to-real pipeline from v1 remains the production workhorse, with upgrades. Isaac Sim generates physics-accurate synthetic data (PhysX + RTX path tracing); Omniverse Replicator exports directly to Cosmos Transfer format; domain randomization (visual, physical, sensor, environmental — with ADR curricula) remains standard; Omniverse NuRec neural reconstruction builds digital twins from smartphone capture. **Isaac Lab 3.0** entered early access at GTC (March 2026) on the new **Newton physics engine 1.0** and PhysX SDK, adding multiphysics simulation and markedly better dexterous-manipulation support at DGX scale, alongside **Isaac Lab-Arena** for large-scale policy evaluation and **OSMO** for cloud-native workflow orchestration. The **Physical AI Data Factory Blueprint** (Cosmos + OSMO, offered turnkey on Azure and Nebius) packages curation → augmentation → evaluation as a reference architecture: "compute is data."

### 5.2 DreamDojo: the neural simulator (Feb 2026)

**DreamDojo** is the discontinuity: an open-source, interactive world model that takes continuous robot motor controls and generates the future in pixels — "no engine, no meshes, no hand-authored dynamics." Built on Cosmos-Predict 2.5, released as 2B and 14B variants (pretrained on 256 H100s), and trained on **DreamDojo-HV**: 44,711 hours of egocentric human video, 6,015 tasks, 1.135M trajectories — 96x more skills and 2,000x more scenes than the most diverse public robot datasets. Because it learns physics relationally from diverse human video rather than memorizing environments, it generalizes to unseen scenes and objects.

Distillation brings inference to **10.81 FPS**, enabling three production-relevant applications:
1. **Live teleoperation inside the dream** (VR headset driving a virtual robot).
2. **Policy evaluation**: simulated success rates correlate with real-world results at **Pearson r = 0.995** — checkpoint ranking without physical deployment. Given the field's benchmarking crisis (Section 8), this may be DreamDojo's most valuable near-term function.
3. **Model-based planning**: parallel action-proposal rollouts (+17% success on a fruit-packing task).

The near-term architecture is hybrid: Isaac Sim/Lab for structured, contact-rich RL and verified physics; DreamDojo-class neural simulators for diversity, evaluation, and long-tail scenario generation — converging toward Fan's "Physical RL" stage where policies train in dream space with periodic real-world grounding.

---

## 6. Agentic robotics: physical auto-research (ENPIRE) and skill libraries (ASPIRE)

Two GEAR-lab releases in June–July 2026 opened an axis orthogonal to model scaling: putting **fleets of frontier coding agents** — the same Claude Code / Codex-class agents that transformed software engineering — in charge of robotics itself. ENPIRE automates the *research loop*; ASPIRE automates the *skill accumulation loop*. Together they are the first concrete instantiation of the "physical auto-research" stage in Jim Fan's roadmap — the capability he predicts culminates by 2040, arriving in embryonic form in 2026.

### 6.1 ENPIRE: agentic robot policy self-improvement in the real world (June 17, 2026)

**ENPIRE** (NVIDIA GEAR, CMU LeCAR, UC Berkeley; arXiv 2606.19980) is a harness framework that lets coding agents conduct the entire robotics research cycle on physical hardware. The team's conjecture: coding agents already automate algorithm search in digital environments; the missing abstraction for robotics is a **repeatable physical feedback loop** — reset the scene, execute a policy, verify the outcome, refine the next iteration. ENPIRE instantiates that loop with four modules (the acronym):

- **EN — Environment**: automatic reset and verification interfaces the agent can call. The robot itself returns the task to a randomized initial state (e.g., for GPU insertion: pick up the card wherever it landed, return it to a pre-insertion pose, unplug it from the board) and confirms the reset succeeded. Auto-evaluation scores each trial without human judgment — for zip-tie insertion, a detector boxes the head and strap, SAM-3 resolves part masks per camera, each view independently judges whether the strap passes through the head, and per-camera verdicts fuse into a binary reward.
- **PI — Policy Improvement**: agents generate and revise policy code from rewards, videos, traces, and failure cases, across multiple regimes — heuristic learning, tool calling, behavior cloning, offline RL, online RL, code-as-policy.
- **R — Rollout**: budgeted physical trials on one or many robots in parallel, preserving state, action, video, and result for audit.
- **E — Evolution**: agents analyze logs, consult literature, compare hypothesis branches (shared via Git — one branch per agent, one node per idea), reuse successful recipes, prune ideas that fail on hardware, and improve the training infrastructure itself.

**The operating model** is exactly the "goal-driven agent fleet" pattern: eight coding agents were dropped into a robot fleet with GPU compute and a generous token budget and given a simple objective — solve the task as fast as possible, keep robots busy but safe, don't waste compute. Humans then largely withdrew; researchers reviewed what the agents produced overnight.

**Results:**
- **99% pass@8 success** on contact-rich dexterous tasks: Push-T, pin-box organization, zip-tie tying/cutting with a cutter tool, and **seating a GPU into a motherboard PCIe slot**. (pass@8 here means up to 8 *in-context* retries per subtask within one long-horizon rollout, each conditioned on prior failures — measuring emergent retry-and-recovery, not sampling luck; a 13% policy stays ~13% under this metric.)
- **Agent comparison (AutoEnvBench)**: the three harnessed agents were **Codex (GPT-5.5), Claude Code (Opus 4.7), and Kimi Code (Kimi K2.6)**, tracked on research progress over wall-clock time rather than just final success. A telling sim-to-real datum: on Push-T, all three agents solved the task in simulation, but two of three initially failed on real hardware — friction, object dynamics, and sensor noise that simulation didn't capture. ENPIRE's answer is to make the *real world* iterable rather than the simulator more faithful.
- **Fleet scaling ("physical scaling law")**: 1 → 4 → 8 agent teams reach success progressively faster, with two new efficiency metrics — **Mean Robot Utilization (MRU)** and **Mean Token Utilization (MTU)**. The costs of scale are candidly reported: robots idle (often ~half the time) while agents read logs, write code, or wait on LLM backends; larger teams spend growing token budgets summarizing each other's branches, so token-to-success grows super-linearly with fleet size even as wall-clock time falls.
- Simulation evaluation in RoboCasa (kitchen manipulation) separates agent research behavior from hardware throughput and confirms recipe transfer.

**Why it matters:** robotics research has been throttled by a constraint software never had — every failed trial needs a human to reset the scene and judge the outcome, capping iteration at tens of trials per day regardless of algorithm speed. ENPIRE turns real-world robot learning into "a controllable optimization procedure that agents can manage" — continuous integration where the test runner has motors and grippers. Jim Fan's framing: "AutoResearch in the physical world for the first time." An open-source release is planned, which would let universities and even hobbyists stand up self-driving robot labs.

**Honest limits:** results are on bounded lab tasks with purpose-built reset/verification interfaces; constructing the EN module is itself nontrivial engineering per task (though the paper notes reset is often *easier* than the main task); and the token economics — costs scaling faster than fleet size — are the open question for industrial-scale deployment.

### 6.2 ASPIRE: the self-evolving skill library (July 1, 2026)

**ASPIRE** adds the complementary loop: **agentic, code-as-policy control with compounding skills**.

Robot behavior is expressed as executable programs (Code-as-Policy). Coding agents observe multimodal sensory traces from simulation and real robots, run **evolutionary search over control programs**, and — critically — automate the human robotics engineer's debugging loop: replay execution, inspect perception outputs and trajectories, localize the failure (perception vs. grasp vs. planning vs. recovery), repair the program, and **distill the fix into a persistent skill** that later tasks invoke directly. Prior Code-as-Policy systems knew only that a task failed and discarded their fixes; ASPIRE keeps both the diagnosis and the remedy.

Results: dual-arm handover success improved from **20% to 92%** through iterative repair; in long-horizon generalization tests, success on novel tasks climbed from near zero to **31%** as the library grew. Fan frames it as a new continuous-learning paradigm: training shifts from gradient descent to skill refinement; the training artifact shifts from weights to a growing library of sensorimotor skills; distributed training becomes a swarm of agents practicing different skills and merging experience. "A robot solving its 100th task is no longer as clueless as solving its first."

### 6.3 Architectural implication

Taken together, ENPIRE and ASPIRE show the deployed robot brain becoming a *system*: a WAM/VLA policy, an agentic outer loop (research and repair), a versioned skill store, physical CI infrastructure (auto-reset, auto-verification, budgeted rollouts), and full-fidelity trace capture (state/action/video/result per trial, preserved for audit and replay). This elevates the middleware layer — dataflow runtimes, skill and experiment registries, multimodal trace capture and replay, hot-swappable nodes, Git-mediated agent coordination — from plumbing to strategic infrastructure. Legacy ROS-era assumptions fit this poorly; runtimes designed for low-latency dataflow and replayability (e.g., dora-rs-class frameworks) map directly onto ENPIRE's EN/R modules and ASPIRE's skill execution requirements. Notably, ENPIRE's biggest reported inefficiency — robots idling while agents think — is a *scheduling and orchestration* problem, i.e., a middleware problem, not a model problem.

---

## 7. Deployment patterns span cloud to edge

NVIDIA's reference "three-computer solution" is unchanged and now fully shipping: **DGX** for training, **RTX PRO servers** for Omniverse simulation and synthetic data, and **Jetson AGX Thor** for on-robot inference — with OpenUSD and NIM microservices maintaining format compatibility across stages.

Cloud/datacenter serving still centers on Triton Inference Server (dynamic batching, TensorRT/PyTorch/ONNX, HTTP/gRPC), with perception → world model → policy ensembles, Prometheus-driven Kubernetes autoscaling, and TensorRT in-flight batching, paged KV caching, and FP8/INT4 quantization.

Latency budgets by domain remain: **sub-10 ms** at 1 kHz for high-precision control loops; **sub-30 ms** at 100 Hz for general manipulation; **sub-100 ms** at 30–60 FPS for visual reasoning. TensorRT Edge-LLM's C++ interface removes Python overhead for direct robotics integration. Note where the new models sit against these budgets: DreamZero-Flash's 7 Hz (~140 ms) closed-loop control suits deliberative manipulation layered over faster low-level controllers — the System 1/System 2 split (cf. Figure's Helix: 7–9 Hz planning over 200 Hz control) is now the standard decomposition.

Edge: **Jetson AGX Thor** (2,070 FP4 TFLOPS, 128GB unified memory; 7.5x Orin's AI compute at 3.5x efficiency) runs 7B–13B models locally — sized, not coincidentally, for Cosmos 3 Nano-class and GR00T N-class deployment. JetPack 7 ships CUDA 13 with Blackwell optimizations, TensorRT with FP4, and Isaac ROS for ROS 2. CES 2026 added the **Jetson T4000** module at 4x greater energy efficiency for smaller platforms. A new hardware tier appeared below the stack: the **Isaac GR00T Reference Humanoid Robot** (announced June 1, GTC Taipei; shipping late 2026) — Unitree H2 Plus chassis (~6 ft, ~150 lb, 75 DoF), Sharpa Wave tactile five-finger hands, Jetson AGX Thor T5000, and the open Isaac/Cosmos/ROS software stack — an "IBM PC reference design" for humanoids, with a Unitree G1 reference workflow available immediately.

---

## 8. The competitive landscape

**Google DeepMind** runs both lanes. Gemini Robotics (VLA on Gemini 2.0, with 1.5 adding embodied reasoning, thinking-before-acting, and motion transfer in Oct 2025) continues; Genie 3 (24 fps/720p interactive worlds) now powers consumer-facing Project Genie and has been grafted onto **Waymo's** sensor stack for long-tail driving simulation. At I/O 2026 Google announced **Gemini Omni**, fusing Veo, Genie, and Gemini reasoning into one multimodal model producing physics-grounded video — the "world model as a feature of the LLM stack" strategy, and a direct hedge against the world-model pure-plays.

**The world-model startups** made Q1 2026 the largest non-LLM funding quarter on record. **Yann LeCun left Meta in November 2025** and founded Paris-based **AMI Labs**, closing a **$1.03B seed at a $3.5B valuation** (March 2026; Europe's largest seed round; NVIDIA, Samsung, Toyota Ventures, Bezos Expeditions among backers) to pursue JEPA latent-space world models — research-first, years-scale timelines, open science. **Fei-Fei Li's World Labs** closed ~$1B at ~$5B on Marble's commercial traction. The architecture war is explicitly three-way — latent-space JEPA vs. generative pixels (Genie/Cosmos/DreamDojo) vs. VLA policies — with all three marketed as "world models." (v1's V-JEPA 2 analysis stands as the JEPA lineage's technical foundation; note that its champion and momentum have moved from Meta to AMI Labs, and its 16 s/step planning latency has been leapfrogged by WAMs.)

**Tesla** retains unmatched fleet-scale driving data, the FSD data engine, and 35K+ H100 training capacity, and Optimus Gen 3 (22-DoF hands, FSD-derived networks) targets a Summer 2026 low-volume Fremont ramp — but the humanoid program's credibility lagged in H1: repeated teleoperation flags on 2024–25 demos (Bloomberg, The Verge, Electrek), the June 2025 departure of program lead Milan Kovac, hand/arm reliability pauses, and slipped dates. Structural advantages (silicon, training stack, manufacturing scale) remain real; deployed public evidence remains thin.

**Figure AI** shipped **Helix 02 (January 2026)**: a ~10M-parameter network replaced ~109,000 lines of hand-engineered C++ balance code, and the system completed a 4-minute, 61-action dishwasher-loading task end-to-end without resets. Figure 03 (Oct 2025: 35-DoF hands, 2x frame rate, System 1/System 2 Helix at 7–9 Hz / 200 Hz) is in production at BotQ (~1 robot/hour; up to 12K/year capacity). The deployment-economics signal: the first BMW industrial use case took 12 months to stand up; the second paying customer took 30 days. Valuation ~$39B after an OpenAI-led round.

**1X Technologies** moved from announcement to delivery: **NEO began shipping to US homes in 2026** at $20K or $499/month — the first consumer humanoid — with the honest caveat that unknown tasks may fall back to scheduled remote "Expert Mode" teleoperation (which doubles as the data flywheel). Their 1XWM world model (Jan 2026) and human-video-first training thesis are now the industry mainstream.

**Agility Robotics** is the operational-evidence leader: 100K+ totes moved at GXO, Toyota and Mercado Libre added, **$300M+ in contracted Digit v5 orders**, and a **~$2.5B SPAC announced July 5, 2026**.

**China leads on volume.** Unitree shipped 5,500+ humanoids in 2025 and AgiBot 5,168 — together over 80% of global installations — with roughly 13,000–16,000 total units shipped industry-wide in 2025 and TrendForce forecasting **>50,000 in 2026 (~700% YoY)**. Unitree's G1 ($13.5–16K) and R1 ($5,900) collapsed hardware entry pricing; Unitree open-sourced UnifoLM-VLA-0 in January; Ant Group's LingBot-VLA (20K hours of real dual-arm data, fully open) anchors a parallel Chinese open-model ecosystem. NVIDIA expanded robotics hiring across Beijing, Shanghai, and Shenzhen — and its reference humanoid rides a Unitree chassis. The emerging equilibrium — American models on Chinese bodies — is economically natural and geopolitically unstable.

**Deployment reality check.** Pilots are real (BMW, Mercedes, GXO, Amazon), dedicated production lines run on three continents, and costs are falling on trend — but **no deployment has publicly crossed the threshold of unsupervised operation in an unstructured environment**; every one keeps a human at the exception boundary. v1's caution stands: every robot company can show impressive videos; sustained diverse real-world operation is the hard part.

---

## 9. Open-source ecosystem

**LeRobot (Hugging Face)** consolidated its position as the community standard (12K+ stars; imitation/RL/VLA implementations; $100 SO100 arms through ALOHA to Unitree G1), and gained first-party weight: NVIDIA is integrating Isaac and GR00T directly into LeRobot, bridging its claimed 2M robotics developers with Hugging Face's 13M builders. **SmolVLA** (450M params, consumer-hardware VLA) remains the efficiency reference point. Open datasets (AgiBot World's 1M+ trajectories, Droid, Open X-Embodiment) plus MuJoCo continue to make the open toolchain surprisingly complete — now extended by open *world-model* assets: DreamDojo weights and the DreamDojo-HV dataset lineage, ASPIRE, Cosmos 3 under OpenMDW 1.1, and Unitree/Ant open VLAs. The open stack is no longer merely "sufficient without vendor lock-in" (v1's framing); on several axes it now leads the closed alternatives.

---

## 10. Current limitations and open challenges (updated)

**Partially resolved since v1:**
- *Real-time world-model inference.* v1: world models were 30x too slow for control (V-JEPA 2 at 16 s/step). Now: DreamZero-Flash at 7 Hz and DreamDojo at 10.8 FPS make world-model-in-the-loop control and simulation practical, with fast low-level controllers underneath.
- *The data bottleneck.* v1 called action-conditioned data the fundamental constraint. EgoScale's scaling law converts it from a research problem into a capital-allocation problem — egocentric human data is cheap, abundant, and predictably useful. The bottleneck moves downstream to capture logistics and curation.

**Still open, and newly sharpened:**
- *Temporal consistency and physics fidelity.* Generative world models still drift over long horizons and violate physics subtly; policies trained purely in dream space will learn to exploit model errors (the reward-hacking analogue). Continual real-world grounding remains mandatory.
- *Reliability drift.* 1X's shirt-folding model degrading over 50 days from environmental drift (v1) remains emblematic; ASPIRE-style continuous repair is a promising but unproven countermeasure at fleet scale.
- *Hardware reliability as the software bottleneck.* Fan's own assessment: the most advanced AI has not saturated current hardware — "the body's capabilities exceed the brain's command capabilities" — while overheating, motor failures, and firmware faults cap iteration speed.
- *Benchmarking.* Fan called the state of robotics benchmarking an "epic disaster": no MMLU/SWE-Bench equivalent, per-press-release SOTA claims, and demo videos cherry-picked from hundreds of attempts. RoboArena, MolmoSpaces, Isaac Lab-Arena, and DreamDojo-based evaluation are the early fixes; expect neural-sim evaluation to become the field's CI pipeline.
- *Safety certification.* Still no established framework for general-purpose robots in unstructured environments. The most likely forcing function is now visible: medical robotics (Cosmos-H/GR00T-H under FDA, ISO 13485, IEC 62304 regimes) will import near-zero-tolerance software discipline into the Physical AI stack.

---

## 11. Market trajectory and adoption outlook

Analyst projections have firmed rather than changed shape: Physical AI growing from ~$5B (2025) toward $50–85B by 2034–35 at 31–35% CAGR; the humanoid segment specifically from $2.03B (2024) toward ~$13B by 2029 and up to ~$50B by 2035. Investment continued past v1's $16B+ (first three quarters of 2025) marker: Figure's $39B valuation, Agility's SPAC, 1X at ~$10B, and $2B+ into world-model pure-plays (AMI Labs, World Labs) in Q1 2026 alone. Unit volume is the newest hard number: ~13–16K humanoids shipped in 2025, >50K forecast for 2026, with China supplying the overwhelming majority.

Deployments still concentrate in structured environments — logistics (Digit at GXO/Amazon), automotive manufacturing (Figure at BMW, Apptronik at Mercedes), healthcare — with the home now opening via 1X NEO. The demonstration-to-reliable-deployment transition remains the central challenge.

**Updated architectural guidance** (superseding v1's closing recommendations):
1. **Hybrid sim-real workflows** — unchanged, but now three-tier: classical sim (Isaac) + neural sim (DreamDojo-class) + minimal real fleets for grounding.
2. **Egocentric data pipelines** — new first-class investment: capture hardware, retargeting, curation, and rights management for human video, not just robot logs.
3. **Evaluation infrastructure as a product** — neural-sim policy ranking and standardized benchmarks are where iteration speed now comes from; treat them like CI/CD.
4. **Multi-model pipelines → agentic systems** — plan for policies composed with agentic repair loops and versioned skill libraries (ASPIRE-style), which demands dataflow runtimes with tracing, replay, and hot-swap.
5. **Edge-cloud tiering** — unchanged: Thor-class edge inference, centralized training and world-model-heavy simulation.

---

## 12. Future direction — assessment and predictions

1. **"RIP VLA" is ~70% right.** The WAM generalization margins are too large to be tuning artifacts, and the VLM-objective-misalignment critique is sound. But semantic reasoning, task decomposition, and instruction following are what video backbones lack — the endpoint is hybrid ("WAM body, VLM head"), and Cosmos 3's reasoner+generator split already is that hybrid. Watch whether latent-space prediction (JEPA) beats pixel generation for *control* while pixel generation retains data-generation and evaluation roles; that would partially absorb AMI Labs' thesis into the incumbents before AMI ships.

2. **EgoScale is the most consequential result of H1** — more than DreamZero — because a scaling law against a ~$1K-per-collector data source turns robotics progress into a capital-allocation race. Expect an "Internet of Hands" land grab in consumer wearables; Meta's and Apple's installed bases make them accidental robotics-data companies. The moat analysis in v1 (Tesla fleet, Figure factories) shifts accordingly.

3. **Neural simulators eat evaluation before they eat RL.** DreamDojo's r = 0.995 policy-ranking correlation attacks the field's true bottleneck (trustworthy iteration), while dream-space RL will fight world-model exploitation for longer. Standardized neural-sim evaluation becomes the de facto CI for robot policies within ~12 months.

4. **The agentic layer is underrated — and ENPIRE is its proof of concept.** ENPIRE and ASPIRE mark the collision of coding-agent tooling (Claude Code, Codex, Kimi Code) with robotics, and they relocate value into infrastructure: physical CI (auto-reset/auto-verify environments), experiment and skill registries, low-latency dataflow, multimodal trace replay, agent-fleet orchestration. Three specific bets follow. (a) "Agent-operable environment" becomes a product category — the EN module (reset + verification harness) is the expensive, per-task engineering that everyone will want off the shelf. (b) ENPIRE's economics problem (token cost growing super-linearly with fleet size; robots idle ~50% waiting on agents) makes orchestration/scheduling the highest-leverage optimization target — a middleware problem, and a natural, differentiated position for dataflow frameworks like dora-rs (execution substrate for code-as-policy and rollout pipelines; ENPIRE-compatible trace capture/replay; Cosmos/GR00T node integrations). (c) AutoEnvBench-style "research progress per wall-clock hour / per token" becomes a headline metric for frontier coding agents themselves — robotics becomes an eval for Claude Code and Codex, which pulls frontier-lab attention (and pricing pressure) into physical AI.

5. **The market splits into a model layer and a volume layer.** NVIDIA runs the Wintel playbook (open models, open reference hardware, sell compute); China ships the units. American brains on Chinese bodies is the natural equilibrium and a geopolitical fault line — expect export-control friction and a bifurcating open-model ecosystem. With weights increasingly open on both sides, the durable Western moats are compute and data flywheels, not model checkpoints.

6. **Concrete 12-month calls.** GR00T N2 ships and RoboArena becomes robotics' SWE-Bench; at least one major consumer-wearables player announces an egocentric manipulation-data program; the first bounded-cell production deployment with no human at the exception boundary lands by mid-2027 (most likely Agility or Figure); 1X NEO's home fleet is revealed to lean heavily on Expert-Mode teleoperation — which is the data flywheel working as designed, not a failure; and the term "world model" fragments under buzzword inflation into WAM vs. JEPA vs. omnimodel, exactly as AMI's own CEO predicted.

7. **On the "Physical Turing Test in 2–3 years" claim:** plausible for bounded domains (warehouse and kitchen-adjacent tasks indistinguishable from a teleoperator), not for open-world household generality. Fan's longer arc — Physical API (lights-out factories, automated wet labs), then "physical auto-research" by 2040 — is directionally useful as a roadmap even if the dates are marketing-grade.

The convergence of every major AI lab on world models, noted in v1 as a paradigm shift comparable to the transformer, has now produced its first scaling laws, its first real-time policies, and its first billion-dollar pure-play bets. The determining questions for the next 18 months are no longer *whether* world models become the foundation of Physical AI, but which of the three architectures wins the control loop, who owns the egocentric data flywheel, and who builds the runtime for agentic, skill-accumulating robots.

---

## Appendix: Changelog (v1 → v2)

**Superseded / corrected from the January 2026 report:**
- *Cosmos three-family structure (Predict/Transfer/Reason)* → unified by **Cosmos 3 omnimodel** (May 2026); 2.x families remain available as the legacy/production track and as backbones (DreamDojo uses Predict 2.5).
- *"V-JEPA 2 at 16 s/step is 30x faster than Cosmos for planning"* → obsolete as a frontier comparison; **DreamZero-Flash runs closed-loop at 7 Hz** and DreamDojo at 10.8 FPS.
- *"Data collection bottleneck is the fundamental constraint; Tesla fleet and Figure manufacturing scale are the strategic moats"* → revised: **EgoScale's dexterity scaling law** makes egocentric human video the scalable supervision source; the moat shifts to egocentric capture flywheels.
- *"Real-time inference presents ongoing challenges… sacrifice capacity for speed (SmolVLA) or accept latency (V-JEPA 2)"* → largely resolved via distillation and asynchronous execution (DreamZero-Flash, DreamDojo distillation); System 1/System 2 decomposition is now standard.
- *Meta V-JEPA 2 as "most architecturally elegant approach"* → the JEPA lineage's center of gravity moved: **LeCun left Meta (Nov 2025) and founded AMI Labs** ($1.03B seed, Mar 2026).
- *1X World Model announced, NEO at $20K early access* → **NEO is delivering to US homes** ($20K or $499/mo), with Expert-Mode teleoperation fallback.
- *Figure 03/Helix as of Oct 2025* → add **Helix 02 (Jan 2026)**: 10M-param network replacing ~109K lines of C++ balance code; 61-action end-to-end dishwasher task; BotQ at ~1 robot/hour.
- *Isaac Lab (v1-era)* → **Isaac Lab 3.0** on Newton physics 1.0; add Isaac Lab-Arena, OSMO, Physical AI Data Factory Blueprint.
- *GR00T N1.x era* → **N1.6 (CES), N1.7 early access with commercial licensing (GTC), N2 previewed (WAM-based, end of 2026, #1 on RoboArena/MolmoSpaces)**.
- *Market/deployment figures* → add 2025 actuals (~13–16K humanoids shipped; China >80%) and 2026 forecast (>50K); Agility SPAC; AMI/World Labs raises.

**New in v2 (no v1 counterpart):** World Action Model paradigm and DreamZero (§3); EgoScale and the dexterity scaling law (§4.2); DreamDojo neural simulation (§5.2); ENPIRE physical auto-research and ASPIRE agentic skill libraries (§6); Cosmos 3 omnimodel and GR00T Reference Humanoid (§2.2, §7); Gemini Omni; benchmarking-crisis analysis; updated predictions (§12).
