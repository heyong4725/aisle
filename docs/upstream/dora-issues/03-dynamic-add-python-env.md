# Bug/Feature: dynamically added nodes lose the Python environment — no `--uv` inheritance, and interpreter symlink resolution bypasses the venv

**Filed as [dora-rs/dora#2918](https://github.com/dora-rs/dora/issues/2918) (2026-07-31).**

**Versions:** dora-cli 1.0.0-rc.4; daemon from git rev `7eb4a5f8b`. macOS arm64, uv-managed venvs.

## What happens

Two related problems for `dora node add` on a dataflow started with `dora start ... --uv`:

1. The dynamically added node is spawned WITHOUT the dataflow's `--uv` wrapping, so a
   plain `path: my_node.py` runs under whatever bare python the daemon finds — imports
   like pyarrow/dora fail (`ExitCode(1)` before register).
2. Pointing `path:` at a venv interpreter does not help: the daemon appears to resolve
   the interpreter SYMLINK before exec, so argv[0] is the uv base interpreter and
   `pyvenv.cfg` discovery never happens. Observed with the node yaml literally containing
   the venv path:

```
path: /.../wt-h4/.venv/bin/python
args: /.../trace_recorder.py
```
```
ModuleNotFoundError: No module named 'numpy'
```

while `/.../wt-h4/.venv/bin/python -c "import numpy"` succeeds in a shell.

**Workaround we ship:** set `env: {PYTHONPATH: <venv site-packages>}` on the dynamic
node — packages then resolve regardless of how the interpreter was exec'd.

## Asks

- `dora node add --uv` (or better: inherit the dataflow's exec/build mode by default) so
  dynamic nodes run in the same environment as their static peers.
- Don't canonicalize the node executable path before exec (or exec via the given path so
  venv discovery works).
