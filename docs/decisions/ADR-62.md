# ADR-62 — Matched session binding of representation documents and process inputs

Status: PROPOSED — engineering interpretation for #519; no study admission.

MON-8 permits representation-specific instructions declared by MON-1 while
requiring shared instructions and execution authority to match. SPEC 420 records
concrete hashes for files, confinement profiles and environment variables. Two
isolated arm views necessarily have different absolute paths and may have
different representation documents.

The controller retains both concrete manifests. It compares derived role
identities only after checking the actual compiled policy, exact editable grants,
private generated environment paths and recorded hashes. Runtime search paths,
executable authority, shared system instructions and all other shared fields
remain subject to equality. Normalizing a comparison never changes a retained
manifest or confers OS confinement capability.

An explicit `prompt_row` selects the treatment table's declared documentation
row. In that mode alone, `research_contract_sha256` identifies the existing
launcher set-digest of the row's document bundle. Every document must be visible,
read-only under the declared edit grants, and byte-identical to the controller's
copy. The controller retains the row id and both bundle identities. Without this
mode, the existing manifest comparison applies. Document availability is not
proof of prompt delivery; the final launcher integration must bind delivered
instructions as well.

Explicit launch bindings retain the argv for each arm, require identical
arguments, and verify a canonical executable path against the admitted agent
binary hash. The runner requires a finite positive `budget.wall_ceiling_s`
alongside the existing token ceiling. It starts each attempt with zero prior
spend; existing output directories cannot be overwritten or resumed. Supporting
representation-specific argv later requires an explicit, verifiable delivery
mapping rather than ignoring argument differences.

The matched controller uses only the existing campaign process supervisor and
telemetry. It does not invoke historical campaign treatment or scoring behavior.
Engineering fixtures exercise real child processes with an explicitly synthetic
pass-through adapter. They prove process integration, not confinement, expert
parity, agent performance, or pilot/confirmatory readiness. Those study gates
remain separate, and #519 remains incomplete until the supported entry point and
full common-evidence integration are implemented and verified.

Controller journal metrics distinguish request attempts, launched tool processes
and controller-call wall time. They do not establish total agent tool calls:
frontend-native edits, reads and shell calls require their own verified telemetry
and request linkage. The common envelope leaves the overall `tool_calls` value
unknown until that coverage exists. Current controller call ceilings apply to
the registered request channel and are not a claim of complete frontend budget
enforcement. Completing that accounting remains within #519's software scope.

Executable engineering launch bindings require `system_prompt_arg`, a positive
argv index whose UTF-8 bytes hash to the manifest's `prompts.system_sha256`.
Admission and the immediate prelaunch recheck verify this mapping; retained
`launch.json` records its transport, index and hash. This establishes delivery to
the child process only. It does not prove that a provider received those bytes
in a system role, nor that a frontend avoided injecting additional instructions.
The receipt explicitly records `provider_role_verified: false`. Frontend-specific
prompt-role and served-model verification remain required for study readiness.

Research-contract delivery similarly requires a distinct positive argv index
`research_contract_arg`. Without a representation document row, the selected
argument's UTF-8 SHA-256 must match `research_contract_sha256`. With a declared
row, it must instead be the exact compact UTF-8 JSON array of `{path, text}`
objects in the row's path order, using the controller's verified document bytes.
Only that verified bundle argument is normalized for cross-arm launch comparison;
all other arguments and index mappings remain shared. The existing document-set
hash binds paths and their individual hashes, while the retained argv preserves
actual delivery bytes. The receipt leaves frontend interpretation unverified;
passing a bundle to a process does not prove that a coding frontend followed it.

Fresh admission verifies actual HOME, XDG config and XDG cache baselines against
their declared hashes. Each baseline is the SHA-256 of canonical compact sorted
JSON mapping relative regular-file paths to their SHA-256 and permission mode;
file contents are not retained. Redirected and nonregular entries are refused.
The fresh-launch recheck repeats this verification. During an active session,
normal writes inside its already admitted private HOME do not constitute initial
contamination; only that arm's initial-content comparison is skipped while the
other arm's baseline, generated directory roles and environment remain checked.
A new launch always requires fresh baseline verification again.


Fresh session plans may bind named `private_roots` for controller evidence and
capability storage. Actual engineering probes showed that otherwise identical
permission comparisons retained different per-session private path strings.
These optional roles normalize only exact canonical existing directories that
are explicitly hidden from both arms and disjoint from controller source,
participant read/write/execute grants, runtime roots and other private roles.
Names and original paths remain part of the immutable plan; retained-plan
verification repeats these checks. Extra hidden paths without a role continue
to affect comparison. This changes no compiled sandbox grant and does not
establish full paired-session equivalence, provenance of private contents or
study admission.

Trusted run-controller environments also participate in MON-8 matching. Each
binding must first verify its complete environment record and generated private
HOME directories. Comparison then substitutes only those verified HOME roles;
all other variables, including executable search paths and locale, must match
between arms. Independent valid environment receipts do not establish matching
settings. Fresh HOME locations remain distinct and retained in the plan.

Simulator accounting is being implemented as per-launch operation journals,
separate from episode-relative outcome time. A journal binds the run, launch
index, physics timestep in nanoseconds and environment count. It records each
wrapped build/reset/step attempt before invocation, then its completion or
failure and injected-monotonic-clock duration. Completed environment-steps and
completed environment-simulation nanoseconds are derived from completed step
calls. A failed or interrupted native step leaves actual partial advancement
unknown; a missing terminal event never proves complete accounting. Journal
completeness concerns the recorded lifecycle only. Until all relevant simulator
boundaries and the controller's expected launch inventory are connected and
verified, these receipts cannot populate total session simulator work or satisfy
CSE-4's protocol-level units and aggregation gate.

The trusted typed host treats closure of its coordinator-owned `turn` input as
the end of lockstep work. It delivers that closure event, then ends its transport
iterator even if other graph inputs remain open. The real accounting acceptance
run exposed a cycle: authored workers waited on remaining inputs until manual
Dora shutdown, leaving only the daemon's stop grace for host verification and
receipt writing. The coordinator can schedule no further turns after closing its
output, so those cyclic inputs cannot authorize additional work. Worker exit,
runtime verification and host postflight remain required; neither a successful
episode nor a planned daemon stop substitutes for their records.

Dynamic workers select the current runtime's direct Python interpreter before
binding its executable digest. On macOS framework builds, the command-line
launcher starts the interpreter inside `Resources/Python.app`; granting only
the launcher cannot run it under a single-executable worker policy (see
[CPython's framework launcher](https://github.com/python/cpython/blob/3.13/Mac/Tools/pythonw.c)).
Both arm providers and their run-binding check use the direct interpreter.
It must already belong to the admitted runtime inventory and pass the actual
capability audit. An absent or non-executable interpreter refuses preparation;
selection neither adds runtime roots nor permits a fallback launcher.
