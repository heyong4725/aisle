"""agent_adapters — the four agent seams as one registry (issue #285 C2).

`tools/campaign.py` grew four `if agent ==` seams: the session command,
the two token parsers (HAR-5 `tokens_new`; ADR-43 `tokens_generated`),
the credential lifecycle, and the treatment-identity extras. A4 already
compares two agents and #285 adds a third KIND (locally hosted), so the
seams become one adapter record per agent.

Design constraints, tested in tests/unit/test_campaign.py:
- The claude/codex adapters are BYTE-IDENTICAL delegations to the
  existing campaign.py functions — no recorded arm's semantics move.
- ADR-43 is structural here: each adapter names its `enforcement_unit`
  (`tokens_new` for API agents — the vendor-shaped ledger the A3/A4/A5
  numbers mean; `tokens_generated` for local arms — output tokens, the
  cross-arm unit). A comparison spanning kinds must cite
  tokens_generated, and the record carries both.
- A local adapter has NO credential seam: `seed_credentials` records
  that fact instead of copying nothing silently.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "tools"))

import campaign as _campaign  # noqa: E402


def parse_generated_local(lines: list[str]) -> int:
    """The local driver emits claude-shaped assistant/usage events (a
    deliberate choice so ONE stream shape serves the tee/ceiling path);
    output tokens only, per ADR-43."""
    total = 0
    for line in lines:
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict) or event.get("type") != "assistant":
            continue
        usage = (event.get("message") or {}).get("usage") or {}
        value = usage.get("output_tokens", 0)
        total += int(value) if isinstance(value, (int, float)) else 0
    return total


def local_cmd(model: str, prompt: str) -> list[str]:
    return [
        sys.executable,
        str(REPO_ROOT / "tools" / "local_agent.py"),
        "--model",
        model,
        "-p",
        prompt,
    ]


@dataclass(frozen=True)
class AgentAdapter:
    name: str
    cmd: Callable[[str, str], list[str]]  # (model, prompt) -> argv
    parse_usage: Callable[[list[str]], int]  # the ledger's enforcement stream
    parse_generated: Callable[[list[str]], int]  # ADR-43 cross-arm unit
    enforcement_unit: str  # "tokens_new" | "tokens_generated"
    login_dir: Path | None
    cred_name: str | None
    login_hint: str
    # treatment-identity keys BEYOND campaign.TREATMENT_IDENTITY that this
    # kind must pin (a local arm's sampling seed/quantization are treatment)
    treatment_extras: tuple = field(default=())


ADAPTERS: dict[str, AgentAdapter] = {
    "claude": AgentAdapter(
        name="claude",
        cmd=lambda model, prompt: _campaign.agent_cmd_campaign("claude", model, prompt),
        parse_usage=_campaign.PARSE_USAGE["claude"],
        parse_generated=_campaign.PARSE_GENERATED["claude"],
        enforcement_unit="tokens_new",
        login_dir=_campaign.CAMPAIGN_LOGIN["claude"][0],
        cred_name=_campaign.CAMPAIGN_LOGIN["claude"][1],
        login_hint=(
            f"CLAUDE_CONFIG_DIR={_campaign.CAMPAIGN_LOGIN['claude'][0]} claude  # then /login"
        ),
    ),
    "codex": AgentAdapter(
        name="codex",
        cmd=lambda model, prompt: _campaign.agent_cmd_campaign("codex", model, prompt),
        parse_usage=_campaign.PARSE_USAGE["codex"],
        parse_generated=_campaign.PARSE_GENERATED["codex"],
        enforcement_unit="tokens_new",
        login_dir=_campaign.CAMPAIGN_LOGIN["codex"][0],
        cred_name=_campaign.CAMPAIGN_LOGIN["codex"][1],
        login_hint=f"CODEX_HOME={_campaign.CAMPAIGN_LOGIN['codex'][0]} codex login",
    ),
    "local": AgentAdapter(
        name="local",
        cmd=local_cmd,
        parse_usage=parse_generated_local,  # ADR-43: no vendor meter —
        parse_generated=parse_generated_local,  # enforcement IS generated
        enforcement_unit="tokens_generated",
        login_dir=None,
        cred_name=None,
        login_hint="none: local inference has no credential",
        treatment_extras=("local_backend", "local_model_digest", "sampling_seed"),
    ),
}


def seed_credentials(adapter: AgentAdapter, env: dict) -> tuple[dict | None, str | None]:
    """The credential seam through the adapter: API adapters delegate to
    campaign.seed_session_credentials unchanged; a local adapter records
    the absence explicitly (never a silent no-op)."""
    if adapter.login_dir is None:
        return {"credentials": "none (local adapter)"}, None
    return _campaign.seed_session_credentials(adapter.name, env)


def treatment_identity(adapter: AgentAdapter) -> tuple:
    """campaign.TREATMENT_IDENTITY plus the adapter's kind-specific pins."""
    return tuple(_campaign.TREATMENT_IDENTITY) + tuple(adapter.treatment_extras)
