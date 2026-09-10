"""Keep an owned frontend and its pinned nested host within one controller lifetime."""

from __future__ import annotations

import asyncio
import hashlib
import os
import signal
import stat
from pathlib import Path

from aisle.harness.code_mode_authority import CodeModeAuthority, _require
from aisle.harness.code_mode_proxy import CodeModeProxy
from aisle.harness.frontend_app_server import run_app_server_async


def verify_host(host):
    """Check the operator-bound executable; this is not an external confinement attestation."""
    _require(type(host) is dict and set(host) == {"path", "sha256"}, "invalid host binding")
    path = Path(host["path"])
    _require(path.is_absolute() and path.resolve() == path, "redirected host executable")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        _require(
            stat.S_ISREG(info.st_mode)
            and info.st_mode & 0o111
            and 0 < info.st_size <= 256 * 1024 * 1024,
            "invalid host executable",
        )
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    _require(digest == host["sha256"], "host executable digest differs")
    return str(path)


async def run_code_mode_app_server(
    *,
    host,
    dispatch,
    delegated_tools,
    output,
    references,
    argv,
    cwd,
    env,
    protocol_output,
    timeout_s,
    **kwargs,
):
    """Cancel and reap the frontend if the host or proxy fails; never fall back.

    Both arms must bind the same host digest and mode before using this runner.
    ``handle_call`` should be asynchronous when it performs blocking work, so
    authority failure can cancel the owned frontend immediately.
    """
    executable = verify_host(host)
    _require(dispatch is not None, "nested host requires shared dispatch authority")
    _require(
        not any(arg == "--code-mode-host" or arg.startswith("--code-mode-host=") for arg in argv),
        "frontend already selects a host",
    )
    output = Path(output).absolute()
    _require(output.resolve() == output, "redirected host evidence path")
    output.mkdir(parents=True, exist_ok=False, mode=0o700)
    process = None
    proxy = None
    tasks = []
    failure = None
    try:
        with (output / "host.stderr").open("xb") as stderr:
            process = await asyncio.create_subprocess_exec(
                executable,
                "--listen",
                "grpc://127.0.0.1:0",
                env=env,
                cwd=cwd,
                stdout=asyncio.subprocess.PIPE,
                stderr=stderr,
                start_new_session=True,
                limit=4096,
            )
            raw = await asyncio.wait_for(process.stdout.readline(), min(5, timeout_s))
            _require(raw.endswith(b"\n") and len(raw) <= 4096, "host did not publish an endpoint")
            endpoint = raw.decode().strip()
            with (output / "host.stdout").open("xb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            authority = CodeModeAuthority(dispatch, delegated_tools=delegated_tools)
            async with CodeModeProxy(endpoint, authority, output=output / "rpc") as proxy:
                frontend = asyncio.create_task(
                    run_app_server_async(
                        [
                            *argv,
                            "--code-mode-host",
                            "http://" + proxy.address,
                            "-c",
                            "features.code_mode=true",
                        ],
                        cwd=cwd,
                        env=env,
                        output=protocol_output,
                        timeout_s=timeout_s,
                        **kwargs,
                    )
                )
                host_exit = asyncio.create_task(process.wait())
                proxy_failure = asyncio.create_task(proxy.failed.wait())
                tasks = [frontend, host_exit, proxy_failure]
                await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                proxy.check()
                _require(not host_exit.done(), "nested host exited during frontend execution")
                result = await frontend
            proxy.check()
            return result
    except BaseException as exc:
        failure = type(exc).__name__ + ": " + str(exc)
        raise
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if process is not None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            await process.wait()
        references["code_mode"] = {
            "host": dict(host),
            "delegated_tools": sorted(delegated_tools),
            "failure": failure,
            "proxy": proxy.reference() if proxy is not None else None,
            "complete_coverage": False,
            "confinement_verified": False,
        }
