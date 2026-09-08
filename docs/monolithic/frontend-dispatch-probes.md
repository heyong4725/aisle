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
