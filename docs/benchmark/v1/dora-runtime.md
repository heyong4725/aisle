# Installing Dora from the declared source pin

AISLE supports a Dora CLI built from an immutable upstream commit, without
waiting for an upstream release tag. The Python node API remains pinned in
pyproject.toml; a source CLI is a separately verified dependency.

On macOS arm64 or Linux, install Git, rustup and uv, then run:

```sh
uv sync --extra sim --locked
rustup toolchain install 1.97.1
uv run --extra sim --locked python tools/dora_runtime.py install --prefix /absolute/path/to/new-dora-prefix
uv run --extra sim --locked python tools/dora_runtime.py verify --prefix /absolute/path/to/new-dora-prefix
```

The prefix must be new. A failed install keeps partial output for diagnosis
and does not write a success receipt; retry with another fresh prefix.
The installer fetches only the official repository at the declared commit,
checks Cargo.lock before and after the locked development-profile build, and
records the CLI and compiler version, platform, manifest hash and binary hash.
Build logs go to stderr; stdout contains one JSON result. Verification requires
the paired Python API and rejects changed source-pin or executable bytes.

The receipt is local installation evidence, not signed provenance or proof
that the upstream implementation is correct. Build output need not be
bit-identical across machines. Keep the receipt and binary together; moving
the prefix is permitted because executable identity is checked by content.

The current pin is corrected commit `48e96b5b43af7c4d9fae3c964faef88e30d9ae06`,
marked `validated` for AISLE runtime adoption. This does not declare an upstream
release or benchmark release readiness. The Python API remains 1.0.1.

The [focused upstream review](https://github.com/dora-rs/dora/pull/3429#issuecomment-5564787674)
verified all seven metadata/frame-budget tests, including delivery of all 80
metadata-heavy events across bounded frames. A local installation through this
tool passed receipt verification and the real-process
`slow_consumer_keeps_backpressure_commit` regression (one test, 12.26 seconds).
The [corrected-revision Linux diagnostics](https://github.com/heyong4725/aisle/actions/runs/34081090523)
passed all seven quickstart stages from clone (339.0 seconds) and archive
(318.4 seconds). Those diagnostic records retain `release_acceptance=false`;
the normal Linux workflow must separately exercise the supported installer and
verified prefix. No release tag is required for this adoption.

The supported quickstart then uses the verified prefix explicitly:

```sh
uv run --extra sim --locked python tools/quickstart.py --runtime-prefix /absolute/path/to/new-dora-prefix
```

A candidate pin is refused before rollout. A validated pin's receipt is checked
before use and subsequent subprocess launches; the pinned directory is passed
to child processes through PATH. This installation check does not replace the
quickstart's runtime stages or any benchmark release gate. Updating the pin
invalidates old receipts and requires a new installation.

To return to an official release later, update the declared dependency policy
and verify the matching CLI/API pair through the same runtime acceptance gates.
