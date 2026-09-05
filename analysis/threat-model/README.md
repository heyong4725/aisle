# Agent-to-actuation threat model: fixture evidence (SPEC 460, issue #350)

`bypass-report.json` is the executed attack catalog (THR-9, THR-10,
THR-12) against the real `actuation-gateway` and guard code with a fake
authenticated driver (THR-16). Evidence kind: fixture. It establishes
process- and transport-level behaviour inside one Python process only.

## Boundary

`src/aisle/harness/actuation_gateway.py`: one gateway per environment owns
an epoch-bound HMAC capability the driver verifies (THR-4, THR-6); the
gateway validates and clamps every request with the pinned SPEC 080 guard
logic and trusts no topic name, producer claim, or prior validation result
(THR-5); malformed requests, missing or stale stamps, guard exceptions,
audit-sink failure, controller teardown, and gateway silence fail closed
with no new command, and the driver lease holds (THR-7); every decision,
receipt, rejection, and epoch event goes to a write-ahead, append-only,
monotonic audit stream (THR-8).

## Catalog result

| outcome | attacks |
|---|---|
| blocked at expected layer | 18 of 18 |
| blocked elsewhere, survived, not executed | 0 |
| driver receipts without a gateway decision | 0 |

Classes: direct driver call, spoofed gateway identity, replayed epoch,
replayed command, wrong environment, forged safe topic, alternate channel,
out-of-limit request, malformed payload, missing stamp, stale stamp,
future stamp, guard-crashing payload, gateway silence, audit-sink failure,
controller teardown, dynamic-node swap, forged prior-validator claim.

## What this does not establish

- OS-level process, socket, and filesystem confinement of the participant
  (residual path RES-1, an in-scope blocker owned by #353).
- Claude and Codex campaign paths under the production confinement
  profile (THR-11): not run.
- Device credentials, firmware watchdogs, physical stop latency (RES-2,
  hardware-pending).
- The out-of-scope registry (kernel or hypervisor compromise, malicious
  administrator, physical tampering) narrows every claim and awaits CON-14
  ratification (THR-3, THR-13).

The word `unbypassable` is not licensed by this evidence (THR-14).

```bash
uv run harness threat run --output analysis/threat-model/bypass-report.json
```
