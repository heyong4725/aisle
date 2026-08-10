"""The frame-pairing session lifecycle shared by every estimated-pose rung.

Extracted VERBATIM from segmented_pose's L1Session once L2 needed the same
rules (TC-9): seven review rounds hardened this exact lifecycle —
duplicating it per rung would re-open every closed defect. The rules:

* episode boundary (T08 parity with oracle-pose): reset_done clears the
  active target AND both frame buffers — obs and depth ride separate dora
  channels with no cross-channel ordering, so a pre-reset frame's twin can
  drain after the boundary, and stamps cannot disambiguate because the
  bridge never rewinds sim_time_ns across a teleport (PR #133/#134
  round-2 reviews, executed repro);
* ONE publish per target_request: a completed plan can never be
  re-triggered by the still-flowing frame stream, but a REFUSED estimate
  keeps the request pending so a transient occlusion or a low-margin
  detection retries on the next frame while a persistent one times out
  honestly;
* unknown meds refused ONCE at request time (L0 parity with oracle-pose),
  never accepted into a state that refuses per-frame or KeyErrors;
* stamp pairing: the observation frame and depth are consumed only as a
  same-stamp pair from one render pass, buffered SYMMETRICALLY so
  publication does not depend on which of the co-scheduled frames dora
  delivers first; unstamped frames (negative sentinel) never pair;
* reset WATERMARK (PR #139 round-2 review): clearing the buffers at
  reset_done defends against ONE straggler half, but a slow estimator
  (L2's 0.7 s detector) backs the queues up ~30 messages deep, so
  COMPLETE pre-reset pairs can drain after the boundary and pair with
  each other — stamps match and sim time never rewinds across a
  teleport, so only a watermark can tell the epochs apart. Frames at or
  below the newest stamp seen before the reset never attempt.

Subclasses implement `_estimate(obs, depth) -> dict | None` (raising
PoseRefused to keep the request pending) — L1 masks ground-truth
segmentation, L2 detects on rgb.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(kw_only=True)
class FramePairSession:
    meds: dict
    calibration: dict | None = None
    target: str | None = None
    pending: bool = False
    latest_depth: tuple[int, np.ndarray] | None = None
    latest_obs: tuple[int, np.ndarray] | None = None
    newest_stamp_ns: int = -1
    reset_watermark_ns: int = -1

    def on_bridge_info(self, info: dict) -> None:
        self.calibration = info["calibration"]

    def on_target_request(self, request: dict) -> bool:
        """False for a med not in the scene manifest — refused ONCE, loudly,
        by the caller."""
        med = request.get("target_med")
        if med not in self.meds:
            return False
        self.target = med
        self.pending = True
        return True

    def on_reset_done(self) -> None:
        self.target = None
        self.pending = False
        self.latest_depth = None
        self.latest_obs = None
        self.reset_watermark_ns = self.newest_stamp_ns

    def on_depth(self, sim_time_ns: int, depth: np.ndarray) -> dict | None:
        """Buffer the frame; publishable estimate if its obs twin is here."""
        self.newest_stamp_ns = max(self.newest_stamp_ns, sim_time_ns)
        self.latest_depth = (sim_time_ns, depth)
        if (
            self.latest_obs is not None
            and self.latest_obs[0] == sim_time_ns >= 0
            and sim_time_ns > self.reset_watermark_ns
        ):
            return self._gated_estimate(self.latest_obs[1], depth)
        return None

    def on_obs(self, sim_time_ns: int, obs: np.ndarray) -> dict | None:
        """Buffer the observation frame (seg at L1, rgb at L2); publishable
        estimate if its depth twin is already here, or None when there is
        nothing to do. Raises PoseRefused when the frame cannot support a
        trustworthy pose; the request then STAYS pending."""
        self.newest_stamp_ns = max(self.newest_stamp_ns, sim_time_ns)
        self.latest_obs = (sim_time_ns, obs)
        if (
            self.latest_depth is not None
            and self.latest_depth[0] == sim_time_ns >= 0
            and sim_time_ns > self.reset_watermark_ns
        ):
            return self._gated_estimate(obs, self.latest_depth[1])
        return None

    def _gated_estimate(self, obs: np.ndarray, depth: np.ndarray) -> dict | None:
        if not (self.pending and self.target and self.calibration is not None):
            return None
        out = self._estimate(obs, depth)
        if out is not None:
            self.pending = False
        return out

    def _estimate(self, obs: np.ndarray, depth: np.ndarray) -> dict | None:
        raise NotImplementedError
