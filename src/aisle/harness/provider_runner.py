"""Own the HTTP provider boundary throughout an admitted App Server session."""

from __future__ import annotations

import asyncio
import json
from contextlib import ExitStack

from aisle.harness.frontend_app_server import run_app_server_async
from aisle.harness.provider_relay import ProviderRelay, verify_provider


def provider_settings(binding, address, timeout_s):
    """Render the controller's provider override for its owned listener."""
    return {
        "model_provider": "aisle_authorized",
        "model_providers.aisle_authorized.name": "AISLE authorized provider",
        "model_providers.aisle_authorized.base_url": f"http://{address[0]}:{address[1]}/v1",
        "model_providers.aisle_authorized.wire_api": "responses",
        "model_providers.aisle_authorized.requires_openai_auth": binding.get(
            "requires_openai_auth", True
        ),
        "model_providers.aisle_authorized.supports_websockets": False,
        "model_providers.aisle_authorized.request_max_retries": 0,
        "model_providers.aisle_authorized.stream_max_retries": 0,
        "model_providers.aisle_authorized.stream_idle_timeout_ms": int(timeout_s * 1000),
    }


def run_provider_app_server(
    *,
    binding,
    dispatch,
    delegated_tools,
    provider_output,
    references,
    argv,
    timeout_s,
    code_mode_host=None,
    code_mode_output=None,
    mcp_harness=False,
    mcp_output=None,
    **options,
):
    """Retain closed provider evidence and reap the frontend on relay failure."""
    verify_provider(binding)
    if "--" in argv:
        raise ValueError("provider launch cannot append configuration after an option terminator")
    relay = None
    mcp = None
    mcp_listener = None
    failure = None
    try:
        with (
            ProviderRelay(
                binding,
                dispatch=dispatch,
                delegated_tools=delegated_tools,
                output=provider_output,
                timeout_s=timeout_s,
            ) as relay,
            ExitStack() as resources,
        ):
            handle = options["handle_call"]
            settings = provider_settings(binding, relay.address, timeout_s)
            if mcp_harness:
                from aisle.harness.mcp_harness_authority import MCPHarnessAuthority
                from aisle.harness.mcp_harness_transport import MCPHarnessServer

                mcp = resources.enter_context(
                    MCPHarnessAuthority(
                        mcp_output,
                        handle_call=handle,
                        timeout_s=timeout_s,
                    )
                )
                operations = sorted(
                    name
                    for namespace, name, kind in delegated_tools
                    if namespace == "mcp__aisle_harness" and kind == "function_call"
                )
                server = resources.enter_context(MCPHarnessServer(mcp, operations=operations))
                mcp_listener = {
                    "schema_version": "aisle.mcp-harness-listener.v1",
                    "session_id": dispatch.session_id,
                    "host": server.address[0],
                    "port": server.address[1],
                }
                settings.update(
                    {
                        "mcp_servers.aisle_harness.url": f"http://{server.address[0]}:{server.address[1]}/mcp",
                        "mcp_servers.aisle_harness.enabled": True,
                    }
                )
                options["on_mcp_source"] = mcp.observe
            command = [*argv]
            for key, value in settings.items():
                command.extend(["-c", key + "=" + json.dumps(value)])

            async def asynchronous_handle(call, source):
                return await asyncio.to_thread(handle, call, source)

            options["handle_call"] = asynchronous_handle

            async def execute():
                if code_mode_host is not None:
                    from aisle.harness.code_mode_runner import run_code_mode_app_server

                    options["protocol_output"] = options.pop("output")
                    return await run_code_mode_app_server(
                        host=code_mode_host,
                        dispatch=dispatch,
                        delegated_tools={
                            namespace + "." + name
                            for namespace, name, kind in delegated_tools
                            if namespace in {"harness", "mcp__aisle_harness"}
                            and kind == "function_call"
                        },
                        output=code_mode_output,
                        references=references,
                        argv=command,
                        timeout_s=timeout_s,
                        **options,
                    )
                return await run_app_server_async(command, timeout_s=timeout_s, **options)

            async def watch():
                while not relay.failed.is_set() and not (mcp is not None and mcp.failed.is_set()):
                    await asyncio.sleep(0.01)
                if mcp is not None and mcp.failed.is_set():
                    raise ValueError("MCP harness failed: " + str(mcp._failure))
                raise ValueError("provider relay failed: " + str(relay._failure))

            async def owned():
                frontend = asyncio.create_task(execute())
                watcher = asyncio.create_task(watch())
                try:
                    done, _ = await asyncio.wait(
                        (frontend, watcher), return_when=asyncio.FIRST_COMPLETED
                    )
                    if watcher in done:
                        await watcher
                    if relay.failed.is_set():
                        raise ValueError("provider relay failed: " + str(relay._failure))
                    if mcp is not None and mcp.failed.is_set():
                        raise ValueError("MCP harness failed: " + str(mcp._failure))
                    return await frontend
                finally:
                    for task in (frontend, watcher):
                        if not task.done():
                            task.cancel()
                    await asyncio.gather(frontend, watcher, return_exceptions=True)

            return asyncio.run(owned())
    except BaseException as exc:
        failure = exc
        raise
    finally:
        final_failure = failure
        for name, resource, label in (
            ("mcp_harness", mcp, "MCP harness"),
            ("provider", relay, "provider relay"),
        ):
            if resource is None:
                continue
            try:
                references[name] = resource.reference()
                if name == "mcp_harness" and mcp_listener is not None:
                    references[name]["listener"] = mcp_listener
                if references[name]["failure"] is not None:
                    raise ValueError(label + " failed: " + references[name]["failure"])
            except BaseException as exc:
                if final_failure is None:
                    final_failure = exc
                else:
                    final_failure.add_note(label + " finalization failed: " + str(exc))
        if failure is None and final_failure is not None:
            raise final_failure
