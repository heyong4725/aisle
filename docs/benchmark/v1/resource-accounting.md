# Resource accounting (BMK-17)

- Tokens: provider-reported input and output tokens per session from the
  agent adapter's usage stream (`tools/agent_adapters.py`, HAR-5). Cached
  input tokens are reported separately and never subtracted.
- API cost: computed from the provider's public price list on the access
  date, in USD; the price list version and date are recorded. Missing prices
  leave the field null, never zero.
- Retries: every provider retry is counted in tokens and wall time and
  listed under `retries`.
- Parallel agents: reported per session; a fleet run (`harness fleet`)
  records the fleet size and per-agent shares.
- Wall, CPU, GPU time: from the session manifest (`durations`), host CPU
  accounting, and the simulator backend's device report (`sim_device`).
- Peak memory and storage: RSS peak of the session tree and bytes written
  under `runs/`.
- Model download and setup: amortized separately as `setup`, never charged
  to a session.
- Incomparable fields stay explicit as `not_comparable` with a reason.
- No composite score collapses accuracy, safety, and cost; Pareto views
  may be shown only beside the registered fields (BMK-16).
