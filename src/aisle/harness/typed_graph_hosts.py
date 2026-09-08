"""Transport-only typed graph replacement; validation and launch admission are separate."""

from __future__ import annotations

import copy
import json
import math
import posixpath
import re
import shlex
from decimal import Decimal
from pathlib import Path

from aisle.harness.typed_execution_bundle import PARTICIPANT_FILES
from aisle.harness.typed_node_worker import validate_configuration


class GraphHostError(ValueError):
    """A graph cannot be mapped to its bound typed hosts without changing semantics."""


def _json_equal(left, right):
    """Compare retained JSON values without coercing booleans or numeric types."""
    return json.dumps(left, sort_keys=True, allow_nan=False) == json.dumps(
        right, sort_keys=True, allow_nan=False
    )


def _arguments(text):
    """Tokenize Dora's POSIX shlex subset without shell expansion or execution."""
    words, word = [], []
    quote = None
    active = False
    index = 0
    while index < len(text):
        char = text[index]
        index += 1
        if quote is None and not active and char == "#":
            end = text.find("\n", index)
            index = len(text) if end < 0 else end + 1
            continue
        if quote is None and char in " \t\n":
            if active:
                words.append("".join(word))
                word, active = [], False
            continue
        active = True
        if char == quote:
            quote = None
        elif quote is None and char in "\"'":
            quote = char
        elif char == "\\" and quote != "'":
            if index == len(text):
                raise GraphHostError("node arguments end with an incomplete escape")
            escaped = text[index]
            index += 1
            if escaped != "\n":
                if quote == '"' and escaped not in '$`"\\':
                    word.append("\\")
                word.append(escaped)
        else:
            word.append(char)
    if quote is not None:
        raise GraphHostError("node arguments contain an unclosed quote")
    if active:
        words.append("".join(word))
    return words


def _expand(value, declared):
    result = []
    index = 0
    while index < len(value):
        if value[index] != "$":
            result.append(value[index])
            index += 1
            continue
        start = index
        index += 1
        if index < len(value) and value[index] == "$":
            result.append("$")
            index += 1
            continue
        default = None
        if index < len(value) and value[index] == "{":
            end = value.find("}", index + 1)
            if end < 0:
                result.append("${")
                index += 1
                continue
            name = value[index + 1 : end]
            split = name.find(":-")
            if split > 0:
                name, default = name[:split], name[split + 2 :]
            index = end + 1
        else:
            while index < len(value) and (value[index].isalnum() or value[index] == "_"):
                index += 1
            name = value[start + 1 : index]
            if not name:
                result.append("$")
                continue
        if name in declared:
            result.append(declared[name])
        elif default is not None:
            result.append(default)
        else:
            raise GraphHostError("node environment expansion requires an admitted value binding")
    return "".join(result)


def _scalar_string(value):
    # EnvValue tries bool, i64, f64, then String, including expanded strings.
    if type(value) is str:
        if value in {"true", "false"}:
            return value
        if re.fullmatch(r"[+-]?[0-9]+", value):
            number = int(value)
            if -(2**63) <= number < 2**63:
                return str(number)
        if re.fullmatch(
            r"[+-]?(?:(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?|inf(?:inity)?|nan)",
            value,
            re.IGNORECASE,
        ):
            value = float(value)
        else:
            return value
    if type(value) is bool:
        return "true" if value else "false"
    if type(value) is int and -(2**63) <= value < 2**63:
        return str(value)
    if type(value) is float:
        if math.isnan(value):
            return "NaN"
        if math.isinf(value):
            return "-inf" if value < 0 else "inf"
        rendered = format(Decimal(str(value)), "f")
        return rendered.rstrip("0").rstrip(".") if "." in rendered else rendered
    raise GraphHostError("node environment value is not a Dora scalar")


def node_configuration(node, *, expansion_environment=None):
    """Decode Dora settings using only explicitly supplied expansion values."""
    declared = {} if expansion_environment is None else expansion_environment
    validate_configuration({"environment": declared, "arguments": []})
    environment = {
        key: _scalar_string(_expand(value, declared) if type(value) is str else value)
        for key, value in node.get("env", {}).items()
    }
    arguments = node.get("args", "")
    if type(arguments) is not str:
        raise GraphHostError("node arguments must use Dora's string form")
    return validate_configuration({"environment": environment, "arguments": _arguments(arguments)})


def _source(node):
    path = node.get("path")
    if type(path) is not str or path.startswith("/"):
        return None
    source = posixpath.normpath("graphs/" + path)
    return source if source in PARTICIPANT_FILES else None


def replace_authored_nodes(
    authored, baseline, bindings, controller_root, *, expansion_environments=None
):
    """Replace only process entries, preserving authored graph inputs and outputs.

    Caller must first run normal validation on the complete snapshot, bind the
    config files and controller revision, and independently authorize execution.
    Bindings here are the controller's retained config summaries, not worker claims.
    No files are read, created, or repaired by this transformation.
    """
    try:
        if type(authored) is not dict or type(baseline) is not dict:
            raise GraphHostError("graph descriptors must be mappings")
        if not _json_equal(
            {k: v for k, v in authored.items() if k != "nodes"},
            {k: v for k, v in baseline.items() if k != "nodes"},
        ):
            raise GraphHostError("global graph runtime settings differ from trusted baseline")
        controller_root = Path(controller_root)
        if not controller_root.is_absolute() or ".." in controller_root.parts:
            raise GraphHostError("controller root must be absolute and canonical")
        if type(bindings) is not dict:
            raise GraphHostError("host bindings must be a mapping")
        expansions = {} if expansion_environments is None else expansion_environments
        if type(expansions) is not dict or not set(expansions) <= set(bindings):
            raise GraphHostError("expansion environment has an undeclared node")
        trusted = {n["id"]: n for n in baseline["nodes"] if _source(n) is None}
        result = copy.deepcopy(authored)
        seen, assigned = set(), set()
        for node in result["nodes"]:
            node_id = node["id"]
            if type(node_id) is not str or node_id in seen:
                raise GraphHostError("node identity is invalid or duplicated")
            seen.add(node_id)
            if node_id in trusted:
                # Normal validation owns authored topology, including routes into
                # trusted consumers. Preserve those inputs without granting graph
                # edits authority over trusted executables or runtime settings.
                if not _json_equal(
                    {k: v for k, v in node.items() if k != "inputs"},
                    {k: v for k, v in trusted[node_id].items() if k != "inputs"},
                ):
                    raise GraphHostError("trusted node definition has changed")
                continue
            source = _source(node)
            if source is None or node_id not in bindings:
                raise GraphHostError("authored node has no bound editable implementation")
            # Process execution, restart and logging directives must not become
            # authority on the trusted host. Unsupported directives fail visibly.
            if set(node) - {
                "id",
                "path",
                "args",
                "env",
                "inputs",
                "outputs",
                "input_types",
                "output_types",
                "description",
                "name",
            }:
                raise GraphHostError("node contains startup directives without a worker mapping")
            binding = bindings[node_id]
            if set(binding) != {
                "config_path",
                "config_sha256",
                "module",
                "outputs",
                "wall_outputs",
                "configuration",
            }:
                raise GraphHostError("host binding fields differ")
            settings = node_configuration(node, expansion_environment=expansions.get(node_id))
            config_path = Path(binding["config_path"])
            if (
                not config_path.is_absolute()
                or ".." in config_path.parts
                or type(binding["config_sha256"]) is not str
                or re.fullmatch("[0-9a-f]{64}", binding["config_sha256"]) is None
                or binding["module"] != source[4:-3].replace("/", ".")
                or binding["configuration"] != settings
            ):
                raise GraphHostError("host source/configuration binding differs from authored node")
            outputs = node.get("outputs", [])
            if type(outputs) is not list or any(type(p) is not str for p in outputs):
                raise GraphHostError("graph outputs are invalid")
            env = settings["environment"]
            if (
                len(set(outputs)) != len(outputs)
                or "turn_done" not in outputs
                or env.get("AISLE_LOCKSTEP", "").strip().lower() not in {"1", "true", "yes"}
                or env.get("AISLE_TURN_NODE") != node_id
                or set(env.get("AISLE_TURN_OUTPUTS", "").split(",")) != set(outputs)
                or binding["outputs"] != [p for p in outputs if p != "turn_done"]
                or binding["wall_outputs"]
                != [p for p in env.get("AISLE_TURN_WALL_OUTPUTS", "").split(",") if p]
            ):
                raise GraphHostError("host turn/output grants differ from authored node")
            node.pop("env", None)
            node["path"] = str(Path(controller_root) / "src/aisle/harness/typed_node_host.py")
            node["args"] = shlex.join(
                [
                    "--config",
                    str(config_path),
                    "--config-sha256",
                    binding["config_sha256"],
                ]
            )
            assigned.add(node_id)
        if not set(trusted) <= seen or assigned != set(bindings):
            raise GraphHostError("trusted nodes or host bindings are missing or extra")
        return result
    except GraphHostError:
        raise
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise GraphHostError(f"invalid typed host graph: {exc}") from exc
