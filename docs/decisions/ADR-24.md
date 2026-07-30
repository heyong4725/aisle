# ADR-24 — Installed-environment attestation (issue #38)

Status: DRAFT v2 — decision-ready for the owner (v1 revised per the
PR #68 review: version maps do not attest code, recorded drift does not
satisfy CON-5, and collection must live inside ADR-21's self-verified
boundary). Trigger: PR #34 red-team follow-up 4/5. Relates to ADR-21
(trusted baseline), SPEC 060 VAL-2/VAL-3, SPEC 050 CAP-4, CON-5.

## Problem

Validation verdicts — and therefore the HAR-2 rollout gate, the
INSTALL_MISSING/SOURCE_INVALID error surface, alternative hints, and
`search --installed` — depend on **installed-environment state**, but
the attested surface (`tools/env_hash.py`, ADR-21) fingerprints only
the frozen tree:

- `uv pip install dora-yolo` flips gate outcomes with zero trace in any
  run record or baseline.
- CON-5's tuple (`graph hash, env hash, platform, seeds`) is silently
  non-unique: two machines with identical tuples can hold different
  environments — including the everyday case, default `uv sync` vs
  `uv sync --extra sim`, which share a `uv.lock` yet differ in genesis/
  torch/dora (none of them registry `pip:` sources).
- A name+version map cannot close this: an agent can install a
  same-name-same-version distribution from a local/alternate source, or
  mutate installed files, and a version comparison still passes while
  different code executes.

## Decision (proposed, v2)

**One principle: a recorded run's environment is either MATERIALIZED
from the trusted lock and verified by hash — or the run is marked
unattested.** Version maps are diagnostics only; identity comes from
artifacts.

1. **D1 — Environment fingerprint joins the CON-5 identity
   (constitution spec-change).** Define
   `env_fingerprint = sha256(uv.lock bytes ‖ selection)` where
   `selection` is the resolved environment descriptor: python version,
   platform tags, and the exact extras/dependency-groups enabled. CON-5
   is amended to `(graph hash, env hash, env_fingerprint, platform,
   seed list)`. This makes default-vs-`--extra sim` distinct
   identities, and resolves the lock's multi-entry ambiguity (multiple
   `torch`/`scipy` entries per platform) — the fingerprint names the
   selection, not just the file.

2. **D2 — Campaign environments are materialized, not probed.** For any
   gate with `env-baseline != local`, the environment MUST have been
   built by `uv sync --locked` (with the recorded extras) from the
   baseline's `uv.lock` — uv verifies artifact hashes at install time,
   so provenance rides the lock's own hashes rather than a version
   probe. The gate refuses (`DIST_DRIFT`) when: `uv.lock` differs from
   the baseline's blob; `uv sync --check --locked` (same extras)
   reports the venv out of sync; any attested dist is an editable /
   directory / VCS-without-hash install (`direct_url.json` present with
   a local or unhashed source); or the lock is absent. Post-session,
   the campaign audit re-verifies the **installed files against their
   `RECORD` hashes** for the attested set (registry-referenced dists +
   the sim core: genesis, torch, dora-rs, pyarrow), so mid-session
   mutation of installed code is caught as tamper evidence even though
   a session-start gate cannot prevent it.

3. **D3 — Collection and comparison live inside the ADR-21
   self-verified boundary.** The collector/comparator ship in
   `tools/env_hash.py` itself (the blob that verifies ITSELF against
   the baseline ref before blessing anything) — not in agent-editable
   `registry.py`/`rollout.py`. `env_hash.py --check` gains the
   dist-attestation result alongside the frozen-tree result; the
   campaign audit REFUSES a record whose dist-gate evidence is missing
   or malformed (no silent record-by-convention). `registry.py` may
   keep a convenience probe for `search --installed` (a discovery UX,
   not a gate), clearly marked untrusted.

4. **D4 — Dev runs are honest, not blocked.** With
   `env-baseline local`, the gate computes what it can: if the venv
   verifies against the local lock, the manifest records the
   fingerprint and `attested: true`; otherwise it records
   `attested: false` with the drift summary. CON-5 reproducibility
   claims attach ONLY to attested runs — an unattested run is visibly
   outside the constitutional tuple rather than silently pretending.

5. **D5 — Diagnostics stay, demoted.** The validate report's
   `dist_state` map (canonical name → version|null over
   registry-referenced dists) remains as a debugging aid in VAL-3 —
   explicitly labeled non-attesting. All name joins (map, lock,
   `_pip_dist`) use PEP 503 canonicalization (case and `[-_.]+`
   folding); `_pip_dist` gains it in the implementation PR.

## What this deliberately does NOT do

- No full-site-packages hashing at gate time: materialize-and-verify
  (uv's install-time artifact hashes + `--check`) covers provenance;
  the bounded RECORD re-verification covers mutation for the attested
  set; hashing everything every gate would be minutes of I/O for no
  additional trust.
- No node-level probing: attestation is a gate/record/audit concern.
- `env_hash`'s frozen-tree meaning is unchanged; the fingerprint is a
  separate CON-5 component so drift attribution stays legible ("code
  drifted" vs "environment drifted").

## Consequences

- An env-change PR that installs a hub distribution becomes visible
  end-to-end: new lock blob in the baseline, new fingerprint in every
  subsequent manifest, and campaign gates refuse until the baseline
  moves.
- The sim-extra footgun becomes a first-class identity difference
  instead of an invisible one.
- Editable installs are unusable in campaigns by construction — the
  cost of making "what ran" provable.
- Cost: one `uv sync --check` per gate (~seconds), one bounded RECORD
  re-verification per session in the audit; constitution + VAL-3 +
  HAR-2/4 spec-change PRs; `make_worktree` gains `--locked`.
- H2/H3 records to date predate this and are not retroactively
  annotated.

## Open questions for the owner

1. D2's post-session RECORD re-verification set: proposed =
   registry-referenced dists + the named sim core. Full-environment
   re-verification is the strict alternative (~minutes per session).
2. CON-5 amendment wording: fingerprint as a fifth tuple component
   (proposed) vs folding it into `env hash` (simpler tuple, muddier
   attribution).

IDs: CON-5 (amended tuple), CON-7 (posture parity), ADR-21 (boundary +
baseline surface), VAL-3, HAR-2/4, CAP-4; CON-14 (implementation via
constitution + spec-change PRs after acceptance).
