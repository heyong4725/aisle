"""MON-2/MON-6/MON-13: typed workers receive pinned dependencies and authored overlays."""

import subprocess
import sys

import pytest
from test_turn_node import Raw
from test_typed_validation_snapshot import ROOT, _view

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "name", ["segmented_pose", "grasp_topdown", "ik_trajectory", "task_state_machine"]
)
def test_unchanged_typed_script_starts_from_closed_bundle(tmp_path, name):
    """MON-2/MON-6: baseline startup resolves bundled helpers without host imports."""
    from aisle.harness.typed_execution_bundle import build_execution_bundle
    from aisle.harness.typed_node_supervisor import supervise_typed_worker
    from aisle.turn_node import Node

    view = _view(tmp_path)
    bundle = tmp_path / "bundle"
    record = build_execution_bundle(ROOT, view, bundle)
    source = (bundle / f"src/aisle/nodes/{name}.py").read_text()
    bootstrap = (
        "import sys; sys.path.insert(0, sys.argv[1]); "
        "from aisle.harness.typed_node_worker import serve; "
        "raise SystemExit(serve(sys.stdin.buffer, sys.stdout.buffer, sys.stderr))"
    )
    raw = Raw([])
    node = Node(raw, {})
    with (tmp_path / "stderr.log").open("wb") as log:
        process = subprocess.Popen(
            [sys.executable, "-I", "-B", "-c", bootstrap, str(bundle / "src")],
            cwd=bundle,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=log,
            start_new_session=True,
        )
        result = supervise_typed_worker(
            process,
            node,
            source,
            "aisle.nodes." + name,
            tmp_path / "evidence",
            outputs=set(),
            timeout_s=10,
        )
    assert result["ok"], (result, (tmp_path / "stderr.log").read_text())
    assert result["input_exhausted"]
    assert record["files"][f"src/aisle/nodes/{name}.py"]["origin"] == "participant"
    assert not (bundle / "src/aisle/turn_node.py").exists()
    assert not (bundle / "src/aisle/harness/typed_node_supervisor.py").exists()
    assert not (bundle / ".git").exists()


def test_bundle_copies_candidate_without_following_its_imports(tmp_path):
    """MON-6: authored source cannot cause the builder to copy new private dependencies."""
    from aisle.harness.typed_execution_bundle import build_execution_bundle

    view = _view(tmp_path)
    source = view / "src/aisle/nodes/segmented_pose.py"
    source.write_text("import aisle.harness.matched_session\n")
    bundle = tmp_path / "bundle"
    build_execution_bundle(ROOT, view, bundle)
    assert (bundle / source.relative_to(view)).read_bytes() == source.read_bytes()
    assert not (bundle / "src/aisle/harness/matched_session.py").exists()


def test_execution_bundle_refuses_added_or_changed_files(tmp_path):
    """MON-13: an execution receipt binds a closed read-only inventory."""
    from aisle.harness.typed_execution_bundle import (
        ExecutionBundleError,
        build_execution_bundle,
        verify_execution_bundle,
    )

    bundle = tmp_path / "bundle"
    record = build_execution_bundle(ROOT, _view(tmp_path), bundle)
    extra = bundle / "src/aisle/turn_node.py"
    extra.write_text("raise RuntimeError('not a worker dependency')")
    with pytest.raises(ExecutionBundleError):
        verify_execution_bundle(bundle, record)
    extra.unlink()
    source = bundle / "src/aisle/nodes/segmented_pose.py"
    source.chmod(0o644)
    source.write_text("changed")
    with pytest.raises(ExecutionBundleError):
        verify_execution_bundle(bundle, record)


@pytest.mark.parametrize("mutation", ["symlink", "empty_directory", "missing", "mode"])
def test_bundle_rejects_non_content_drift(tmp_path, mutation):
    """MON-13: redirection, extra directories, missing files and writable modes fail."""
    from aisle.harness.typed_execution_bundle import (
        ExecutionBundleError,
        build_execution_bundle,
        verify_execution_bundle,
    )

    bundle = tmp_path / "bundle"
    record = build_execution_bundle(ROOT, _view(tmp_path), bundle)
    source = bundle / "src/aisle/nodes/segmented_pose.py"
    if mutation == "symlink":
        source.unlink()
        source.symlink_to(ROOT / "src/aisle/nodes/segmented_pose.py")
    elif mutation == "empty_directory":
        (bundle / "extra").mkdir()
    elif mutation == "missing":
        source.unlink()
    else:
        source.chmod(0o644)
    with pytest.raises(ExecutionBundleError):
        verify_execution_bundle(bundle, record)


def test_bundle_dependencies_are_bound_by_session_identity():
    """MON-13: the shared admission identity includes every trusted bundle input."""
    from aisle.harness.matched_session import CONTROLLER_FILES
    from aisle.harness.typed_execution_bundle import CONTROLLER_FILES as DEPENDENCIES

    assert set(DEPENDENCIES) <= set(CONTROLLER_FILES)
    assert "src/aisle/harness/typed_execution_bundle.py" in CONTROLLER_FILES
