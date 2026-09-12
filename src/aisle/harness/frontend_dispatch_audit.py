"""Replay covered-call evidence against a separately retained controller reference.

The caller owns snapshot acquisition and authenticates the expected reference;
this function cannot authenticate a reference supplied by the participant. It
verifies one journal, not route coverage, confinement or campaign admission.
"""

from __future__ import annotations

import hashlib
import json

from aisle.harness.frontend_dispatch import MAX_FRAME_BYTES


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _object(pairs):
    result = {}
    for key, value in pairs:
        _require(key not in result, "duplicate JSON field")
        result[key] = value
    return result


def _constant(value):
    raise ValueError("non-finite JSON value")


def verify_dispatch_journal(artifacts, *, expected, byte_limit):
    """Verify bytes/counts/decisions without promoting partial evidence to totals.

    ``expected`` must be authenticated outside this layer and contain session_id,
    ceiling, attempts, reserved, and the complete filename-to-SHA256 map. The
    caller must bound acquisition before constructing this immutable snapshot.
    A valid uncertain-delivery journal has ok=true and delivery_uncertain=true;
    this is evidence consistency, never permission to continue that session.
    """
    result = {
        "ok": False,
        "errors": [],
        "attempts": None,
        "reserved": None,
        "delivery_uncertain": None,
        "complete_coverage": False,
        "confinement_verified": False,
    }
    try:
        _require(type(byte_limit) is int and byte_limit > 0, "invalid verification byte limit")
        _require(type(artifacts) is dict, "snapshot must be a byte mapping")
        artifacts = dict(artifacts)
        _require(
            all(type(k) is str and type(v) is bytes for k, v in artifacts.items()),
            "snapshot must contain immutable byte values",
        )
        _require(sum(map(len, artifacts.values())) <= byte_limit, "journal exceeds byte limit")
        _require(
            type(expected) is dict
            and set(expected) - {"hosted_requests"}
            == {"session_id", "ceiling", "attempts", "reserved", "artifacts"},
            "invalid controller reference",
        )
        session = expected["session_id"]
        _require(type(session) is str and 0 < len(session) <= 256, "invalid session identity")
        for key in ("ceiling", "attempts", "reserved"):
            _require(type(expected[key]) is int and expected[key] >= 0, "invalid reference count")
        _require(expected["ceiling"] > 0, "invalid reference ceiling")
        _require(
            type(expected["artifacts"]) is dict
            and expected["artifacts"]
            == {name: hashlib.sha256(data).hexdigest() for name, data in artifacts.items()},
            "artifact set or content differs from controller reference",
        )
        used = set()

        def row(name, schema, fields, *, size_limit=65536):
            used.add(name)
            data = artifacts[name]
            _require(len(data) <= size_limit, "metadata exceeds size limit")
            value = json.loads(data, object_pairs_hook=_object, parse_constant=_constant)
            _require(type(value) is dict and set(value) == fields, "invalid record fields")
            _require(value["schema_version"] == schema, "unsupported record schema")
            _require(value["session_id"] == session, "record session differs")
            return value

        authority = row(
            "authority.json",
            "aisle.frontend-dispatch-authority.v1",
            {
                "schema_version",
                "session_id",
                "ceiling",
                "complete_coverage",
                "confinement_verified",
            },
        )
        _require(
            type(authority["ceiling"]) is int
            and authority["ceiling"] == expected["ceiling"]
            and authority["complete_coverage"] is False
            and authority["confinement_verified"] is False,
            "authority differs or promotes unverified coverage",
        )
        seen = set()
        reserved = 0
        uncertain = False
        hosted_count = expected.get("hosted_requests", 0)
        _require(
            type(hosted_count) is int and 0 <= hosted_count <= 10000, "invalid hosted request count"
        )
        hosted_rows = []
        hosted_seen = set()
        for number in range(1, hosted_count + 1):
            prefix = f"hosted-{number:08d}"
            reservation = row(
                prefix + "-reservation.json",
                "aisle.hosted-reservation.v1",
                {
                    "schema_version",
                    "session_id",
                    "request_id",
                    "local_attempts",
                    "allowance",
                    "reserved_before",
                    "request_sha256",
                    "request_bytes",
                },
            )
            request_id = reservation["request_id"]
            _require(
                type(request_id) is str
                and 0 < len(request_id) <= 256
                and request_id not in hosted_seen,
                "invalid hosted request identity",
            )
            hosted_seen.add(request_id)
            for key in ("local_attempts", "allowance", "reserved_before", "request_bytes"):
                _require(
                    type(reservation[key]) is int and reservation[key] >= 0,
                    "invalid hosted reservation count",
                )
            _require(
                reservation["local_attempts"] <= expected["attempts"]
                and (
                    not hosted_rows
                    or reservation["local_attempts"] >= hosted_rows[-1][1]["local_attempts"]
                ),
                "hosted transaction ordering differs",
            )
            hosted_rows.append((prefix, reservation))
        hosted_index = 0
        completed_hosted = []
        budget_refusals = []

        def settle_hosted(local_attempts):
            nonlocal hosted_index, reserved, uncertain
            from aisle.harness.provider_hosted import hosted_calls, hosted_request

            while (
                hosted_index < len(hosted_rows)
                and hosted_rows[hosted_index][1]["local_attempts"] == local_attempts
            ):
                _require(not uncertain, "further request after uncertain hosted work")
                prefix, reservation = hosted_rows[hosted_index]
                hosted_index += 1
                allowance = reservation["allowance"]
                _require(
                    reservation["reserved_before"] == reserved
                    and allowance <= expected["ceiling"] - reserved,
                    "hosted allowance exceeds shared budget",
                )
                frame_name = prefix + "-request.frame"
                frame = artifacts[frame_name]
                used.add(frame_name)
                _require(
                    len(frame) <= MAX_FRAME_BYTES
                    and len(frame) == reservation["request_bytes"]
                    and hashlib.sha256(frame).hexdigest() == reservation["request_sha256"],
                    "hosted request frame differs",
                )
                request = json.loads(frame, object_pairs_hook=_object, parse_constant=_constant)
                _require(
                    type(request) is dict
                    and type(request.get("max_tool_calls")) is int
                    and request["max_tool_calls"] == allowance,
                    "upstream hosted limit differs",
                )
                del request["max_tool_calls"]
                prepared, _ = hosted_request(json.dumps(request).encode(), allowance)
                _require(prepared == frame, "unsupported hosted request encoding")
                settlement = row(
                    prefix + "-settlement.json",
                    "aisle.hosted-settlement.v1",
                    {
                        "schema_version",
                        "session_id",
                        "request_id",
                        "status",
                        "error_type",
                        "calls",
                        "reserved_after",
                        "response_sha256",
                        "response_bytes",
                    },
                    size_limit=MAX_FRAME_BYTES,
                )
                _require(
                    settlement["request_id"] == reservation["request_id"]
                    and type(settlement["reserved_after"]) is int,
                    "hosted settlement identity differs",
                )
                response_name = prefix + "-response.frame"
                source = artifacts.get(response_name)
                if source is not None:
                    used.add(response_name)
                    _require(
                        len(source) <= MAX_FRAME_BYTES
                        and type(settlement["response_bytes"]) is int
                        and settlement["response_bytes"] == len(source)
                        and settlement["response_sha256"] == hashlib.sha256(source).hexdigest(),
                        "hosted response frame differs",
                    )
                else:
                    _require(
                        settlement["response_sha256"] is None
                        and settlement["response_bytes"] is None,
                        "missing hosted response",
                    )
                status = settlement["status"]
                if allowance == 0:
                    _require(
                        reserved == expected["ceiling"]
                        and status == "refused"
                        and source is None
                        and settlement["calls"] == []
                        and settlement["error_type"] is None,
                        "invalid exhausted hosted request",
                    )
                    budget_refusals.append(
                        {
                            "kind": "hosted",
                            "reservation": prefix + "-reservation.json",
                            "request_id": reservation["request_id"],
                        }
                    )
                elif status == "completed":
                    _require(
                        source is not None and settlement["error_type"] is None,
                        "incomplete hosted settlement",
                    )
                    calls = hosted_calls(source)
                    identities = {(call["turn_id"], call["call_id"]) for call in calls}
                    _require(
                        len(calls) <= allowance
                        and settlement["calls"] == calls
                        and not identities & seen,
                        "hosted call count or identity differs",
                    )
                    seen.update(identities)
                    completed_hosted.extend(calls)
                    reserved += len(calls)
                else:
                    _require(
                        status == "uncertain"
                        and settlement["calls"] is None
                        and type(settlement["error_type"]) is str
                        and 0 < len(settlement["error_type"]) <= 256,
                        "invalid uncertain hosted settlement",
                    )
                    reserved += allowance
                    uncertain = True
                _require(settlement["reserved_after"] == reserved, "hosted settled budget differs")

        for attempt in range(1, expected["attempts"] + 1):
            settle_hosted(attempt - 1)
            _require(not uncertain, "further call after uncertain delivery")
            prefix = f"{attempt:08d}"
            frame_name = f"{prefix}.frame"
            frame = artifacts[frame_name]
            used.add(frame_name)
            _require(len(frame) <= MAX_FRAME_BYTES, "call frame exceeds size limit")
            digest = hashlib.sha256(frame).hexdigest()
            reservation = row(
                f"{prefix}-reservation.json",
                "aisle.frontend-dispatch-reservation.v1",
                {
                    "schema_version",
                    "session_id",
                    "attempt",
                    "call",
                    "frame_sha256",
                    "frame_bytes",
                    "decision",
                    "reason",
                    "reserved_after",
                },
            )
            for key in ("attempt", "frame_bytes", "reserved_after"):
                _require(type(reservation[key]) is int, "invalid reservation count")
            _require(
                reservation["attempt"] == attempt
                and reservation["frame_bytes"] == len(frame)
                and reservation["frame_sha256"] == digest,
                "reservation/frame identity differs",
            )
            call = reservation["call"]
            _require(
                type(call) is dict
                and set(call) == {"turn_id", "call_id", "tool_name"}
                and all(type(v) is str and 0 < len(v) <= 256 for v in call.values()),
                "invalid normalized call identity",
            )
            identity = (call["turn_id"], call["call_id"])
            reason = None
            if identity in seen:
                reason = "call identity replay refused"
            elif reserved >= expected["ceiling"]:
                reason = "dispatch budget exhausted"
            seen.add(identity)
            if reason is None:
                reserved += 1
            _require(
                reservation["decision"] == ("authorized" if reason is None else "refused")
                and reservation["reason"] == reason
                and reservation["reserved_after"] == reserved,
                "reservation decision or budget counter differs",
            )
            if reason == "dispatch budget exhausted":
                budget_refusals.append(
                    {
                        "kind": "local",
                        "reservation": f"{prefix}-reservation.json",
                        "call": call,
                    }
                )
            terminal_names = [f"{prefix}-delivery.json", f"{prefix}-delivery-error.json"]
            present = [name for name in terminal_names if name in artifacts]
            _require(bool(present) == (reason is None), "missing or unauthorized delivery record")
            for name in present:
                delivery = row(
                    name,
                    "aisle.frontend-dispatch-delivery.v1",
                    {
                        "schema_version",
                        "session_id",
                        "attempt",
                        "frame_sha256",
                        "status",
                        "error_type",
                    },
                )
                _require(
                    type(delivery["attempt"]) is int
                    and delivery["attempt"] == attempt
                    and delivery["frame_sha256"] == digest,
                    "delivery identity differs",
                )
                failed = delivery["status"] == "uncertain"
                _require(
                    (
                        failed
                        and type(delivery["error_type"]) is str
                        and 0 < len(delivery["error_type"]) <= 256
                    )
                    or (delivery["status"] == "returned" and delivery["error_type"] is None),
                    "invalid delivery outcome",
                )
                _require(
                    name != terminal_names[1] or failed, "supplemental delivery must be uncertain"
                )
                _require(
                    not (len(present) == 2 and name == terminal_names[0] and failed),
                    "supplemental receipt requires a returned primary delivery",
                )
                uncertain = uncertain or failed
        settle_hosted(expected["attempts"])
        _require(hosted_index == len(hosted_rows), "unmatched hosted transactions")
        _require(used == set(artifacts), "unmatched journal artifacts")
        _require(
            reserved == expected["reserved"], "reserved total differs from controller reference"
        )
        result.update(
            ok=True,
            attempts=expected["attempts"],
            reserved=reserved,
            delivery_uncertain=uncertain,
            budget_refusals=budget_refusals,
        )
        if hosted_count:
            result["hosted_calls"] = completed_hosted
    except (ValueError, TypeError, KeyError, RecursionError) as exc:
        result["errors"].append(str(exc))
    return result
