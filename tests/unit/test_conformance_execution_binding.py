"""MON-13: frontend qualification also binds the admitted controller execution context."""

import copy
import hashlib
import json

import pytest
from test_conformance_profile_binding import bound_profile

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "change",
    [
        None,
        "development",
        "tool_runtime",
        "typed_validation",
        "run_controller",
        "confinement_bindings",
        "ambient_bindings",
        "private_roots",
        "prompt_row",
    ],
)
def test_profile_rejects_changed_execution_context(tmp_path, change):
    """MON-13: identical frontend argv cannot substitute different validator or controller
    bindings.
    """
    from aisle.harness.frontend_conformance import execution_digest, read_bound_profile

    root, candidates, launches, reference, _ = bound_profile(tmp_path)
    execution = {
        "typed_validation": {"bundle_manifest": {"immutable_id": "original"}},
        "tool_runtime": {"immutable_id": "runtime"},
    }
    path = root / reference["path"]
    profile = json.loads(path.read_bytes())
    profile["execution_sha256"] = execution_digest(execution)
    path.write_text(json.dumps(profile))
    reference["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    target = copy.deepcopy(execution)
    if change is not None:
        target[change] = "changed"
        with pytest.raises(ValueError, match="execution"):
            read_bound_profile(
                root,
                reference,
                candidate=candidates["typed"],
                launch=launches["typed"],
                arm="typed",
                execution=target,
            )
    else:
        assert (
            read_bound_profile(
                root,
                reference,
                candidate=candidates["typed"],
                launch=launches["typed"],
                arm="typed",
                execution=target,
            )["profile"]
            == profile
        )
