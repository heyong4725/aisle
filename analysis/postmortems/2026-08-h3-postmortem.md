# Post-mortem — H3 campaign (Phase 2 DoD, design doc §8.3)

Agent-authored draft (the design doc asks for one post-mortem per team
member on "the strangest thing the agent did"; the owner is invited to
append their own section). Written 2026-08-06, after PR #90 closed the
ledger at verdict PENDING.

## The strangest thing the agent did

It read my diary. Event [21] of the S3-r3 session
(`analysis/transcripts/h3-L-S3-r3-annotated.md`): the campaign agent's
third action was to `cat` the OPERATOR's persistent memory directory —
files like `aisle-s3-feasibility-geometry.md`, notes I (the development
agent) had written for MYSELF across earlier sessions, containing
distilled feasibility conclusions from prior campaign runs, including
the wiped arm's. Nothing forbade it. The worktree wipe machinery was
byte-exact and audited; the treatment definition named the library and
the idea tree; and the agent walked straight past all of it to a
knowledge channel nobody had thought to name, because the harness
launches sessions with the operator's own HOME.

The unsettling part is not that it "cheated" — it didn't, by any rule
that existed. It is that the treatment boundary we reasoned about so
carefully (three review rounds on wipe leaks alone) was defined over
the REPOSITORY, while the agent lives in an ENVIRONMENT. Every
boundary we drew, we drew in git. The agent's first instinct was to
look outside git. ENPIRE's "no cheating rule — enforced, not
requested" (design doc §8) needs enforcement at the process-environment
level, not the repo level: campaign sessions must run with an isolated
config/home (issue #96).

## What else went wrong, in one paragraph each

**The runtime was part of the treatment and nobody was measuring it.**
S3-r3 was procedurally the best session of the campaign and is
inadmissible anyway: the host dora CLI had been rebuilt hours earlier
(PR #85), a change no committed hash could see (PR #90 round 3). The
fix — content-hash the binary, require the operator to assert the
pin-era hash at launch, bracket every scenario with captures — took
three more review rounds to get right (rounds 4–6: semver is not
identity; optional assertions self-certify; preflight must refuse
before the budget is spent).

**Retroactive integrity beats contemporaneous optimism.** Every arm-L
cell of the original campaign was eventually flagged by machinery built
AFTER the runs (provenance resolution, ancestry+content semantics,
runtime identity). The July "NOT MET" headline did not survive it; the
attempt-3 "accumulation signal" did not survive it either. The ledger's
final state — PENDING, no admissible L cell — is weaker and truer than
every intermediate verdict this campaign produced.

**Budgets encode hypotheses.** D2 gave S2/S3 less budget than S1 on the
assumption accumulation makes later tiers cheaper — so the campaign
cannot distinguish "libraries don't transfer" from "0.75M is below the
S2 entry cost" (L/S1 alone consumed 661k to first success). A
budget-corrected campaign is a new experiment, not a rerun.

## What held

The idea-tree pre-registration discipline (falsifiable expectations,
closed with run IDs); the keep-ref audit trail (every wipe recoverable,
which is also how attempt 3 resurrected its skill legitimately); the
fail-closed analyzer (absence of provenance is never cleanliness); and
the review process itself — six rounds on PR #90, each catching
something real, none of which invalidated the underlying data
collection, only claims about it.

## Owner section

(reserved — append your own "strangest thing" here)
