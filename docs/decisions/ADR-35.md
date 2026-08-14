# ADR-35 — a reset reply's `mode` is the mode REQUESTED, on every route

Status: ACCEPTED (issues #205, #194). CON-15 interpretation of TC-6; amends
no spec text. The reset service is in the CON-7 frozen set, so the
implementing PR is an `env-change`.

## Context

TC-6 says a reset reply carries metadata `seed`, `mode`, `t_reset_ms`. It
does not say whether `mode` is the mode the requester ASKED FOR or the one
the service EXECUTED. The four routes had quietly settled on different
answers:

| route | who builds the reply | `mode` was |
|---|---|---|
| refusal (`reset_refused`) | `refusal_reply_metadata` | REQUESTED — echoed from the payload |
| teleport relay | the bridge | both (they agree) |
| behavioral success | the service | REQUESTED — `BEHAVIORAL`, and it executed |
| behavioral exhaustion → fallback | the bridge | EXECUTED — `0`, for a request made in mode 1 |

Only the last one disagrees, and it disagrees on the route that is hardest
to reason about.

Separately, RST-2 requires the fallback to carry `fallback: true` in the
reply metadata, and it did not. `fallback_teleport` attaches
`fallback`/`behavioral_attempts` to the FORWARD to the bridge, and the
bridge builds its reply metadata fresh — `_metadata(...)` echoes only
`request_id`. So after up to three real motion attempts, the reply reaching
consumers was byte-for-byte a plain teleport reply. A6 audited its three
fallbacks from the node's stderr log, not from reply metadata, which is why
the gap survived a whole ablation.

## Decision

1. **`mode` is the mode REQUESTED, on every route.** `fallback: true` is
   what says a teleport actually ran. One meaning per key, and nothing is
   lost: requested mode and executed mode are both recoverable from the
   pair, whereas reporting only the executed mode destroys the request
   intent.
2. **The fallback reply carries `fallback` and `behavioral_attempts`**, as
   RST-2 already required.
3. **`t_reset_ms` on that route spans the WHOLE reset** — the behavioral
   attempts plus the fallback teleport — not the bridge's hop alone. RST-1
   budgets the reset, and the exhaustion route is where the reset is most
   expensive.

## Where the fix lives, and why not the bridge

In the reset SERVICE, not `dora_genesis`. The service already relays the
bridge's `reset_done` on its own output (issue #192 made it the boundary
authority) and it is the only node that knows the behavioral context. It
remembers the forwarded fallback in a single slot and merges the audit keys
onto the relay, correlated on `request_id` so a stray reply cannot pick it
up — one slot because TC-6 resets never overlap.

The alternative — teaching the bridge to echo service-supplied keys — was
rejected: it makes a cross-node contract out of what is one node's own
bookkeeping, and it would put behavioral concepts (`behavioral_attempts`)
into a node that has no idea behavioral resets exist.

## Consequences

- No consumer reads `mode`, `fallback` or `behavioral_attempts` today —
  checked across `src/` and `tools/`, they appear only in docstrings. This
  changes the audit trail, not control flow. The keys become reliable for
  the first time, which is what a future consumer would have needed.
- Recorded runs predating this carry the old shape: a fallback looks like a
  teleport. A6's findings stand because its fallback count came from the
  reset node's stderr (`log_reset.jsonl`), independent of reply metadata.
- `env_hash` moves: `src/aisle/reset/` is frozen.

## Alternatives considered

- **`mode` = executed.** Rejected: it is the reading that produced the
  inconsistency, and it destroys request intent. A consumer counting
  behavioral resets by `mode == 1` would undercount every fallback with no
  way to recover the difference.
- **Leave `t_reset_ms` as the bridge's.** Rejected: it under-reports the
  route that most threatens RST-1's budget, which is the opposite of what
  an audit field is for.
- **Emit a second, behavioral-only reply.** Rejected outright: consumers
  advance an episode epoch on every `reset_done` (issue #179), so two
  replies for one request desyncs nav's epoch and makes it refuse the new
  episode's goals.
