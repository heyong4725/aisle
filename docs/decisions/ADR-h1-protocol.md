# ADR-h1-protocol — H1 composition-experiment protocol (design doc §8.2.4)

Status: accepted (CON-15: §8.2.4 defines what to record but not the
mechanics; interpretations recorded here, revised per the PR #27 review).
Artifact: `tools/h1_protocol.py` (Class A) + tests/unit/test_h1_protocol.py.
Hypothesis under test (§6 H1): a frontier coding agent composes a VALID,
LAUNCHING dataflow for T1 zero-shot ≥80% of attempts, and reaches a
working (>0% success) graph within 3 validate-fix cycles.

## Protocol

1. **Pinned treatment (CON-5).** One commit OID is resolved at protocol
   start and every worktree is detached AT that OID; the results record
   the OID, agent CLI version, explicit model id, prompt sha256, session
   argv, and budgets. Nothing floats with HEAD mid-protocol.
2. **Isolated sessions.** Each attempt gets a fresh worktree OUTSIDE the
   repository (a temp scratch root) — no adjacency to the base checkout
   or sibling attempts. The session composes `graphs/agent_h1.yaml` and
   iterates `harness validate`; a workspace audit (`git status`) records
   every file the session touched beyond the graph as a violation.
3. **Scoring is untouchable by the agent.** The zero-shot artifact is
   the graph CONSUMED BY THE FIRST `harness validate` call, captured by
   a runner-installed shim on the session venv's entry point (race-free;
   an agent that composes but never validates gets its final composition
   as the flagged zero-shot artifact). First and final graphs are scored
   in SEPARATE FRESH worktrees at the pinned OID — one per scored graph,
   so a corrected final never inherits warm caches, run dirs, or ledger
   state — into which only the graph text is copied.
4. **The headline is first-graph valid AND launching** (H1's own words):
   zero-shot = the first snapshot passes the scorer's validate AND its
   8-seed `--tier T1` rollout produces ≥1 episode result. Launch is
   classified explicitly: `launched | refused | stalled | no_episodes |
   invalid | no_graph` — gate refusals and pre-episode crashes are not
   launches. "Working within 3 cycles" uses the FINAL graph (valid,
   ≤3 validate calls, pass@1 > 0). Final == first graphs are scored once.
5. **Validate-fix cycles** = structured command telemetry for BOTH arms:
   claude stream-json tool_use/tool_result pairs; codex `--json`
   `command_execution` items (never text-occurrence counting).
6. **Equal treatment, write-confined.** Both arms: the same wall-clock
   session budget (20 min), the same prompt, explicit pinned models, and
   WRITE CONFINEMENT to the session workspace — claude via a
   `sandbox-exec` profile (writes limited to the worktree/scratch/caches;
   the H1 results tree is read-DENIED except for the current attempt
   artifacts required by the snapshot shim, so prior attempts cannot leak in),
   codex via its native `--sandbox workspace-write` with
   `approval_policy=never --ignore-user-config`. `--no-sandbox` exists
   as a recorded escape hatch if sandbox-exec breaks a CLI. Recorded
   limitations: claude additionally has `--max-turns 50` (no codex
   equivalent; the wall budget binds both), and read access outside the
   denied results tree is not restricted.
7. **Failure attribution.** Agent failures (no graph, invalid, refusal,
   timeout) are COMPLETE records — the measurement. ANY nonzero agent-CLI
   exit that is not the runner's own timeout kill is an infrastructure
   failure (`InfraError` → `runner_errors` → protocol exits nonzero,
   CON-8) — API errors and CLI crashes never contaminate agent
   statistics. Timeout kills the whole process GROUP (agent-spawned
   children included).
8. **Resume merges, same treatment only.** `--start N` REFUSES if the
   existing results' treatment block (commit OID, model, CLI version,
   prompt sha, budgets) differs from the current invocation — mixed
   treatments never masquerade as one experiment. Records merge by
   attempt index; prior runner errors whose attempts were successfully
   re-run are resolved; the aggregate `ok` reflects the RETAINED error
   union, and the summary is recomputed over all records.
9. **Budget accounting is explicit, not campaign spend.** Scoring
   rollouts run `--env-baseline local` (ADR-21: logged in every
   manifest, neither charging nor consulting the campaign ledger — H1 is
   a human-run protocol, not the research campaign), and the results
   report `total_episodes_scored` (one 8-seed rollout per valid first
   graph, plus one per DIFFERING valid final) so the protocol's own
   episode spend is first-class.
10. **No parallelism**: one attempt at a time — rollout scoring must not
   contend for the machine (the T20 orphan-load lesson).

## Outputs

`runs/h1/<agent>/attempt_NN/` (session JSONL and stderr logs, first/final
graph snapshots, validate/rollout reports, record) and
`runs/h1/h1_results_<agent>.json`
with the treatment block and summary (zero-shot valid-and-launching rate
vs the 80% target, working-within-3-cycles, mean pass@1, violations,
timeouts). The committed table lands in `analysis/` once both arms run.

## Execution note

Launching the protocol spawns autonomous headless agent sessions with
permissions bypassed (inside isolated scratch worktrees). It is
therefore run BY A HUMAN from a terminal, not from inside another agent
session:

    uv run python tools/h1_protocol.py --agent claude --attempts 1   # smoke
    uv run python tools/h1_protocol.py --agent claude --attempts 20
    uv run python tools/h1_protocol.py --agent codex  --attempts 20
