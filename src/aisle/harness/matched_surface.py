"""Named controller-owned task surfaces for matched engineering preparation.

Selecting paths does not attest a task, qualify frontend coverage or authorize
collection. Callers must bind the selected files in their ordinary receipts.
"""

from dataclasses import dataclass

LEGACY_SURFACE = "t1-l1-v1"
PILOT_L2_SURFACE = "t1-l2-pilot-v1"


@dataclass(frozen=True)
class TaskSurface:
    identity: str
    docs_directory: str
    typed_graph: str
    typed_turn_plan: str
    monolithic_graph: str
    monolithic_module: str
    perception_source: str

    @property
    def analysis_directory(self) -> str:
        return (
            "analysis/monolithic-control/pilot-l2"
            if self.identity == PILOT_L2_SURFACE
            else "analysis/monolithic-control"
        )

    @property
    def participant_files(self) -> tuple[str, ...]:
        return (
            self.perception_source,
            "src/aisle/nodes/grasp_topdown.py",
            "src/aisle/nodes/ik_trajectory.py",
            "src/aisle/nodes/task_state_machine.py",
        )

    @property
    def extra_controller_files(self) -> tuple[str, ...]:
        # L2 imports the shared geometry, but does not author the L1 estimator.
        return ("src/aisle/nodes/segmented_pose.py",) if self.identity == PILOT_L2_SURFACE else ()


_LEGACY = TaskSurface(
    LEGACY_SURFACE,
    "docs/monolithic",
    "graphs/expert_t1.yaml",
    "graphs/turn_plans/expert_t1.json",
    "graphs/monolithic_t1.yaml",
    "experts/monolithic/expert_t1.py",
    "src/aisle/nodes/segmented_pose.py",
)
_PILOT_L2 = TaskSurface(
    PILOT_L2_SURFACE,
    "docs/monolithic/pilot-l2",
    "graphs/pilot_t1_l2_typed.yaml",
    "graphs/turn_plans/pilot_t1_l2_typed.json",
    "graphs/pilot_t1_l2_monolithic.yaml",
    "experts/monolithic/pilot_t1_l2.py",
    "src/aisle/nodes/l2_pose.py",
)


def task_surface(identity: str = LEGACY_SURFACE) -> TaskSurface:
    """Reject an explicit unknown selection instead of falling back to L1."""
    if type(identity) is str:
        if identity == LEGACY_SURFACE:
            return _LEGACY
        if identity == PILOT_L2_SURFACE:
            return _PILOT_L2
    raise ValueError("unsupported matched task surface")


def record_surface(record: dict) -> TaskSurface:
    """Resolve the named selection after the caller verifies its receipt."""
    return task_surface(record.get("task_surface", LEGACY_SURFACE))


def development_surface(development: dict | None) -> TaskSurface:
    """Resolve an optional development declaration without accepting an invalid selection."""
    if development is None:
        return task_surface()
    if type(development) is not dict:
        raise ValueError("invalid development task surface declaration")
    return record_surface(development)
