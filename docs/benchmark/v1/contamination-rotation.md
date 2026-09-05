# Contamination and rotation policy (BMK-19)

- Public release dates: recorded per task family and per public seed set
  in `task-distributions.json` when a version is released.
- Agent and model cutoffs: each baseline records provider, model id,
  client version, access date, and training or access cutoff where the
  provider publishes it; unknown stays `unknown`.
- Participant disclosures: submissions declare prior exposure to public
  instances and any use of AISLE materials in training or prompts.
- Leaked or compromised instances: quarantined immediately; the affected
  private set is retired and never called blind again.
- Rotation cadence: private evaluation seeds and the hidden fault bank
  rotate on every benchmark version; a replacement set is frozen and
  committed (`harness freeze`, FLT-5 commitments) before use, and the old
  set is revealed only under the registered schedule (FLT-14, FLT-16).
- Cross-version comparability: reported only through the frozen
  qualification set shared across versions; private-set results are not
  compared across versions.
