"""Trusted Dora entry point for a separately confined authored typed node."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
from pathlib import Path

from aisle.harness.treatment_confinement import MacOSPolicy, wrap_verified_command
from aisle.harness.typed_node_worker import MODULES, validate_configuration
from aisle.harness.typed_worker_launch import launch_typed_worker, verify_typed_launch
from aisle.turn_node import Node

_LAUNCH_FIELDS = {
    "bundle",
    "bundle_manifest",
    "source_roots",
    "policy",
    "profile_path",
    "attestation",
    "python",
    "python_sha256",
    "environment",
    "environment_record",
    "runtime_record",
    "timeout_s",
}


class HostConfigError(ValueError):
    """The trusted host cannot bind its complete execution declaration."""


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise HostConfigError("duplicate host configuration field")
        result[key] = value
    return result


def load_host_config(path, digest):
    """Read one canonical, size-limited, exact-hash engineering declaration."""
    try:
        if type(digest) is not str or re.fullmatch("[0-9a-f]{64}", digest) is None:
            raise HostConfigError("host configuration requires an exact SHA-256")
        path = Path(path).absolute()
        if path.resolve() != path or not path.is_file():
            raise HostConfigError("host configuration is missing or redirected")
        with path.open("rb") as stream:
            data = stream.read(16 * 1024 * 1024 + 1)
        if len(data) > 16 * 1024 * 1024 or hashlib.sha256(data).hexdigest() != digest:
            raise HostConfigError("host configuration size or hash differs")
        config = json.loads(data, object_pairs_hook=_object)
        json.dumps(config, allow_nan=False)
        if type(config) is not dict or set(config) != {
            "schema_version",
            "purpose",
            "node_id",
            "module",
            "outputs",
            "wall_outputs",
            "configuration",
            "output",
            "launch",
        }:
            raise HostConfigError("host configuration fields differ")
        if (
            config["schema_version"] != "aisle.typed-node-host.v1"
            or config["purpose"] != "expert_parity"
            or type(config["module"]) is not str
            or config["module"] not in MODULES
            or type(config["node_id"]) is not str
            or re.fullmatch("[A-Za-z0-9_-]+", config["node_id"]) is None
        ):
            raise HostConfigError("host identity or engineering purpose is invalid")
        for key in ("outputs", "wall_outputs"):
            values = config[key]
            if (
                type(values) is not list
                or any(
                    type(name) is not str or re.fullmatch("[A-Za-z0-9_-]+", name) is None
                    for name in values
                )
                or len(set(values)) != len(values)
                or "turn_done" in values
            ):
                raise HostConfigError("host output grants are invalid")
        if not set(config["wall_outputs"]) <= set(config["outputs"]):
            raise HostConfigError("host wall outputs exceed declared outputs")
        validate_configuration(config["configuration"])
        output = config["output"]
        if (
            type(output) is not str
            or not Path(output).is_absolute()
            or Path(output).resolve() != Path(output)
        ):
            raise HostConfigError("host evidence path must be canonical and absolute")
        launch = config["launch"]
        if type(launch) is not dict or set(launch) not in (
            _LAUNCH_FIELDS,
            _LAUNCH_FIELDS | {"max_calls"},
        ):
            raise HostConfigError("host launch fields differ")
        timeout = launch["timeout_s"]
        calls = launch.get("max_calls", 100000)
        if (
            type(timeout) not in (int, float)
            or not math.isfinite(timeout)
            or timeout <= 0
            or type(calls) is not int
            or calls <= 0
        ):
            raise HostConfigError("host worker budgets must be finite and positive")
        return config
    except HostConfigError:
        raise
    except (OSError, TypeError, ValueError, KeyError) as exc:
        raise HostConfigError(f"invalid host configuration: {exc}") from exc


def preflight_host_config(path, digest):
    """Verify host launch inputs without importing or attaching Dora transport."""
    config = load_host_config(path, digest)
    try:
        launch = dict(config["launch"])
        launch["policy"] = MacOSPolicy(
            **{
                key: value if key == "network_policy" else tuple(Path(p) for p in value)
                for key, value in launch["policy"].items()
            }
        )
        compiled = verify_typed_launch(
            **{
                key: launch[key]
                for key in (
                    "bundle",
                    "bundle_manifest",
                    "source_roots",
                    "policy",
                    "python",
                    "python_sha256",
                    "runtime_record",
                    "environment",
                    "environment_record",
                )
            }
        )
        wrap_verified_command(
            [launch["python"], "-I", "-B", "-c", "pass"],
            compiled,
            Path(launch["profile_path"]),
            launch["attestation"],
        )
        policy = launch["policy"]
        readable = (*policy.visible_roots, *policy.runtime_read_roots, *policy.output_roots)
        config_path, output = Path(path).absolute(), Path(config["output"])
        if output.exists() or output.is_symlink():
            raise HostConfigError("host evidence already exists; resume refused")
        for asset in (config_path, output):
            if any(asset.is_relative_to(p) or p.is_relative_to(asset) for p in readable):
                raise HostConfigError("host configuration/evidence overlaps worker authority")
            if not any(asset.is_relative_to(p) for p in policy.hidden_roots):
                raise HostConfigError("host configuration/evidence requires a hidden binding")
        if load_host_config(path, digest) != config:
            raise HostConfigError("host configuration drifted before Dora attachment")
    except Exception as exc:
        raise HostConfigError(f"host preflight refused: {exc}") from exc
    return config, launch


class _DeferredTransport:
    """Attach Dora only when a started worker first needs its host transport."""

    def __init__(self, factory):
        self.factory = factory
        self.node = None

    def _get(self):
        if self.node is None:
            self.node = self.factory()
        return self.node

    def __iter__(self):
        yield from self._get()

    def send_output(self, *args, **kwargs):
        return self._get().send_output(*args, **kwargs)


def run_configured_node(path, digest, *, raw_node_factory=None):
    """Verify before Dora attachment, then delegate all authored execution to the worker."""
    config, launch = preflight_host_config(path, digest)
    if raw_node_factory is None:
        from dora import Node as DoraNode

        raw_node_factory = DoraNode
    node = Node(
        _DeferredTransport(raw_node_factory),
        {
            "AISLE_LOCKSTEP": "1",
            "AISLE_TURN_NODE": config["node_id"],
            "AISLE_TURN_OUTPUTS": ",".join([*config["outputs"], "turn_done"]),
            "AISLE_TURN_WALL_OUTPUTS": ",".join(config["wall_outputs"]),
        },
    )
    receipt = {"host_config_sha256": digest, "configuration": config, "ok": False}
    try:
        result = launch_typed_worker(
            **launch,
            output=config["output"],
            node=node,
            module=config["module"],
            outputs=set(config["outputs"]),
            configuration=config["configuration"],
        )
        if load_host_config(path, digest) != config:
            raise HostConfigError("host configuration drifted during execution")
        result["host_config_sha256"] = digest
        receipt.update(ok=result["ok"], result=result)
        return result
    except BaseException as exc:
        receipt["error"] = str(exc) or type(exc).__name__
        raise
    finally:
        # The launcher owns creation of this previously absent private directory.
        # Its worker result remains separate from this host configuration receipt.
        output = Path(config["output"])
        if output.is_dir() and output.resolve() == output:
            with (output / "host.json").open("x") as stream:
                stream.write(json.dumps(receipt, indent=2, allow_nan=False) + "\n")


def main(argv=None):
    """CON-8: emit one JSON verdict; authored execution never occurs in this process."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--config-sha256", required=True)
    args = parser.parse_args(argv)
    try:
        result = run_configured_node(args.config, args.config_sha256)
    except Exception as exc:
        result = {"ok": False, "classification": "infrastructure_exclusion", "error": str(exc)}
    print(json.dumps(result, allow_nan=False))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
