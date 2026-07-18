# SPEC 070 — Harness CLIs (rollout, traces, report, registry, trace_check)

Status: DRAFT. Modules under `src/aisle/harness/`. All CLIs obey CON-8.

Rollout runner:
- HAR-1: `harness rollout --graph G --tier T --episodes N --seeds a..b --reset teleport|behavioral [--verifier oracle|realistic|both]` → JSON `{run_id, pass1, pass8, failures:{class:count}, episodes:[...], traces_dir, videos:[...], durations:{wall_s, sim_s}}`.
- HAR-2: Before launching, rollout MUST: verify env hashes vs `tools/env_hash.py --check` (refuse on mismatch, CON-7); run SPEC 060 validate (refuse on !ok); confirm an OPEN idea entry exists for the current git branch (HAR-8) unless `--no-idea-gate` (humans only; flag is logged).
- HAR-3: pass@8 semantics = ENPIRE in-context retries: within one episode, on subtask failure the graph's task-state machine MAY retry ≤8 times, each retry conditioned on the failure; pass@8 counts the episode. NEVER computed as best-of-8 independent episodes.
- HAR-4: Every episode records: dora-record Arrow traces of all topics, overhead video (mp4), episode_result JSON, seed. `runs/<run_id>/manifest.json` includes git_sha, env_hash, platform, graph hash (CON-5).
- HAR-5: Token accounting hook: rollout reads `ANTHROPIC_TOKENS_LOG`/agent-provided counters if present and stores them in the run manifest (best effort; absence is not an error).

Traces:
- HAR-6: `harness traces query --run R --episode E --node N --topic T [--t0 --t1] [--format json|npz]` returns aligned slices; `--summarize` returns per-topic stats (rate achieved, min/max, gaps) instead of data.

Idea tree:
- HAR-7: `harness report log --idea "..." [--parent I12] --expect "+10pp on T1"` appends JSONL to `runs/ideas/<branch>.jsonl` with id, ts, git_sha; `harness report close --id I13 --observed "..." --verdict up|down|flat`.
- HAR-8: An idea is OPEN if logged and not closed. Rollout gate per HAR-2.

Traceability:
- HAR-9: `tools/trace_check.py` scans specs for MUST requirement IDs and tests for docstring citations; exits nonzero listing uncovered MUSTs. CI runs it (CON: CON-2 note — marker unit).

Acceptance: `tests/accept/test_rollout_m0.py::test_expert_t0_50eps` (HAR-1..4 end-to-end; also M0-1), `tests/unit/test_idea_gate.py` (HAR-2,7,8), `tests/unit/test_trace_check_selfhost.py` (HAR-9 — run trace_check on this repo; it must pass).
