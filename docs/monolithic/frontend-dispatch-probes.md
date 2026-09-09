# Frontend dispatch probes

Issue #536 follows the remaining frontend budget and evidence work in #519
(MON-8, MON-12, MON-13). The existing stdout observer counts emitted events.
An event observed after execution cannot authorize that execution beforehand.

`tools/frontend_codex_probe.py` makes this distinction reproducible using an
actual Codex CLI, a scripted localhost Responses endpoint, and a harmless
`exec_command` that writes `marker.txt`. It uses no model inference or API key.
The script records the installed binary digest and version, its own source
digest, exact invocation/environment, selected provider-request fields,
scripted SSE responses, CLI stdout/stderr, hook input, and observed side effect.
Each case requires a new canonical output directory.

Both probes retain `fixture-preflight.json` before launching the measured
invocation. The report's `fixture_files` binds that manifest and selected input
files by SHA-256: invocation, sandbox policy, copied probe sources, generated
hook, and Claude settings. Explicitly absent hook/command files are recorded as
null. Postflight checks reject changed, missing, oversized, or symlink inputs;
a successful tool refusal does not make a changed fixture valid.

These checks compare retained files before and after the invocation. They do
not prove what code was loaded, detect changes restored between checks, or
authenticate an archive supplied by a participant. The output directory remains
writable by the fixture frontend. Configuration digests describe this specific
invocation, including its local transport endpoint; they do not attest the full
admitted tool surface or campaign confinement. Historical reports without these
bindings remain partial observations.

On macOS, from the repository root:

```bash
uv run python tools/frontend_codex_probe.py \
  --binary /absolute/path/to/codex \
  --mode baseline \
  --output /private/tmp/aisle-codex-baseline
```

Repeat with distinct output paths for `deny`, `unsupported`, `malformed`,
`timeout`, `exit1`, `missing_script`, and `missing_command`. The profile currently
accepts only CLI version `0.153.4`; a different revision requires a deliberate
fixture/profile update and new observations. The recorded digest identifies the
binary tested; it is not an independent attestation of its provenance.

The child uses fresh Codex configuration and temporary state. An outer macOS
sandbox restricts writes to the output directory and networking to local IPC and
localhost. The CLI's inner sandbox is disabled because nested sandbox application
failed on the development host. Hook trust is bypassed only for the probe's own
fixed generated hook. This setup is an engineering fixture, not the production
campaign confinement policy or proof of exhaustive containment.

`ok: true` means the probe observed a consistent process result, matching tool
reply and side effect (or explicit refusal). Inspect `tool_executed` and
`tool_blocked` for the measured behavior. A missing marker alone is insufficient:
a crash, timeout, missing reply, wrong call identity or unsuccessful command is
an invalid probe. A failed hook can still produce a valid observation that the
tool executed. Reports always retain `complete_coverage: false` and
`confinement_verified: false`.

The initial actual-CLI audit found:

| Case | Tool wrote its marker |
| --- | --- |
| No hook | Yes |
| Valid hook denial | No |
| Unsupported stop field | Yes |
| Non-JSON hook stdout | Yes |
| Hook timeout | Yes |
| Hook exits 1 | Yes |
| Missing hook executable | Yes |
| Missing Python hook script | No: Python exits 2, which invokes denial |

The valid denial's hook `tool_use_id` matched the scripted provider call and
subsequent `function_call_output` refusal. That denied attempt had no
`command_execution` item in exec JSONL, so stdout observations alone also miss
attempts. The [official hook documentation](https://learn.chatgpt.com/docs/hooks)
describes coverage exceptions and denial shapes; the fixtures test actual
installed behavior rather than assuming all hook errors deny execution.

The synthetic model uses fallback CLI metadata. These probes do not certify a
study model profile, hosted tools, continued process input, nested Code Mode,
subagents, Claude, total tool counts, authenticated controller request linkage,
or budget enforcement. The next implementation still needs a controller-owned
boundary that reserves budget before dispatch and prevents side effects when
authorization fails. Every admitted route and both arm paths remain in scope;
these fixtures do not close #536 or #519, authorize study collection, or replace
independent reviewer/operator/private-evaluator gates.

## Claude Bash hook probe

`tools/frontend_claude_probe.py` exercises Claude Code `2.1.263` with a scripted
localhost Messages endpoint. Select the binary explicitly and use a fresh output
directory for each of `baseline`, `deny`, `timeout`, `exit1`, `malformed`, and
`missing_command`:

```bash
uv run python tools/frontend_claude_probe.py \
  --binary /absolute/path/to/claude \
  --mode baseline \
  --output /private/tmp/aisle-claude-baseline
```

The fixture sends only a fixed dummy API key; no live provider or model inference
is involved. The `sonnet` alias in the request is an observed label, not proof of
a served model. Fresh `CLAUDE_CONFIG_DIR`, temporary directories, empty external
settings sources and an empty strict MCP configuration isolate the fixture's
configuration. The built-in catalog remains available. Permissions are bypassed
inside the same limited outer macOS fixture sandbox described above; `--bare`
is not used because it skips hooks.

Claude makes a preliminary request with no tools. That request receives a text
response without consuming the single Bash instruction. The profile requires
three provider requests, one matching `toolu_fixture` result, normal process
completion and the expected marker or explicit denial. Denial additionally
requires a matching retained hook input and no marker. Startup errors, timeouts,
missing/mismatched results and other failed tool outcomes cannot count as denial.

The report retains binary/version, invocation/environment, full advertised tool
definitions, scripted responses, hook input, stdout/stderr and marker evidence.
It binds and copies both the Claude fixture source and the shared Codex helper
source used for hook generation and artifact writes. This is one Bash-route
measurement, not a complete frontend profile. Its coverage and confinement flags
remain false; the MCP identity and production authorization work in #536 remains
separate.

The continued-input acceptance fixture starts a bounded input-reading process
through `exec_command`, extracts its actual session ID, and sends `write_stdin`
as a separately reserved call. The authorized case must retain the delivered
text; the exhausted-budget case must retain refusal without that side effect.
Both cases replay the dispatch journal and keep coverage and confinement false.
Run `tests/accept/test_frontend_continued_input.py` with
`AISLE_CODEX_PROBE_BINARY` set to the selected binary. This uses a scripted local
provider and does not consume provider credentials.

The Codex fixture's explicit `allow_pty=True` API option (`--allow-pty` on the CLI)
allows writes to `/dev/ptmx` and numbered `/dev/ttys` devices so the input route
can execute. The option is recorded in the invocation and the resulting policy
is included in the fixture snapshot. It defaults to false. These device grants
are engineering fixture configuration, not a campaign confinement attestation.
