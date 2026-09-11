"""MON-12/MON-13: hosted provider work has one matching owned frontend lifecycle."""

import copy
import hashlib
import json

import pytest
from test_app_server_source_audit import _audit, _case
from test_hosted_dispatch import REQUEST, hosted_frame
from test_provider_response_authority import frames

from aisle.harness.frontend_app_server import dynamic_tools
from aisle.harness.frontend_dispatch import DispatchBudget

pytestmark = pytest.mark.unit


def test_unqualified_historical_search_observation_stays_partial():
    """MON-12/MON-13: a legacy partial profile does not acquire the hosted contract
    retroactively.
    """
    records, grants = _case()
    records["00000006-received.json"] = records["00000005-received.json"]
    records["00000005-received.json"] = {
        "method": "item/completed",
        "params": {
            "threadId": "thread",
            "turnId": "turn",
            "item": {"type": "webSearch", "id": "old"},
        },
    }
    report = _audit(records, grants)
    assert report["ok"] and report["complete_coverage"] is False, report


@pytest.mark.parametrize("fault", [None, "missing", "duplicate", "identity", "action", "scope"])
def test_hosted_source_requires_matching_frontend_events(tmp_path, fault):
    """MON-13: rehashed transcripts cannot omit or alter the hosted call's owned lifecycle."""
    records, grants = _case()
    params = {"dynamicTools": dynamic_tools(("check",))}
    records["invocation.json"]["thread_params"] = params
    records["00000003-sent.json"]["params"] = params
    grants[0]["session_id"] = "session"
    events = []
    for phase in ("started", "completed"):
        events.append(
            {
                "method": "item/" + phase,
                "params": {
                    "threadId": "thread",
                    "turnId": "turn",
                    "item": {
                        "type": "webSearch",
                        "id": "search-0",
                        "query": "fixture",
                        "action": {"type": "search", "query": "fixture", "queries": None},
                        "results": None,
                    },
                },
            }
        )
    if fault == "missing":
        events.pop()
    elif fault == "duplicate":
        events.append(copy.deepcopy(events[-1]))
    elif fault == "identity":
        events[-1]["params"]["item"]["id"] = "other"
    elif fault == "action":
        events[-1]["params"]["item"]["action"]["query"] = "changed"
    elif fault == "scope":
        events[-1]["params"]["threadId"] = "other"
    terminal = records.pop("00000005-received.json")
    for number, event in enumerate([*events, terminal], 5):
        records[f"{number:08d}-received.json"] = event
    frame = json.dumps(records["00000004-received.json"]).encode() + b"\n"
    provider_sources = [
        frames(
            [
                {
                    "id": "fc",
                    "type": "function_call",
                    "call_id": "call",
                    "namespace": "harness",
                    "name": "check",
                    "arguments": "{}",
                }
            ],
            response_id="first",
        ),
        hosted_frame(1),
    ]
    output = tmp_path / "dispatch"
    with DispatchBudget(output, session_id="session", ceiling=2) as budget:
        budget.dispatch(grants[0]["call"], frame, lambda _: None)
        budget.hosted_response("00000002", REQUEST, lambda _: provider_sources[1])
    provider = {
        "invocation.json": json.dumps(
            {
                "schema_version": "aisle.provider-relay.v1",
                "session_id": "session",
                "binding": {
                    "base_url": "http://127.0.0.1:1234/v1",
                    "hosted_tool_contract": "aisle.fixture.responses.max_tool_calls.v1",
                },
                "delegated_tools": [["harness", "check", "function_call"]],
                "complete_coverage": False,
                "confinement_verified": False,
            }
        ).encode()
    }
    for number, source in enumerate(provider_sources, 1):
        prefix = f"{number:08d}"
        provider[prefix + "-request.body"] = (
            REQUEST if number == 2 else b'{"stream":true,"tools":[]}'
        )
        provider[prefix + "-request.json"] = (
            b'{"content_encoding":"identity","authorization_present":false}'
        )
        provider[prefix + "-response.sse"] = source
        provider[prefix + "-delivery.json"] = json.dumps(
            {"sequence": number, "events_returned": 3, "error_type": None}
        ).encode()
    report = _audit(
        records,
        grants,
        dispatch_ceiling=2,
        dispatch={
            "artifacts": {path.name: path.read_bytes() for path in output.iterdir()},
            "expected": budget.reference(),
            "byte_limit": 100000,
        },
        provider={
            "artifacts": provider,
            "byte_limit": 100000,
            "expected": {
                "artifacts": {
                    name: hashlib.sha256(raw).hexdigest() for name, raw in provider.items()
                },
                "bytes": sum(map(len, provider.values())),
                "failure": None,
                "complete_coverage": False,
                "confinement_verified": False,
            },
        },
    )
    assert report["ok"] is (fault is None), report
