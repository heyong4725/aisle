"""Behavioral reset core (SPEC 040 RST-2) — attempt/fallback semantics.

RST-2: command the robot to return the target box to a sampled shelf
pose, verify with the REALISTIC verifier, retry <=3, then fall back to
teleport with `fallback: true` in the reply metadata — the loop must
NEVER hang on a reset.

This module is the pure lifecycle: attempts are delegated to an injected
`attempt` strategy (motion + realistic verification land in the RST-2
motion PR; until then the production strategy reports failure
immediately, so behavioral requests deterministically fall back to
teleport — the spec's terminal behavior when every attempt fails).
Self-contained inside the frozen reset/ boundary: importing unfrozen
policy code here would let an agent edit reset behavior through a file
the env hash does not cover.
"""

from __future__ import annotations

from dataclasses import dataclass, field

MAX_ATTEMPTS = 3  # RST-2: retry <=3, then teleport fallback


@dataclass(kw_only=True)
class BehavioralOutcome:
    """What the service replies with once the behavioral flow settles."""

    fallback: bool  # True: teleport must run (attempts exhausted/unavailable)
    attempts: int  # attempts actually spent


@dataclass(kw_only=True)
class BehavioralReset:
    """Attempt loop for one behavioral request. `attempt` is the injected
    strategy: () -> bool (True = the box is back on the shelf AND the
    realistic verifier confirmed it). The default strategy is
    `no_motion_available` until the motion PR lands."""

    attempt: object
    attempts_spent: int = field(default=0)

    def run(self) -> BehavioralOutcome:
        while self.attempts_spent < MAX_ATTEMPTS:
            self.attempts_spent += 1
            if self.attempt():
                return BehavioralOutcome(fallback=False, attempts=self.attempts_spent)
        return BehavioralOutcome(fallback=True, attempts=self.attempts_spent)


def no_motion_available() -> bool:
    """PR-1 production strategy: no motion capability is wired yet, so
    every attempt fails and the service falls back to teleport. The
    attempt loop still runs (attempts=3 in the reply) so the fallback
    path is exercised end-to-end from day one."""
    return False


def behavioral_reply_metadata(request_meta: dict, outcome: BehavioralOutcome) -> dict:
    """Reply keys the teleport fallback's reset_done must carry (RST-2):
    the original request correlation plus the behavioral audit trail."""
    return {
        "request_id": request_meta.get("request_id", ""),
        "fallback": outcome.fallback,
        "behavioral_attempts": outcome.attempts,
    }
