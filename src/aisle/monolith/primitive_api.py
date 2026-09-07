"""Data-only declaration of the monolithic primitive request surface."""

# These sets describe the public API, never arbitrary getattr from peer input.
READS = {
    "primitives": {"api_version", "embodiment", "med_names", "meds", "physics", "home", "layout"},
    "pose": {"target"},
    "grasp": {"grasp", "approach_m", "place_tcp_z", "front"},
    "staged": {"ok", "error", "stages"},
    "stage": {"name", "path", "gripper", "settle_s", "vel", "track_tol", "q"},
    "streamer": {"done"},
}
CALLS = {
    "primitives": {"pose_session", "plan_grasp", "staged_plan", "streamer", "describe"},
    "pose": {"on_bridge_info", "on_target_request", "on_seg", "on_depth", "on_reset_done"},
    "grasp": set(),
    "staged": set(),
    "stage": set(),
    "streamer": {"step"},
}
CREATES = {
    "pose_session": "pose",
    "plan_grasp": "grasp",
    "staged_plan": "staged",
    "streamer": "streamer",
}
