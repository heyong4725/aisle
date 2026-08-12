"""Compatibility hook for campaign checkouts older than PR #166.

The CURRENT campaign runner copies this file to a session-only directory as
``sitecustomize.py``.  Python imports it before an old checkout's ``harness``
entry point, letting the runner enforce its treatment pin without modifying
the historical source tree.  No campaign pin or rollout argv means no action.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

_OID = re.compile(r"^[0-9a-f]{40}$")
_FAILED_SELECTOR = "__AISLE_CAMPAIGN_PIN_COMPAT_FAILED__"


def _set_cli_selector(value: str) -> bool:
    """Set the old CLI's selector and return whether this is a rollout."""
    if "rollout" not in sys.argv[1:]:
        return False
    for index, arg in enumerate(sys.argv[1:], start=1):
        if arg == "--env-baseline":
            if index + 1 < len(sys.argv):
                sys.argv[index + 1] = value
            else:
                sys.argv.append(value)
            return True
        if arg.startswith("--env-baseline="):
            sys.argv[index] = f"--env-baseline={value}"
            return True
    sys.argv.extend(("--env-baseline", value))
    return True


def _validated_pin(root: str | Path, pin: str) -> tuple[str | None, str | None]:
    """Reproduce ADR-21's server trust check for a pre-#166 harness."""
    root = Path(root)
    fetch = subprocess.run(
        ["git", "fetch", "--quiet", "origin", "refs/heads/main"],
        cwd=root,
        capture_output=True,
        text=True,
    )
    if fetch.returncode != 0:
        return None, f"cannot fetch origin main: {fetch.stderr.strip() or 'no remote?'}"
    head = subprocess.run(
        ["git", "rev-parse", "FETCH_HEAD"], cwd=root, capture_output=True, text=True
    )
    if head.returncode != 0:
        return None, "cannot resolve FETCH_HEAD after fetch"
    commit = subprocess.run(
        ["git", "rev-parse", "--verify", f"{pin}^{{commit}}"],
        cwd=root,
        capture_output=True,
        text=True,
    )
    if commit.returncode != 0 or commit.stdout.strip() != pin:
        return None, f"cannot resolve campaign baseline OID {pin}"
    ancestry = subprocess.run(
        ["git", "merge-base", "--is-ancestor", pin, head.stdout.strip()],
        cwd=root,
        capture_output=True,
        text=True,
    )
    if ancestry.returncode == 1:
        return None, f"campaign baseline {pin} is not in protected origin/main history"
    if ancestry.returncode != 0:
        return None, "cannot verify campaign baseline against protected origin/main history"
    return pin, None


def _install() -> None:
    pin = os.environ.get("AISLE_ENV_BASELINE", "")
    if not _OID.fullmatch(pin):
        return
    # Fail closed before importing or patching anything: Python reports but
    # suppresses sitecustomize exceptions, so a failed install must leave a
    # selector the historical gate rejects instead of falling back to main.
    if not _set_cli_selector(_FAILED_SELECTOR):
        return
    try:
        import aisle.harness.rollout as rollout_module

        if getattr(rollout_module, "_aisle_campaign_pin_compat", False):
            _set_cli_selector("origin/main")
            return
        original_run_gates = rollout_module.run_gates

        def pinned_resolver(root, *_args, **_kwargs):
            return _validated_pin(root, pin)

        def pinned_run_gates(*args, **kwargs):
            report = original_run_gates(*args, **kwargs)
            if (
                isinstance(report, dict)
                and report.get("ok")
                and report.get("env_baseline_oid") == pin
            ):
                report = dict(report)
                report["env_baseline"] = pin
                report["env_baseline_oid"] = pin
            return report

        rollout_module.resolve_trusted_baseline = pinned_resolver
        rollout_module.run_gates = pinned_run_gates
        rollout_module._aisle_campaign_pin_compat = True
        # The old validator accepts origin/main; the patched resolver turns
        # that selector into the server-validated pin, and the wrapper records
        # the immutable selector in the gate/manifest.
        _set_cli_selector("origin/main")
    except Exception as error:  # noqa: BLE001 - startup must degrade to refusal
        print(f"campaign baseline compatibility failed: {error}", file=sys.stderr)


_install()
