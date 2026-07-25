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
3. **Scoring is untouchable by the agent.** The runner snapshots the
   FIRST parseable graph during the session (filesystem watcher) and the
   final graph at exit, then scores BOTH in a SEPARATE CLEAN worktree at
   the pinned OID into which only the graph text is copied — an agent
   that rewrites validators, harness code, or anything else in its own
   workspace changes nothing about its score (and earns a violation
   record).
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
6. **Equal treatment.** Both arms: the same wall-clock session budget
   (20 min), the same prompt, bypassed permissions
   (`--dangerously-skip-permissions` / codex
   `--dangerously-bypass-approvals-and-sandbox --ignore-user-config`),
   explicit pinned models. Recorded limitation: claude additionally has
   `--max-turns 50` as a secondary stop; codex has no turn-cap flag, so
   the wall budget is the binding constraint for both.
7. **Failure attribution.** Agent failures (no graph, invalid, refusal,
   timeout) are COMPLETE records — the measurement. Infrastructure
   failures (worktree/uv/CLI crash with no output) raise `InfraError`,
   land in `runner_errors`, and fail the protocol exit (CON-8) — they
   never contaminate agent statistics.
8. **Resume merges.** `--start N` loads the existing results file,
   merges records by attempt index (a re-run index replaces its old
   record), and recomputes the summary over the union.
9. **No parallelism**: one attempt at a time — rollout scoring must not
   contend for the machine (the T20 orphan-load lesson).

## Outputs

`runs/h1/<agent>/attempt_NN/` (session log, first/final graph snapshots,
validate/rollout reports, record) and `runs/h1/h1_results_<agent>.json`
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
