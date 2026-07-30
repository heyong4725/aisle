# SPEC 070 — Harness CLIs (rollout, traces, report, registry, trace_check)

Status: DRAFT. Modules under `src/aisle/harness/`. All CLIs obey CON-8.

Rollout runner:
- HAR-1: `harness rollout --graph G --tier T --episodes N --seeds a..b --reset teleport|behavioral [--verifier oracle|realistic|both]` → JSON `{run_id, pass1, pass8, failures:{class:count}, episodes:[...], traces_dir, videos:[...], durations:{wall_s, sim_s}}`.
- HAR-2: Before launching, rollout MUST: verify env hashes vs `tools/env_hash.py --check` (refuse on mismatch, CON-7); verify the ENVIRONMENT attestation from the same self-verified checker (ADR-24: lock vs baseline blob, `uv sync --locked --check` for the declared extras, no editable/local/unhashed installs in the attested set) — trusted-baseline runs REFUSE on missing or failed attestation (`DIST_DRIFT`), `--env-baseline local` runs record `attested` honestly without refusing; run SPEC 060 validate (refuse on !ok); confirm an OPEN idea entry exists for the current git branch (HAR-8) unless `--no-idea-gate` (humans only; flag is logged).
- HAR-3: pass@8 semantics = ENPIRE in-context retries: within one episode, on subtask failure the graph's task-state machine MAY retry ≤8 times, each retry conditioned on the failure; pass@8 counts the episode. NEVER computed as best-of-8 independent episodes.
- HAR-4: Every episode records: dora-record Arrow traces of all topics, overhead video (mp4), episode_result JSON, seed. `runs/<run_id>/manifest.json` includes git_sha, env_hash, env_fingerprint, env_attested, dist problems, post_run_audit, platform, graph hash (CON-5 as amended by ADR-24). For trusted-baseline runs `env_attested` is FINAL only after the post-run audit (gate-time inventory verified fail-closed by the self-verified checker; the inventory persists as `gate_inventory.json` evidence); dev runs record `post_run_audit: null`.
- HAR-5: Token accounting hook: rollout reads `ANTHROPIC_TOKENS_LOG`/agent-provided counters if present and stores them in the run manifest (best effort; absence is not an error).

Traces:
- HAR-6: `harness traces query --run R --episode E --node N --topic T [--t0 --t1] [--format json|npz]` returns aligned slices; `--summarize` returns per-topic stats (rate achieved, min/max, gaps) instead of data.

Idea tree:
- HAR-7: `harness report log --idea "..." [--parent I12] --expect "+10pp on T1"` appends JSONL to `runs/ideas/<branch>.jsonl` with id, ts, git_sha; `harness report close --id I13 --observed "..." --verdict up|down|flat`.
- HAR-8: An idea is OPEN if logged and not closed. Rollout gate per HAR-2.

Traceability:
- HAR-10: `harness swap --dataflow <name|uuid> --replace <node-id> --with <node.yaml>` hot-swaps a node on a LIVE dataflow (design doc §9.1): it MUST validate the full POST-SWAP graph (every SPEC 060 check, including motion gating and INSTALL_MISSING) and refuse on any error BEFORE any runtime mutation; only then drive `dora node remove` + `node add` (original restored on add failure — the live dataflow is never left without the node); a SUCCESSFUL swap writes the post-swap doc back to the graph file so the next validation sees live reality. Trust anchors (the budget guard, any node executing from the frozen set) are REFUSED — VAL-5's topology check assumes the guard's code is the frozen one. CON-8 JSON with the swap timestamp.
- HAR-11: `harness probe --dataflow <name|uuid> --topic <producer/topic> --for <seconds>` attaches a TEMPORARY read-only inspector node to a live topic and detaches after the window; probes MUST never publish onto existing topics and MUST be excluded from oracle-isolation exemptions (a probe consuming oracle_state is refused, VAL-6 applies).
- HAR-12: Every swap and probe ATTEMPT (including refusals and failed mutations) MUST append a JSONL event (ts, dataflow, node, action, open idea id if any) to `runs/swaps/<branch>.jsonl` — the raw material of the H4 iteration-latency metric (idea-open ts → first episode result after the change, relaunch vs hot-swap).
- HAR-9: `tools/trace_check.py` scans specs for MUST requirement IDs and tests for docstring citations; exits nonzero listing uncovered MUSTs. CI runs it (CON: CON-2 note — marker unit).

Acceptance: `tests/accept/test_rollout_m0.py::test_expert_t0_50eps` (HAR-1..4 end-to-end; also M0-1), `tests/unit/test_idea_gate.py` (HAR-2,7,8), `tests/unit/test_trace_check_selfhost.py` (HAR-9 — run trace_check on this repo; it must pass).
