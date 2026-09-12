# ADR-66 — pilot first: a labelled pilot evidence tier and the execution order

Status: PROPOSED (agent-drafted 2026-09-11 at the owner's direction after the
owner agreed the project is rigorous in the wrong order; owner sign-off per
CON-10). Answers issue #567. Touches no frozen path and no hashed executor
code; adds the `pilot` purpose to the freeze registry. The Class A gate
relaxation it names is a separate `spec-change` to CON-9/CON-16.

## Context

Since Phase 2/3 closed on 2026-08-16 the repository gained 275 commits and no
measured hypothesis result. Fifteen specs are PROPOSED and human-review gated.
The causal-study registration (SPEC 500, #347) was re-frozen from v5 to v15
with every integrity gate still `pending`, two of them external reviews. The
claim ledger already says the quiet part: "the confirmatory power inputs are
assumptions until pilots exist" (`docs/claim-evidence.yaml`).

The order is circular. CSE-8 sizes the confirmatory sample from pilot-only
control success. STA-3 gives pilot records their own campaign identity and
bars them from any confirmatory estimate. The statistics module accepts a
`pilot` campaign phase. The matched-session executor runs `engineering`
sessions and retains them unscored, refusing any scored purpose without CSE-10
gate records. Everything a pilot needs exists except a registration purpose,
an explicit rule that a pilot does not wait on external review, and the will
to run one.

The asymmetry that makes this safe: an unattributable result can never be
repaired, but a labelled pilot can always be rerun as confirmatory once the
gates ratify. Relaxing sequencing costs nothing recoverable. Relaxing the
judge would cost the project. This ADR changes the order and nothing about
the judge.

## Decision

### 1. The pilot evidence tier

- A pilot is a freeze registration with `purpose: pilot`, its own campaign id
  (`<study>-pilot-vN`), and its own seed commitment. The registry refuses a
  confirmatory declaration that inherits a pilot's seeds and a pilot that
  inherits a confirmatory lineage (purpose change is already a `FreezeError`).
- Pilot sessions run through the UNCHANGED executor path
  (`tools/matched_campaign.py run --purpose engineering`), retained and
  unscored, into an output root named by the pilot campaign id. No hashed
  harness source changes for a pilot, so no registration drifts.
- Pilot analysis uses the SPEC 400 protocol with `campaign_phase: pilot` and
  validation purpose `power`. Its outputs are the CSE-8 inputs: control
  success, observed effect direction, per-session cost, validate-fix counts.
- A pilot result enters `docs/claim-evidence.yaml` only as a named power
  input under the still-`unrun` confirmatory claim. It is never `supported`.
  Whether SPEC 410 gains a `pilot` status is the owner's call, not this ADR's.

### 2. What a pilot does not wait for

- External-review gates (CON-14 spec approval, STA-12 statistical review)
  are NOT prerequisites for a pilot.
- Machine gates run wherever the instrument exists and their status is
  RECORDED in the pilot declaration. A failing or absent machine gate is a
  named limitation of the pilot, never a blocker and never silently passed.
- The frozen set, oracle isolation, the attestation tuple, the exact failure
  taxonomy, the wrong-object asymmetry, and honest `attested`/`unattested`
  labelling apply to pilots exactly as to everything else.

### 3. Execution order (from #567)

1. #347 pilot: `cse-causal-study-pilot-v1`, derived from the v15 declaration
   with purpose `pilot`, fresh seeds, 6 sessions per arm on one agent system
   and one selected task stratum (below CSE-8's confirmatory floor of 10,
   which is why it is a pilot), on the simulation machine.
2. H6 (ADR-h6): machinery exists, no GPU.
3. Spec moratorium until 1 and 2 report (section 4).
4. GPU time as a budget line for the two Phase 6 entry gates.
5. One attested SO-101 result under SPEC 520.
6. Second platform: the Franka-on-mobile-base station, desk tasks with the
   base parked first, then retail.

### 4. Spec moratorium and triage

Nothing new enters `specs/` until items 1 and 2 report, except spec-changes
those runs force. The PROPOSED specs are triaged once, by the owner, into:

| Spec | Needed by #347 pilot or H6? | Proposed disposition |
|---|---|---|
| 400 statistics | yes, pilot analysis | ratify the pilot path now; STA-12 review stays a confirmatory gate |
| 420 treatment integrity | partly, session admission | ratify what #519 implements; park the rest |
| 440 monolithic control | yes, control arm | ratify MON-1..MON-8 as built; park MON-9+ until the pilot exposes a need |
| 490 non-oracle task band | yes, task selection | ratify; the pilot uses the current calibration |
| 500 causal study | yes | ratify as the confirmatory contract; the pilot is registered separately |
| 410 claim evidence | for reporting | ratify; decide the `pilot` status question |
| 430 instrument audit | no | park until after the pilot |
| 450 sealed fault bank | H6 only if blinded | park; H6 runs with its public faults first, as ADR-h6 already does |
| 460 actuation threat model | no | park; needed before hardware, not before a sim pilot |
| 470 safety exposure | no | park |
| 480 semantic authorization | no | park |
| 510 fault localization study | no | park until H6 reports |
| 520 SO-101 hardware gate | no | park until GPU budget exists |
| 530 reproduction archive | no | park |
| 540 public benchmark | no | park |

Disposition is the owner's; the table is the proposal.

### 5. Freeze discipline and cadence

- Freeze once per pilot. Amend by dated note (the ADR-h6 pattern), not by
  re-registration, unless a hashed artifact legitimately changes.
- One measured result every two weeks. If none lands, infrastructure PRs
  stop until one does.

### 6. Class A gates

CON-9 applies `/review`, `/simplify`, and the CON-16 cross-review to every
commit, taxing an ADR at the rate of guard code. A separate `spec-change`
proposes: Class A (docs, tests, tools) runs format, lint, and unit only;
Class B and C keep the full gate set. That change needs the owner's approval
under CON-14 because the constitution is STABLE.

## Consequences

- #347 can run this week as a pilot on the simulation machine with today's
  infrastructure, and its result is the missing CSE-8 input.
- The v15 registration stays as the confirmatory pre-registration; the pilot
  never touches it.
- The 09-06 to 09-10 frontend-dispatch series (#541 to #561) is judged by the
  cadence rule going forward: complete it only if the pilot needs it.
- A reviewer can no longer ask whether the evidence machinery is the product.
  The answer will be a dated pilot result and a confirmatory run behind it.

## What this does not decide

Confirmatory authorization (CSE-10, STA-12, CON-14 stay as written); a
`pilot` status in SPEC 410; the wording of the CON-9 amendment; any change to
the frozen set.
