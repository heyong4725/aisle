# ADR-34 — a refused reset is a reply, not a boundary: `reset_refused`

Status: ACCEPTED (issue #195, with #194's payload question). Amends TC-6
(SPEC 010, Class C). Human sign-off required per CON-10.

## Context

Issue #192 moved every episode-state consumer off the bridge's `reset_done`
and onto the reset SERVICE's, because the service is the only endpoint that
answers on all three routes (teleport relay, behavioral success, behavioral
exhaustion). That was right, and it had a side effect nobody chose: the
service also answers REFUSED requests (ADR-8), so refusals started arriving
on the topic every consumer treats as the episode boundary.

Two consumers then needed a special case, added one at a time:

| consumer | what it had to add | what happens without it |
|---|---|---|
| `nodes/budget_guard.py` | skip re-referencing velocity/hold state to home | the robot is NOT at home after a refusal; the next command is clamped against a false origin, permitting a larger real jump than the limit allows |
| `nodes/label_reader.py` | skip `on_reset_done` | a refusal carries no `sim_time_ns`, so the unfenced branch clears a LIVE read request and hangs the tour with no downstream symptom |

Both filter on `metadata["error"]`. Two independent consumers deriving the
same rule from scratch is the signal that the topic, not the consumers, is
wrong. Every future consumer of the boundary has to remember the same
filter, and forgetting is silent — the failure modes above produce no error
anywhere, which is the property that makes them expensive.

The same split also settles the contradiction filed as #194. TC-6 declares
`reset_done` as `UInt32[1]=1`; the refusal path emitted `0`; and the readers
added in #193 (`harness/traces.py`, `tools/judge_recorded_run.py`) treat `0`
as "not a boundary". Three descriptions of one topic.

## Decision

**Refusals leave the boundary topic.** The service answers a refused request
on a new output, `reset_refused` (payload `UInt32[1]=0`, schema
`reset_refused_u32`), carrying the TC-6 correlation metadata plus `error`.

1. `reset_done` now means exactly one thing: the episode boundary passed and
   the sim was touched. Its payload is always `1`, as TC-6 always said.
2. Both `metadata["error"]` special cases are DELETED, not documented. The
   class is removed rather than its two instances.
3. Only `rollout-client` — the requester — subscribes to `reset_refused`.
   The reply carries `request_id` for the audit trail; the client does not
   match on it, because TC-6 resets never overlap (one request at a time).
   An earlier draft of this ADR and of the manifests claimed the client
   "correlates on `request_id`", which the code has never done.
4. The payload filters in `harness/traces.py` and `tools/judge_recorded_run.py`
   **stay**, reclassified from live discriminator to legacy-compat guard.
   Issue #195 sketched deleting them, and for freshly recorded runs they are
   now dead code. But they read RECORDED traces, including runs this
   checkout cannot inspect — `runs/h3/` carries no `traces/` here, so the
   campaign corpus is not verifiable locally. Deleting a filter on the
   strength of a corpus you cannot read is the kind of claim this repo has
   been burned by; the filter costs one comparison per row.

The rule is enforced, not documented:
`tests/unit/test_episode_boundary_wiring.py` already derives each graph's
boundary consumers from node SOURCE rather than a hand-kept list, and now
covers `reset_refused` the same way — a node that handles the topic in code
while the graph never delivers it fails the build (the issue #179 shape).

## Consequences

- `specs/010-topic-contract.md` (Class C), `registry/schema/schemas.toml`
  (a new schema name is a Class C change, CAP-2), the frozen reset service,
  the frozen budget guard, and all eleven `graphs/expert_*.yaml` change
  together. `env_hash` moves once, here.
- **Existing campaign results are unaffected.** No shipped run can contain a
  refusal: `harness/rollout_client.py` validates `AISLE_RESET_MODE` at
  startup and only ever sends `[seed, 0|1]`, so the refusal route is
  unreachable from the shipped client. This changes where a message that has
  never been emitted in a measured run would go.
  Checked as far as it can be: every `reset_done` row in the trace corpus
  present in this checkout carries payload `1` (8 rows across the two runs
  that ship traces — `20260809-221849-e54d2c` and `s1-gate-1ec012`, both
  producers each). The campaign runs are NOT verifiable here, which is why
  the readers keep their filter (above) rather than assuming.
- The refusal route becomes reachable the moment an agent-authored node
  issues a reset, which is this repo's stated direction. Both special cases
  were guards against that future, not against today — which is why fixing
  the shape now, while the corpus is empty, costs nothing.

## Alternatives considered

- **Keep one topic; make the discriminator structural** (payload `1` vs `0`)
  with a shared helper every consumer calls. Rejected: cheaper, but it still
  relies on each consumer remembering to call the helper. It converts a
  silent omission into a slightly louder one instead of removing it. It also
  leaves TC-6's `UInt32[1]=1` contradicted by the emitter.
- **Leave it, with two documented special cases.** Rejected: the count was
  already two and rising, and the argument for stopping at two is the same
  argument that was available at one.
- **Have refusals carry a `sim_time_ns` so they look like boundaries.**
  Rejected outright: `refusal_reply_metadata` deliberately omits the stamp
  because a refused reset never touched the sim, and inventing one would be
  a lie the verifier's episode baseline depends on.

## A refused reset ends the run (issue #209, folded in)

The first draft of this ADR kept `rollout-client` advancing on a refusal and
called that "moving the routing, not the policy". **That was wrong**, and the
round-2 review caught it against the very comment this change deleted from
`budget_guard.py`:

> Deliberately narrower than the other consumers, which DO clear on a refusal
> to stay in phase with rollout-client (it proceeds on any reply).

Verified at the base commit: `oracle_pose`, `ik_trajectory`, `nav_action`,
`s1_expert`, `l2_pose` and `segmented_pose` carry no `error` filter — they
cleared on refusals BY DESIGN, because refusals rode the topic they all
subscribe to. Routing refusals to the requester alone therefore cannot leave
the policy untouched: the client would enter episode N+1 while
`ik-trajectory` holds a stale plan, `s1-expert` drops the new episode's plan
as a duplicate, and nav carries a leg across the boundary. That is issue
#179's class, reached by policy instead of by wiring.

So the client now **ends the run** on a refusal. Nothing advances, so nothing
can fall out of phase; the scene was never reset, so the episode would
measure nothing anyway; and it matches the refuse-over-degrade rule this repo
applied to `--reset behavioral` in #196/#204. Ending the loop is the client's
normal termination path — results flush per line, so completed episodes
survive and the runner reports the short count.

## dora drops undeclared outputs, so the routing is validated

`send_output` to an output a graph does not declare is IGNORED — one stderr
warning, `Ok(())` to the caller (dora node `validate_output`). Before this
change refusals rode `reset_done`, which every graph with a reset service
already declares, so a refusal was always deliverable. Afterwards a graph
that was not updated would drop it, and the requester would wait until the
wall clamp: a hang, which is worse than the degradation this ADR removes.

`harness validate` therefore gains **REFUSAL_UNROUTED**: a graph whose reset
service does not declare `reset_refused`, or declares it with no consumer,
is refused. No pre-existing rule covered this — VAL-4 checks that declared
outputs exist in the manifest, never that a manifest output is declared.
