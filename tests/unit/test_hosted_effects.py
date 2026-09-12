"""MON-12/MON-13: hosted fixture effects bind reserved input and returned search action."""

import hashlib
import json

import pytest
from test_frontend_dispatch import call
from test_provider_response_authority import frames

from aisle.harness.frontend_dispatch import DispatchBudget, DispatchRefused

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("common", [False, True])
@pytest.mark.parametrize(
    "refused,fault",
    [
        (refused, fault)
        for refused in (False, True)
        for fault in (None, "prompt", "effect", "provider", "duplicate", "assistant", "mapping")
    ]
    + [(False, "response")],
)
def test_hosted_fixture_effect_requires_bound_request(tmp_path, refused, fault, common):
    """MON-13: another request or provider cannot qualify the fixture's observable effect."""
    from aisle.harness.frontend_effects import hosted_effect_evidence, hosted_probe

    target, marker = "task.yaml", "# AISLE probe hosted"
    query = hosted_probe(target, marker)
    intent = query
    if common:
        from aisle.harness.frontend_effects import hosted_probe_set

        intent = hosted_probe_set(
            {"typed": target, "monolithic": "other.py" if fault == "mapping" else "agent.py"},
            marker,
        )
    elif fault == "mapping":
        intent = hosted_probe("other.yaml", marker)
    if fault == "duplicate":
        intent += "\n" + intent
    request = json.dumps(
        {
            "model": "fixture",
            "stream": True,
            "tools": [{"type": "web_search"}],
            "input": [
                {
                    "role": "assistant" if fault == "assistant" else "developer",
                    "content": [
                        {"type": "input_text", "text": "other" if fault == "prompt" else intent}
                    ],
                }
            ],
        }
    ).encode()
    output = tmp_path / "dispatch"
    with DispatchBudget(output, session_id="session", ceiling=1) as budget:
        if refused:
            budget.dispatch(call(1), b"prior", lambda _: None)
            with pytest.raises(DispatchRefused):
                budget.hosted_response("request", request, lambda _: pytest.fail("upstream called"))
        else:
            budget.hosted_response(
                "request",
                request,
                lambda _: frames(
                    [
                        {
                            "type": "web_search_call",
                            "id": "search",
                            "status": "completed",
                            "action": {
                                "type": "search",
                                "query": "other" if fault == "response" else query,
                            },
                        }
                    ]
                ),
            )
    before = b"nodes: []\n"
    after = before if refused else before + marker.encode() + b"\n"
    if fault == "effect":
        after += b"unexpected\n"
    artifacts = {
        "frontend-dispatch-reference.json": json.dumps(budget.reference()).encode(),
        "authored/" + target: before,
        "final/" + target: after,
    }
    artifacts.update({"frontend-dispatch/" + p.name: p.read_bytes() for p in output.iterdir()})
    binding = {
        "base_url": "http://127.0.0.1:1/v1",
        "hosted_tool_contract": "aisle.fixture.responses.max_tool_calls.v1",
    }
    if fault == "provider":
        binding = {
            "base_url": "https://api.openai.com/v1",
            "hosted_tool_contract": "openai.responses.max_tool_calls.v1",
        }
    proof = {
        "artifacts": artifacts,
        "record": {
            "arm": "typed",
            "snapshots": {
                phase: {target: {"sha256": hashlib.sha256(raw).hexdigest(), "mode": 0o644}}
                for phase, raw in (("authored", before), ("final", after))
            },
        },
        "admission": {
            "arms": {
                "typed": {"repository": {"editable_allowlist": [target]}},
                "monolithic": {"repository": {"editable_allowlist": ["agent.py"]}},
            },
            "launch_bindings": {"typed": {"provider": binding}},
        },
    }
    args = dict(request_id="request", target=target, marker=marker, refused=refused)
    if fault is None:
        report = hosted_effect_evidence(proof, **args)
        assert report["effect"] == ("unchanged" if refused else "appended")
        assert report["complete_coverage"] is False
    else:
        with pytest.raises(ValueError):
            hosted_effect_evidence(proof, **args)
