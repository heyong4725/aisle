# ADR-h1-protocol — H1 composition-experiment protocol (design doc §8.2.4)

Status: accepted (CON-15: §8.2.4 defines what to record but not the
mechanics; interpretations recorded here). Artifact: `tools/h1_protocol.py`
(Class A). Hypothesis under test (§6 H1): a frontier coding agent
composes a VALID, LAUNCHING dataflow for T1 zero-shot ≥80% of attempts,
and reaches a working (>0% success) graph within 3 validate-fix cycles.

## Protocol interpretations

1. **Fresh session** = one headless agent run (`claude -p` /
   `codex exec`) per attempt, in a DETACHED GIT WORKTREE of the current
   head — full isolation, no cross-attempt contamination, the fleet-mode
   pattern from §8.4.
2. **The session composes and validates only.** The agent gets the
   research contract + the T1 task, writes `graphs/agent_h1.yaml`, and
   iterates `harness validate` until ok. The RUNNER then scores the
   final graph with its own validate + an 8-seed `--tier T1` rollout
   (pass@1). Rationale: §8.2.4's three record fields separate cleanly
   into agent-observable (validate behavior) and runner-scored (launch,
   pass@1); keeping rollouts out of sessions makes attempts cheap,
   comparable, and immune to prompt-driven scoring games.
3. **Validate-fix cycles** = the number of `harness validate`
   invocations the agent makes in its session, counted from the
   stream-json tool-call log; "valid first try" = the first such call
   reporting ok. (Codex arm: coarser text-level counting; its zero-shot
   metric comes from the runner's independent validate of the untouched
   first graph — recorded as a caveat in results.)
4. **pass@1** per attempt = the runner's rollout over seeds 0..7
   (8 episodes), `--no-idea-gate` (a protocol run, not the research
   loop; the flag is manifest-logged as always). The trusted frozen-set
   baseline stays ON: an agent that edits frozen files gets a rollout
   REFUSAL, which is recorded as the attempt's outcome — the no-cheating
   rule is part of what H1 measures.
5. **Failure attribution**: an attempt where the AGENT fails (invalid
   graph, no graph, refusal) is a COMPLETE record — that is the
   measurement. Only runner-infrastructure errors mark the protocol
   itself failed (CON-8: exit 0 iff the protocol ran clean).
6. **Session budget**: 50 turns / 20 min per session, one attempt at a
   time (no parallelism — rollout scoring must not contend for the
   machine, the T20 orphan-load lesson).

## Outputs

`runs/h1/<agent>/attempt_NN/` (session log, graph, rollout report,
record) and `runs/h1/h1_results_<agent>.json` with the summary
(zero-shot rate, working-within-3-cycles count, mean pass@1 — the H1
table). The committed table for the paper lands in `analysis/` once both
agent arms have run.

## Execution note

Launching the protocol spawns autonomous headless agent sessions with
permissions bypassed (inside isolated worktrees). It is therefore run BY
A HUMAN from a terminal, not from inside another agent session:

    uv run python tools/h1_protocol.py --agent claude --attempts 1   # smoke
    uv run python tools/h1_protocol.py --agent claude --attempts 20
    uv run python tools/h1_protocol.py --agent codex  --attempts 20
