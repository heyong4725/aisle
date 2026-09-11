"""MON-8/MON-13: actual frontend launch parameters must match admission and owned endpoints."""

import copy
import hashlib
import json

import pytest

from aisle.harness.frontend_app_server import dynamic_tools

pytestmark = pytest.mark.unit


def launch_proof():
    from aisle.harness.provider_runner import provider_settings

    environment = {"HOME": "/session/home", "PATH": "/usr/bin:/bin"}
    env_hash = hashlib.sha256(json.dumps(environment, sort_keys=True).encode()).hexdigest()
    profile = b"synthetic compiled profile"
    profile_hash = hashlib.sha256(profile).hexdigest()
    budget = {"wall_ceiling_s": 30, "ceiling": 100, "frontend_tool_ceiling": 2}
    launch = {
        "argv": ["/runtime/codex", "app-server", "--listen", "stdio://"],
        "app_server": {"baseInstructions": "system", "developerInstructions": "contract"},
        "provider": {"base_url": "http://127.0.0.1:4321/v1", "requires_openai_auth": False},
    }
    wrapped = ["/runtime/adapter", "-f", "/session/evidence/launch-profile.sb", *launch["argv"]]
    command = list(wrapped)
    for key, value in provider_settings(launch["provider"], ("127.0.0.1", 5432), 30).items():
        command.extend(["-c", key + "=" + json.dumps(value)])
    manifest = {
        "budget": budget,
        "model": {"requested_identity": "fixture"},
        "policy": {"approval": "never", "allowed_external_tools": ["harness.check"]},
        "confinement": {"profile_sha256": profile_hash, "adapter_binary_sha256": "a" * 64},
    }
    documents = {
        "launch.json": {
            "argv": launch["argv"],
            "wrapped_argv": wrapped,
            "cwd": "/view",
            "budget": budget,
            "environment_sha256": env_hash,
            "compiled_profile_sha256": profile_hash,
        },
        "capability.json": {"adapter": {"path": "/runtime/adapter", "sha256": "a" * 64}},
        "provider/listener.json": {
            "schema_version": "aisle.provider-relay-listener.v1",
            "session_id": "session",
            "host": "127.0.0.1",
            "port": 5432,
        },
        "frontend-protocol/invocation.json": {
            "argv": command,
            "cwd": "/view",
            "environment_sha256": env_hash,
            "timeout_s": 30,
            "thread_params": {
                **launch["app_server"],
                "cwd": "/view",
                "ephemeral": True,
                "model": "fixture",
                "approvalPolicy": "never",
                "sandbox": "danger-full-access",
                "dynamicTools": dynamic_tools(["check"]),
            },
            "input_items": [
                {"type": "text", "text": "Perform the assigned research task.", "text_elements": []}
            ],
        },
    }
    proof = {
        "record": {"arm": "typed", "session_id": "session"},
        "admission": {
            "arms": {"typed": manifest},
            "launch_bindings": {"typed": launch},
            "confinement_bindings": {"typed": {"policy": {"visible_roots": ["/view"]}}},
            "ambient_bindings": {
                "typed": {"environment": environment, "record": {"environment_sha256": env_hash}}
            },
        },
    }
    return proof, documents, profile


@pytest.mark.parametrize(
    "drift",
    [
        None,
        "cwd",
        "model",
        "prompt",
        "environment",
        "endpoint",
        "extra_config",
        "budget",
        "profile",
        "wrapper",
    ],
)
def test_original_launch_matches_admission_and_owned_listener(drift):
    """MON-13: a valid effect in another directory, model, wrapper or relay cannot qualify this
    launch.
    """
    from aisle.harness.frontend_qualification import verify_original_launch

    proof, documents, profile = launch_proof()
    documents = copy.deepcopy(documents)
    actual = documents["frontend-protocol/invocation.json"]
    if drift == "cwd":
        actual["cwd"] = "/other"
    elif drift == "model":
        actual["thread_params"]["model"] = "other"
    elif drift == "prompt":
        actual["thread_params"]["developerInstructions"] = "other"
    elif drift == "environment":
        actual["environment_sha256"] = "0" * 64
    elif drift == "endpoint":
        documents["provider/listener.json"]["port"] += 1
    elif drift == "extra_config":
        actual["argv"].extend(["-c", 'model_provider="uncontrolled"'])
    elif drift == "budget":
        documents["launch.json"]["budget"]["frontend_tool_ceiling"] += 1
    elif drift == "profile":
        profile += b" changed"
    elif drift == "wrapper":
        actual["argv"][0] = "/runtime/other-adapter"
    proof["artifacts"] = {name: json.dumps(value).encode() for name, value in documents.items()}
    proof["artifacts"]["launch-profile.sb"] = profile
    if drift is None:
        assert verify_original_launch(proof)["launch_verified"] is True
    else:
        with pytest.raises(ValueError):
            verify_original_launch(proof)


@pytest.mark.parametrize("mode", ["nested", "mcp", "both"])
@pytest.mark.parametrize("drift", [None, "listener", "session", "missing", "backend"])
def test_auxiliary_endpoints_are_owned_and_match_actual_launch(mode, drift):
    """MON-13: auxiliary endpoint overrides must match closed ownership and host evidence."""
    from aisle.harness.frontend_qualification import verify_original_launch

    proof, documents, profile = launch_proof()
    bound = proof["admission"]["launch_bindings"]["typed"]
    command = documents["frontend-protocol/invocation.json"]["argv"]
    if mode in {"mcp", "both"}:
        bound["mcp_harness"] = True
        documents["mcp-harness-reference.json"] = {
            "listener": {
                "schema_version": "aisle.mcp-harness-listener.v1",
                "session_id": "session",
                "host": "127.0.0.1",
                "port": 6543,
            }
        }
        command.extend(
            [
                "-c",
                'mcp_servers.aisle_harness.url="http://127.0.0.1:6543/mcp"',
                "-c",
                "mcp_servers.aisle_harness.enabled=true",
            ]
        )
    if mode in {"nested", "both"}:
        bound["code_mode_host"] = {"path": "/runtime/host", "sha256": "b" * 64}
        documents["code-mode-reference.json"] = {
            "host": dict(bound["code_mode_host"]),
            "backend": "127.0.0.1:7654",
            "listener": {
                "schema_version": "aisle.code-mode-listener.v1",
                "session_id": "session",
                "host": "127.0.0.1",
                "port": 8765,
            },
        }
        command.extend(
            ["--code-mode-host", "http://127.0.0.1:8765", "-c", "features.code_mode=true"]
        )
    selected = documents[
        "code-mode-reference.json" if mode != "mcp" else "mcp-harness-reference.json"
    ]
    if drift == "listener":
        selected["listener"]["port"] += 1
    elif drift == "session":
        selected["listener"]["session_id"] = "other"
    elif drift == "missing":
        del selected["listener"]
    elif drift == "backend":
        if mode != "mcp":
            selected["backend"] = "127.0.0.1:9999"
        else:
            bound.pop("mcp_harness")
    proof["artifacts"] = {name: json.dumps(value).encode() for name, value in documents.items()}
    proof["artifacts"]["launch-profile.sb"] = profile
    proof["artifacts"]["code-mode/host.stdout"] = b"http://127.0.0.1:7654\n"
    if drift is None:
        assert verify_original_launch(proof)["launch_verified"] is True
    else:
        with pytest.raises(ValueError):
            verify_original_launch(proof)
