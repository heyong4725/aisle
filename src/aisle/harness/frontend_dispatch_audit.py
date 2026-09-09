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
            and set(expected) == {"session_id", "ceiling", "attempts", "reserved", "artifacts"},
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

        def row(name, schema, fields):
            used.add(name)
            data = artifacts[name]
            _require(len(data) <= 65536, "metadata exceeds size limit")
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
        for attempt in range(1, expected["attempts"] + 1):
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
        _require(used == set(artifacts), "unmatched journal artifacts")
        _require(
            reserved == expected["reserved"], "reserved total differs from controller reference"
        )
        result.update(
            ok=True, attempts=expected["attempts"], reserved=reserved, delivery_uncertain=uncertain
        )
    except (ValueError, TypeError, KeyError, RecursionError) as exc:
        result["errors"].append(str(exc))
    return result
