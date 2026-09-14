"""Arm launch arguments derived from the admitted candidate's launch fields.

MON-3: the launcher binds the candidate's graph or module; MON-4: both arms
receive the same verifier, reset mode, embodiment, seeds and budgets.
"""

from __future__ import annotations

from pathlib import Path

ARMS = ("typed", "monolithic")
OPERATIONS = ("check", "run")


def arm_arguments(
    arm: str,
    operation: str,
    fields: dict,
    view: Path,
    development: dict,
    *,
    root: Path | None = None,
    run_id: str | None = None,
) -> list[str]:
    """`harness` CLI arguments for one arm's check or run, from the launch fields."""
    if arm not in ARMS or operation not in OPERATIONS:
        raise ValueError("arm or operation is unresolved")
    view = Path(view)
    graph = str(view / fields["typed_graph"])
    module = str(view / fields["monolithic_module"])
    if operation == "check":
        if arm == "typed":
            return ["validate", graph, "--root", str(view), "--embodiment", fields["embodiment"]]
        return ["monolith", "check", "--module", module, "--embodiment", fields["embodiment"]]
    if root is None or run_id is None:
        raise ValueError("run arguments require the controller root and run id")
    common = [
        "--reset",
        fields["reset"],
        "--verifier",
        fields["verifier"],
        "--root",
        str(root),
        "--tier",
        fields["tier"],
        "--embodiment",
        fields["embodiment"],
        "--episodes",
        str(len(development["seeds"])),
        "--seeds",
        ",".join(str(seed) for seed in development["seeds"]),
        "--run-id",
        run_id,
        "--timeout-s",
        str(development["timeout_s"]),
    ]
    if arm == "typed":
        return ["rollout", "--graph", graph, *common]
    return [
        "monolith",
        "run",
        "--module",
        module,
        "--template",
        fields["monolithic_template"],
        *common,
    ]
