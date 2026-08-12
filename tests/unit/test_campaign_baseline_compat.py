"""Historical-checkout compatibility for issue #91 / PR #166."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = REPO_ROOT / "tools" / "campaign_baseline_sitecustomize.py"


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()


def test_old_harness_process_consumes_and_records_campaign_pin(tmp_path):
    """HAR-2 / CON-5: reproduce the review finding end to end.  A fake
    pre-#166 harness neither reads AISLE_ENV_BASELINE nor accepts OIDs and
    even asks explicitly for `local`; Python startup compatibility must
    server-validate the historical pin and make the old gate report it."""
    work = tmp_path / "work"
    remote = tmp_path / "origin.git"
    work.mkdir()
    _git(work, "init", "-q")
    _git(work, "branch", "-M", "main")
    _git(work, "config", "user.email", "test@aisle.invalid")
    _git(work, "config", "user.name", "AISLE test")
    (work / "f").write_text("pin\n")
    _git(work, "add", "f")
    _git(work, "commit", "-qm", "pin")
    pin = _git(work, "rev-parse", "HEAD")
    (work / "f").write_text("server head\n")
    _git(work, "commit", "-qam", "head")
    _git(tmp_path, "init", "--bare", "-q", str(remote))
    _git(work, "remote", "add", "origin", str(remote))
    _git(work, "push", "-q", "-u", "origin", "main")

    fake = tmp_path / "fake"
    package = fake / "aisle" / "harness"
    package.mkdir(parents=True)
    (fake / "aisle" / "__init__.py").write_text("")
    (package / "__init__.py").write_text("")
    (package / "rollout.py").write_text(
        "def resolve_trusted_baseline(root):\n"
        "    return 'MOVING_MAIN', None\n\n"
        "def run_gates(root, graph=None, branch='x', no_idea_gate=True, "
        "embodiment='franka', env_baseline='origin/main'):\n"
        "    oid, error = resolve_trusted_baseline(root)\n"
        "    return {'ok': error is None, 'env_baseline': env_baseline, "
        "'env_baseline_oid': oid}\n"
    )
    script = tmp_path / "old_harness.py"
    script.write_text(
        "import json, os, sys\n"
        "from aisle.harness.rollout import run_gates\n"
        "i = sys.argv.index('--env-baseline')\n"
        "selector = sys.argv[i + 1]\n"
        "print(json.dumps(run_gates(os.environ['TEST_REPO'], "
        "env_baseline=selector)))\n"
    )
    compat = tmp_path / "compat"
    compat.mkdir()
    shutil.copyfile(TEMPLATE, compat / "sitecustomize.py")
    env = os.environ.copy()
    env.update(
        {
            "AISLE_ENV_BASELINE": pin,
            "PYTHONPATH": os.pathsep.join((str(compat), str(fake))),
            "TEST_REPO": str(work),
        }
    )

    proc = subprocess.run(
        [sys.executable, str(script), "rollout", "--env-baseline", "local"],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )

    assert json.loads(proc.stdout) == {
        "ok": True,
        "env_baseline": pin,
        "env_baseline_oid": pin,
    }
