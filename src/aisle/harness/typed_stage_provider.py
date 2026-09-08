"""On-demand typed stages for an already validated, controller-bound candidate."""

from __future__ import annotations

import copy
import json
import threading
from pathlib import Path

from aisle.harness.typed_graph_stage import _validation
from aisle.harness.typed_run_prepare import prepare_typed_stages
from aisle.harness.typed_snapshot import _read, verify_typed_validation_snapshot
from aisle.harness.typed_worker_provisioning import provision_typed_workers


class TypedStageProvider:
    """Produce sequential, non-reusable stages without a fixed relaunch count.

    The enclosing session must authorize these inputs, enforce its total resource
    budget and finish prior-launch cleanup before requesting another stage.
    """

    def __init__(
        self,
        *,
        controller_root,
        snapshot,
        snapshot_record,
        validation_output,
        allocation_root,
        evidence,
        hidden_roots,
        runtime_record,
        python,
        python_sha256,
        adapter_sha256,
        timeout_s,
        max_calls=100000,
    ):
        self.controller = Path(controller_root).absolute()
        self.snapshot = Path(snapshot).absolute()
        self.record = copy.deepcopy(snapshot_record)
        self.validation = Path(validation_output).absolute()
        self.allocation = Path(allocation_root).absolute()
        self.evidence = Path(evidence).absolute()
        if str(self.controller) != self.record["controller_root"]:
            raise ValueError("typed stage provider controller differs from snapshot")
        verify_typed_validation_snapshot(self.snapshot, self.record)
        _validation(self.validation, self.record)
        self.hidden = tuple(Path(p).absolute() for p in hidden_roots)
        self.runtime = copy.deepcopy(runtime_record)
        self.worker = dict(
            python=str(python),
            python_sha256=python_sha256,
            adapter_sha256=adapter_sha256,
            timeout_s=timeout_s,
            max_calls=max_calls,
        )
        for root in (self.allocation, self.evidence):
            if root.resolve() != root or root.exists() or root.is_symlink():
                raise ValueError("typed stage provider requires fresh canonical roots")
        protected = (
            self.controller,
            self.snapshot,
            self.validation,
            Path(self.record["participant_root"]),
            *self.hidden,
            *(Path(p) for p in self.runtime["trees"]),
            self.evidence,
        )
        if any(
            self.allocation.is_relative_to(p) or p.is_relative_to(self.allocation)
            for p in protected
        ):
            raise ValueError("typed stage allocation overlaps protected state")
        if any(
            self.evidence.is_relative_to(p) or p.is_relative_to(self.evidence)
            for p in (
                self.controller,
                self.snapshot,
                self.validation,
                Path(self.record["participant_root"]),
                *(Path(p) for p in self.runtime["trees"]),
            )
        ):
            raise ValueError("typed stage evidence overlaps source, validation or runtime")
        self.evidence.mkdir(parents=True, exist_ok=False)
        self.allocation.mkdir(parents=True, exist_ok=False)
        self._next = 0
        self._failed = False
        self._lock = threading.Lock()

    def __call__(self, index):
        with self._lock:
            if self._failed:
                raise ValueError("typed stage provider is terminal after preparation failure")
            if type(index) is not int or index != self._next:
                raise ValueError("typed stage provider requires sequential launch requests")
            self._next += 1
            attempt = self.evidence / f"launch-{index}"
            owned = False
            receipt = {
                "schema_version": "aisle.typed-stage-provisioning.v1",
                "launch": index,
                "snapshot_id": self.record["immutable_id"],
                "ok": False,
                "error": None,
            }
            try:
                attempt.mkdir()
                owned = True
                declarations = provision_typed_workers(
                    snapshot=self.snapshot,
                    snapshot_record=self.record,
                    validation_output=self.validation,
                    allocation_root=self.allocation / f"launch-{index}",
                    evidence=attempt / "workers",
                    hidden_roots=(*self.hidden, self.evidence),
                    runtime_record=self.runtime,
                    **self.worker,
                )
                stages = prepare_typed_stages(
                    controller_root=self.controller,
                    snapshot=self.snapshot,
                    snapshot_record=self.record,
                    validation_output=self.validation,
                    declarations=[declarations],
                    output=attempt / "stages",
                    runtime_record=self.runtime,
                    adapter_sha256=self.worker["adapter_sha256"],
                    protected_roots=(
                        self.controller,
                        self.snapshot,
                        self.validation,
                        Path(self.record["participant_root"]),
                        self.evidence,
                    ),
                )
                selected = stages["stages"][0]
                stage = Path(selected["root"])
                record = json.loads(_read(stage, "stage.json"))
                if record["immutable_id"] != selected["stage_id"]:
                    raise ValueError("typed stage identity changed after preparation")
                receipt.update(ok=True, stage_root=str(stage), stage_id=record["immutable_id"])
                return stage, record
            except BaseException as exc:
                self._failed = True
                receipt["error"] = str(exc) or type(exc).__name__
                raise
            finally:
                if owned:
                    try:
                        (attempt / "result.json").write_text(json.dumps(receipt, allow_nan=False))
                    except BaseException:
                        self._failed = True
                        raise
