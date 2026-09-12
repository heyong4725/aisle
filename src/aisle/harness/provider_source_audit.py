"""Replay provider delivery against the controller's closed byte reference."""

from __future__ import annotations

import hashlib
import json

from aisle.harness.frontend_dispatch_audit import _constant, _object, verify_dispatch_journal
from aisle.harness.provider_response_authority import ProviderResponseAuthority, _require, _response


class _ReplayedRefusal(Exception):
    """Internal replay stop at an authenticated exhausted reservation."""


class _ReplayedInterruption(Exception):
    """Internal stop at an authenticated cancelled native delivery."""


class _Reservations:
    def __init__(
        self, dispatch, *, allow_refusal=False, uncertain_attempts=frozenset(), interruption=None
    ):
        report = verify_dispatch_journal(**dispatch)
        uncertain = {
            json.loads(raw)["attempt"]
            for name, raw in dispatch["artifacts"].items()
            if name.endswith(("-delivery.json", "-delivery-error.json"))
            and json.loads(raw)["status"] == "uncertain"
        }
        _require(
            report["ok"]
            and uncertain == set(uncertain_attempts)
            and (not report["delivery_uncertain"] or bool(uncertain)),
            "invalid provider dispatch journal",
        )
        self.interruption = interruption
        if interruption is not None:
            attempt, forwarded = interruption
            _require(
                type(attempt) is int and attempt > 0 and type(forwarded) is bool,
                "invalid interrupted delivery selector",
            )
            delivery = json.loads(dispatch["artifacts"][f"{attempt:08d}-delivery.json"])
            _require(
                delivery["status"] == "uncertain" and delivery["error_type"] == "CancelledError",
                "interrupted delivery cause differs",
            )
        self.session_id = dispatch["expected"]["session_id"]
        self.rows = []
        self.used = set()
        self.allow_refusal = allow_refusal
        self.refused = []
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
        if (
            self.allow_refusal
            and number not in self.used
            and number not in self.refused
            and row["decision"] == "refused"
            and row["reason"] == "dispatch budget exhausted"
        ):
            self.refused.append(number)
            raise _ReplayedRefusal()
        _require(
            number not in self.used and row["decision"] == "authorized",
            "provider reservation reused or refused",
        )
        self.used.add(number)
        if self.interruption is not None and number == self.interruption[0]:
            if self.interruption[1]:
                deliver(frame)
            raise _ReplayedInterruption()
        deliver(frame)
        return {"reservation": row}


def verify_provider_sources(artifacts, *, expected, dispatch, byte_limit, delegated_tools):
    """Identify native dispatch entries without counting delegated calls twice."""
    return _verify_provider_sources(
        artifacts,
        expected=expected,
        dispatch=dispatch,
        byte_limit=byte_limit,
        delegated_tools=delegated_tools,
    )


def verify_provider_interruption_sources(
    artifacts, *, expected, dispatch, byte_limit, delegated_tools, failed_attempt, forwarded
):
    """Replay a cancelled native delivery without refunding or claiming execution success."""
    return _verify_provider_sources(
        artifacts,
        expected=expected,
        dispatch=dispatch,
        byte_limit=byte_limit,
        delegated_tools=delegated_tools,
        uncertain_attempts=frozenset({failed_attempt}),
        interruption=(failed_attempt, forwarded),
    )


def verify_provider_replay_sources(artifacts, *, expected, dispatch, byte_limit, delegated_tools):
    """Authenticate terminal identity replay rejection; session effects need separate auditing."""
    return _verify_provider_sources(
        artifacts,
        expected=expected,
        dispatch=dispatch,
        byte_limit=byte_limit,
        delegated_tools=delegated_tools,
        allow_replay=True,
    )


def verify_provider_failure_prefix(
    artifacts, *, expected, dispatch, byte_limit, delegated_tools, failed_attempt
):
    """Replay provider work while preserving one separately audited uncertain harness delivery."""
    return _verify_provider_sources(
        artifacts,
        expected=expected,
        dispatch=dispatch,
        byte_limit=byte_limit,
        delegated_tools=delegated_tools,
        uncertain_attempts=frozenset({failed_attempt}),
    )


def link_native_refusal_sources(artifacts, *, expected, dispatch, byte_limit, delegated_tools):
    """Replay provider bytes through the exhausted call; this is not full session qualification."""
    return _verify_provider_sources(
        artifacts,
        expected=expected,
        dispatch=dispatch,
        byte_limit=byte_limit,
        delegated_tools=delegated_tools,
        allow_refusal=True,
    )


def verify_hosted_refusal_prefix(artifacts, *, expected, dispatch, byte_limit, delegated_tools):
    """Replay the original snapshot including exchanges before a hosted refusal."""
    refused = link_hosted_refusal_sources(
        artifacts, expected=expected, dispatch=dispatch, byte_limit=byte_limit
    )
    if not refused["ok"]:
        return refused
    report = _verify_provider_sources(
        artifacts,
        expected=expected,
        dispatch=dispatch,
        byte_limit=byte_limit,
        delegated_tools=delegated_tools,
        hosted_refusals=frozenset(refused["linked_requests"]),
    )
    if report["ok"]:
        report["linked_requests"] = refused["linked_requests"]
    return report


def read_provider_listener(artifacts, *, session_id):
    """Validate a listener in an authenticated closed snapshot, without claiming confinement."""
    try:
        raw = artifacts["listener.json"]
        _require(type(raw) is bytes and len(raw) <= 65536, "invalid provider listener frame")
        value = json.loads(raw, object_pairs_hook=_object, parse_constant=_constant)
        return verify_listener_identity(
            value, schema="aisle.provider-relay-listener.v1", session_id=session_id
        )
    except (KeyError, TypeError, AttributeError, RecursionError, UnicodeError) as exc:
        raise ValueError("invalid provider listener evidence") from exc


def verify_listener_identity(value, *, schema, session_id):
    """Check a closed listener descriptor against the caller's expected session."""
    try:
        _require(
            type(value) is dict
            and set(value) == {"schema_version", "session_id", "host", "port"}
            and value["schema_version"] == schema
            and type(session_id) is str
            and 0 < len(session_id) <= 256
            and value["session_id"] == session_id
            and value["host"] == "127.0.0.1"
            and type(value["port"]) is int
            and 1 <= value["port"] <= 65535,
            "invalid provider listener identity",
        )
        return value
    except (KeyError, TypeError, AttributeError, RecursionError, UnicodeError) as exc:
        raise ValueError("invalid provider listener evidence") from exc


def _verify_provider_sources(
    artifacts,
    *,
    expected,
    dispatch,
    byte_limit,
    delegated_tools,
    allow_refusal=False,
    hosted_refusals=frozenset(),
    uncertain_attempts=frozenset(),
    allow_replay=False,
    interruption=None,
):
    report = {
        "ok": False,
        "errors": [],
        "native_attempts": [],
        "delegated_calls": [],
        "complete_coverage": False,
        "confinement_verified": False,
    }
    if allow_refusal:
        report["refused_attempts"] = []
        report["withheld_requests"] = []
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
            expected["failure"]
            == (
                "CancelledError"
                if interruption is not None
                else "ValueError"
                if allow_replay
                else "DispatchRefused"
                if allow_refusal or hosted_refusals
                else None
            )
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
        from aisle.harness.provider_relay import verify_provider

        verify_provider(invocation["binding"])
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
        metadata_files = {"invocation.json"}
        if "listener.json" in artifacts:
            read_provider_listener(artifacts, session_id=invocation["session_id"])
            metadata_files.add("listener.json")
        requests = (len(artifacts) - len(metadata_files) + len(hosted_refusals)) // 4
        if hosted_refusals:
            _require(
                hosted_refusals
                == {f"{i:08d}" for i in range(int(min(hosted_refusals)), requests + 1)},
                "provider work continued after hosted refusal",
            )
        _require(
            0 < requests <= 10000
            and set(artifacts)
            == metadata_files
            | {
                f"{i:08d}-{suffix}"
                for i in range(1, requests + 1)
                for suffix in ("request.body", "request.json", "response.sse", "delivery.json")
                if suffix != "response.sse" or f"{i:08d}" not in hosted_refusals
            },
            "provider source inventory differs",
        )
        ordered, withheld = [], []
        for i in range(1, requests + 1):
            prefix = f"{i:08d}"
            if prefix in hosted_refusals:
                # The public wrapper already authenticated these exact requests,
                # zero allowances, absent responses, and refused deliveries.
                continue
            delivery = row(prefix + "-delivery.json")
            _require(
                type(delivery) is dict
                and set(delivery) == {"sequence", "events_returned", "error_type"},
                "failed provider delivery",
            )
            if allow_refusal and delivery["error_type"] == "ValueError":
                _require(
                    delivery["sequence"] is None
                    and type(delivery["events_returned"]) is int
                    and delivery["events_returned"] == 0
                    and "hosted_tool_contract" not in invocation["binding"],
                    "failed response was forwarded or could contain hosted work",
                )
                # A concurrent response can finish acquisition after another
                # request retires the relay, without ever entering forwarding.
                _, events, _ = _response(artifacts[prefix + "-response.sse"])
                _require(
                    all(
                        event["item"]["type"]
                        in {"message", "reasoning", "function_call", "custom_tool_call"}
                        for _, event in events
                        if event is not None and "item" in event
                    ),
                    "withheld response contains unsupported upstream work",
                )
                withheld.append(prefix)
                continue
            _require(
                delivery["error_type"] is None
                or allow_refusal
                and delivery["error_type"] == "DispatchRefused"
                or allow_replay
                and delivery["error_type"] == "ValueError"
                or interruption is not None
                and delivery["error_type"] == "CancelledError",
                "failed provider delivery",
            )
            _require(
                type(delivery["sequence"]) is int and type(delivery["events_returned"]) is int,
                "invalid provider delivery count",
            )
            ordered.append((delivery["sequence"], prefix, delivery["events_returned"]))
        ordered.sort()
        _require(
            [order for order, _, _ in ordered] == list(range(1, len(ordered) + 1)),
            "provider forwarding sequence differs",
        )
        reservations = _Reservations(
            dispatch,
            allow_refusal=allow_refusal,
            uncertain_attempts=uncertain_attempts,
            interruption=interruption,
        )
        hosted_rows = {}
        for number in range(1, dispatch["expected"].get("hosted_requests", 0) + 1):
            name = f"hosted-{number:08d}"
            reservation = json.loads(dispatch["artifacts"][name + "-reservation.json"])
            _require(
                reservation["request_id"] not in hosted_rows, "duplicate hosted provider request"
            )
            hosted_rows[reservation["request_id"]] = (name, reservation)
        used_hosted = set(hosted_refusals)
        completed_hosted = []
        completed_hosted_items = []
        authority = ProviderResponseAuthority(reservations, delegated_tools=delegated_tools)
        delegated_calls = []
        replayed_requests = []
        interrupted_attempts = []
        for order, prefix, count in ordered:
            delivered = []
            source = artifacts[prefix + "-response.sse"]
            if "hosted_tool_contract" in invocation["binding"]:
                from aisle.harness.provider_hosted import (
                    hosted_calls,
                    hosted_frontend_items,
                    hosted_request,
                    request_tools,
                )

                metadata = row(prefix + "-request.json")
                _require(
                    metadata["content_encoding"] == "identity",
                    "unsupported hosted request encoding",
                )
                original = artifacts[prefix + "-request.body"]
                _, hosted = request_tools(original)
                calls = hosted_calls(source)
                _require(
                    interruption is None or not calls,
                    "native interruption cannot qualify upstream hosted work",
                )
                if hosted:
                    _require(prefix in hosted_rows, "hosted provider request lacks a reservation")
                    name, reservation = hosted_rows[prefix]
                    prepared, allowance = hosted_request(
                        original, dispatch["expected"]["ceiling"] - reservation["reserved_before"]
                    )
                    _require(
                        allowance == reservation["allowance"]
                        and prepared == dispatch["artifacts"][name + "-request.frame"]
                        and source == dispatch["artifacts"][name + "-response.frame"],
                        "hosted source differs from reserved provider exchange",
                    )
                    used_hosted.add(prefix)
                    completed_hosted.extend(calls)
                    completed_hosted_items.extend(hosted_frontend_items(source))
                else:
                    _require(not calls, "unadvertised hosted work")
            refused = False
            replayed = False
            interrupted = False
            try:
                authority.forward(source, delivered.append)
            except _ReplayedRefusal:
                refused = True
            except _ReplayedInterruption:
                _require(
                    order == len(ordered), "provider work continued after interrupted delivery"
                )
                interrupted = True
                interrupted_attempts.append(interruption[0])
            except ValueError as exc:
                if not allow_replay or str(exc) not in {
                    "provider response replay",
                    "provider call identity replay",
                }:
                    raise
                _require(
                    order == len(ordered) and not delivered and count == 0,
                    "provider replay was forwarded or work continued after replay",
                )
                replayed = True
                replayed_requests.append(prefix)
            _require(
                row(prefix + "-delivery.json")["error_type"]
                == (
                    "CancelledError"
                    if interrupted
                    else "ValueError"
                    if replayed
                    else "DispatchRefused"
                    if refused
                    else None
                ),
                "provider refusal cause differs",
            )
            _require(not refused or order == len(ordered), "provider work continued after refusal")
            _require(len(delivered) == count, "provider delivered prefix differs")
            if replayed:
                continue
            _, events, calls = _response(source)
            for position, call in calls.items():
                if (refused or interrupted) and position >= count:
                    continue
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
        _require(used_hosted == set(hosted_rows), "unmatched hosted provider reservations")
        if interruption is not None:
            _require(
                interrupted_attempts == [interruption[0]], "missing unique interrupted delivery"
            )
            report["interrupted_attempts"] = interrupted_attempts
        if allow_replay:
            _require(len(replayed_requests) == 1, "missing unique provider identity replay")
            report["replayed_requests"] = replayed_requests
        if allow_refusal:
            _require(len(reservations.refused) == 1, "missing unique provider quota refusal")
            report["refused_attempts"] = reservations.refused
            report["withheld_requests"] = withheld
        report.update(
            ok=True, native_attempts=sorted(reservations.used), delegated_calls=delegated_calls
        )
        if "hosted_tool_contract" in invocation["binding"]:
            report["hosted_calls"] = completed_hosted
            report["hosted_items"] = completed_hosted_items
    except (ValueError, TypeError, KeyError, AttributeError, RecursionError) as exc:
        report["errors"].append(str(exc))
    return report


def link_hosted_refusal_sources(artifacts, *, expected, dispatch, byte_limit):
    """Link exhausted hosted reservations to owned requests, without qualifying a session.

    The caller authenticates both closed references. This checks the refused
    exchanges only; earlier successful work, frontend ownership, and observable
    effects still require their separate semantic replay.
    """
    from aisle.harness.frontend_dispatch import MAX_FRAME_BYTES
    from aisle.harness.provider_hosted import hosted_request
    from aisle.harness.provider_relay import verify_provider

    report = {
        "ok": False,
        "errors": [],
        "linked_requests": [],
        "complete_coverage": False,
        "confinement_verified": False,
    }
    try:
        _require(type(byte_limit) is int and byte_limit > 0, "invalid provider byte limit")
        _require(
            type(artifacts) is dict
            and len(artifacts) <= 50001
            and all(type(name) is str and type(raw) is bytes for name, raw in artifacts.items()),
            "invalid provider snapshot",
        )
        size = sum(map(len, artifacts.values()))
        _require(
            size <= byte_limit and all(len(raw) <= MAX_FRAME_BYTES for raw in artifacts.values()),
            "provider snapshot exceeds limit",
        )
        _require(
            type(expected) is dict
            and set(expected)
            == {"artifacts", "bytes", "failure", "complete_coverage", "confinement_verified"},
            "invalid provider reference",
        )
        _require(
            type(expected["bytes"]) is int
            and expected["bytes"] == size
            and expected["artifacts"]
            == {name: hashlib.sha256(raw).hexdigest() for name, raw in artifacts.items()},
            "provider bytes differ from closed reference",
        )
        _require(
            expected["failure"] == "DispatchRefused"
            and expected["complete_coverage"] is False
            and expected["confinement_verified"] is False,
            "provider did not retain an unpromoted dispatch refusal",
        )
        replay = verify_dispatch_journal(**dispatch)
        _require(
            replay["ok"] and not replay["delivery_uncertain"], "invalid refusal dispatch journal"
        )

        def row(name):
            return json.loads(artifacts[name], object_pairs_hook=_object, parse_constant=_constant)

        invocation = row("invocation.json")
        _require(
            type(invocation) is dict
            and invocation.get("schema_version") == "aisle.provider-relay.v1"
            and invocation.get("session_id") == dispatch["expected"]["session_id"]
            and invocation.get("complete_coverage") is False
            and invocation.get("confinement_verified") is False,
            "invalid refusal provider invocation",
        )
        binding = invocation["binding"]
        verify_provider(binding)
        if "listener.json" in artifacts:
            read_provider_listener(artifacts, session_id=invocation["session_id"])
        _require("hosted_tool_contract" in binding, "missing hosted provider contract")
        linked = []
        for refusal in replay["budget_refusals"]:
            if refusal["kind"] != "hosted":
                continue
            prefix = refusal["request_id"]
            _require(
                len(prefix) == 8
                and prefix.isascii()
                and prefix.isdigit()
                and 1 <= int(prefix) <= 10000,
                "invalid refused provider request identity",
            )
            _require(
                {name for name in artifacts if name.startswith(prefix + "-")}
                == {prefix + "-request.body", prefix + "-request.json", prefix + "-delivery.json"},
                "refused provider exchange has missing or returned source bytes",
            )
            metadata = row(prefix + "-request.json")
            _require(
                type(metadata) is dict
                and set(metadata) == {"content_encoding", "authorization_present"}
                and metadata["content_encoding"] == "identity"
                and type(metadata["authorization_present"]) is bool,
                "unsupported refused provider request encoding",
            )
            delivery = row(prefix + "-delivery.json")
            _require(
                type(delivery) is dict
                and set(delivery) == {"sequence", "events_returned", "error_type"}
                and delivery["sequence"] is None
                and type(delivery["events_returned"]) is int
                and delivery["events_returned"] == 0
                and delivery["error_type"] == "DispatchRefused",
                "provider refusal returned events or has another cause",
            )
            reservation = json.loads(
                dispatch["artifacts"][refusal["reservation"]],
                object_pairs_hook=_object,
                parse_constant=_constant,
            )
            prepared, allowance = hosted_request(
                artifacts[prefix + "-request.body"],
                dispatch["expected"]["ceiling"] - reservation["reserved_before"],
            )
            frame = refusal["reservation"].removesuffix("-reservation.json") + "-request.frame"
            _require(
                allowance == 0 and prepared == dispatch["artifacts"][frame],
                "refused request differs from reserved provider bytes",
            )
            linked.append(prefix)
        _require(bool(linked), "no hosted quota refusal to link")
        report.update(ok=True, linked_requests=linked)
    except (ValueError, TypeError, KeyError, AttributeError, RecursionError) as exc:
        report["errors"].append(str(exc))
    return report
