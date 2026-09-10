"""Replay provider delivery against the controller's closed byte reference."""

from __future__ import annotations

import hashlib
import json

from aisle.harness.frontend_dispatch_audit import _constant, _object, verify_dispatch_journal
from aisle.harness.provider_response_authority import ProviderResponseAuthority, _require, _response


class _Reservations:
    def __init__(self, dispatch):
        report = verify_dispatch_journal(**dispatch)
        _require(
            report["ok"] and not report["delivery_uncertain"], "invalid provider dispatch journal"
        )
        self.session_id = dispatch["expected"]["session_id"]
        self.rows = []
        self.used = set()
        for number in range(1, dispatch["expected"]["attempts"] + 1):
            prefix = f"{number:08d}"
            self.rows.append(
                (
                    number,
                    json.loads(dispatch["artifacts"][prefix + "-reservation.json"]),
                    dispatch["artifacts"][prefix + ".frame"],
                )
            )

    def dispatch(self, call, frame, deliver):
        matches = [
            (number, row)
            for number, row, source in self.rows
            if row["call"] == call and source == frame
        ]
        _require(len(matches) == 1, "provider item lacks one exact reservation")
        number, row = matches[0]
        _require(
            number not in self.used and row["decision"] == "authorized",
            "provider reservation reused or refused",
        )
        self.used.add(number)
        deliver(frame)
        return {"reservation": row}


def verify_provider_sources(artifacts, *, expected, dispatch, byte_limit, delegated_tools):
    """Identify native dispatch entries without counting delegated calls twice."""
    report = {
        "ok": False,
        "errors": [],
        "native_attempts": [],
        "delegated_calls": [],
        "complete_coverage": False,
        "confinement_verified": False,
    }
    try:
        _require(type(byte_limit) is int and byte_limit > 0, "invalid provider byte limit")
        _require(
            type(artifacts) is dict
            and all(type(k) is str and type(v) is bytes for k, v in artifacts.items()),
            "invalid provider snapshot",
        )
        size = sum(map(len, artifacts.values()))
        _require(size <= byte_limit, "provider snapshot exceeds limit")
        _require(
            type(expected) is dict
            and set(expected)
            == {
                "artifacts",
                "bytes",
                "failure",
                "complete_coverage",
                "confinement_verified",
            },
            "invalid provider reference",
        )
        _require(
            expected["failure"] is None
            and expected["complete_coverage"] is False
            and expected["confinement_verified"] is False,
            "failed or promoted provider evidence",
        )
        _require(
            expected["bytes"] == size
            and expected["artifacts"]
            == {name: hashlib.sha256(raw).hexdigest() for name, raw in artifacts.items()},
            "provider bytes differ from closed reference",
        )

        def row(name):
            return json.loads(artifacts[name], object_pairs_hook=_object, parse_constant=_constant)

        invocation = row("invocation.json")
        _require(
            invocation["schema_version"] == "aisle.provider-relay.v1"
            and invocation["session_id"] == dispatch["expected"]["session_id"]
            and invocation["complete_coverage"] is False
            and invocation["confinement_verified"] is False,
            "invalid provider invocation",
        )
        _require(
            {tuple(tool) for tool in invocation["delegated_tools"]} == set(delegated_tools),
            "provider delegation differs from owned routes",
        )
        requests = (len(artifacts) - 1) // 4
        _require(
            0 < requests <= 10000
            and set(artifacts)
            == {"invocation.json"}
            | {
                f"{i:08d}-{suffix}"
                for i in range(1, requests + 1)
                for suffix in ("request.body", "request.json", "response.sse", "delivery.json")
            },
            "provider source inventory differs",
        )
        ordered = []
        for i in range(1, requests + 1):
            prefix = f"{i:08d}"
            delivery = row(prefix + "-delivery.json")
            _require(
                type(delivery) is dict
                and set(delivery) == {"sequence", "events_returned", "error_type"}
                and delivery["error_type"] is None,
                "failed provider delivery",
            )
            _require(
                type(delivery["sequence"]) is int and type(delivery["events_returned"]) is int,
                "invalid provider delivery count",
            )
            ordered.append((delivery["sequence"], prefix, delivery["events_returned"]))
        ordered.sort()
        _require(
            [order for order, _, _ in ordered] == list(range(1, requests + 1)),
            "provider forwarding sequence differs",
        )
        reservations = _Reservations(dispatch)
        authority = ProviderResponseAuthority(reservations, delegated_tools=delegated_tools)
        delegated_calls = []
        for _, prefix, count in ordered:
            delivered = []
            source = artifacts[prefix + "-response.sse"]
            authority.forward(source, delivered.append)
            _require(len(delivered) == count, "provider delivered prefix differs")
            _, events, calls = _response(source)
            for position, call in calls.items():
                if call[1:] not in delegated_tools:
                    continue
                item = events[position][1]["item"]
                payload = (
                    json.loads(
                        item["arguments"], object_pairs_hook=_object, parse_constant=_constant
                    )
                    if item["type"] == "function_call"
                    else item.get("input")
                )
                delegated_calls.append(
                    {
                        "call_id": call[0],
                        "namespace": call[1],
                        "name": call[2],
                        "kind": call[3],
                        "payload": payload,
                    }
                )
        report.update(
            ok=True, native_attempts=sorted(reservations.used), delegated_calls=delegated_calls
        )
    except (ValueError, TypeError, KeyError, AttributeError, RecursionError) as exc:
        report["errors"].append(str(exc))
    return report
