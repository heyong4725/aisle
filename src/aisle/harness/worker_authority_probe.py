"""Fixed Python file/exec probes for actual worker policies, not an attestation.

The controller must provision disposable targets, retain raw captures, verify the
profile/environment identities, and run unrestricted controls before aggregation.
"""

from __future__ import annotations

import errno
import hashlib
import json
from pathlib import Path

_OPERATIONS = {"read", "subprocess_read", "git_blob", "write", "exec"}
_CODE = """
import json, subprocess, sys, zlib
mode, target, data, operation = sys.argv[1:5]
if mode == "subprocess_read":
    child = subprocess.run(
        [sys.executable, "-I", "-B", "-c", sys.argv[5],
         "read", target, data, operation],
        stdin=subprocess.DEVNULL, capture_output=True, timeout=3,
    )
    sys.stdout.buffer.write(child.stdout)
    sys.stderr.buffer.write(child.stderr)
    raise SystemExit(child.returncode)
print(json.dumps({"phase": "ready"}), flush=True)
try:
    if mode == "read":
        with open(target, "rb") as stream:
            result = {"data": stream.read(65).hex()}
    elif mode == "git_blob":
        with open(target, "rb") as stream:
            compressed = stream.read(257)
        if len(compressed) > 256:
            raise ValueError("oversized probe object")
        decoder = zlib.decompressobj()
        decoded = decoder.decompress(compressed, 128)
        if not decoder.eof or decoder.unused_data or decoder.unconsumed_tail:
            raise ValueError("invalid or oversized probe object")
        header, payload = decoded.split(bytes([0]), 1)
        if len(payload) > 64 or header != b"blob " + str(len(payload)).encode():
            raise ValueError("probe object is not a bounded Git blob")
        result = {"data": payload.hex()}
    elif mode == "write":
        with open(target, "xb") as stream:
            stream.write(bytes.fromhex(data))
        result = {"written": True}
    elif mode == "exec":
        child = subprocess.run([target], stdin=subprocess.DEVNULL,
                               capture_output=True, timeout=3)
        result = {"returncode": child.returncode,
                  "stdout": child.stdout.hex(), "stderr": child.stderr.hex()}
    else:
        raise ValueError("unknown fixed probe operation")
except OSError as exc:
    print(json.dumps({"phase": "error", "operation": operation,
                      "errno": exc.errno}), flush=True)
    raise SystemExit(3)
print(json.dumps({"phase": "result", "operation": operation, **result}), flush=True)
"""


def operation_command(python, operation, target, sentinel):
    """Build one bounded, fixed-code probe; the caller owns target provisioning."""
    if operation not in _OPERATIONS:
        raise ValueError("unsupported worker authority operation")
    if type(sentinel) is not bytes or not 1 <= len(sentinel) <= 64:
        raise ValueError("worker authority probe requires a 1-64 byte sentinel")
    target = Path(target)
    if not target.is_absolute():
        raise ValueError("worker authority target must be absolute")
    argv = [
        str(Path(python).resolve(strict=True)),
        "-I",
        "-B",
        "-c",
        _CODE,
        operation,
        str(target),
        sentinel.hex(),
        operation,
    ]
    if operation == "subprocess_read":
        argv.append(_CODE)
    return argv


def operation_case(result, operation, sentinel, *, allowed):
    """Require a result from the intended operation, never just a nonzero exit."""
    if operation not in _OPERATIONS:
        raise ValueError("unsupported worker authority operation")
    try:
        rows = [json.loads(line) for line in result.stdout.splitlines()]
    except (ValueError, UnicodeError):
        rows = []
    ready = len(rows) == 2 and rows[0] == {"phase": "ready"}
    final = rows[-1] if ready and type(rows[-1]) is dict else {}
    expected = {"phase": "result", "operation": operation}
    if operation in {"read", "subprocess_read", "git_blob"}:
        expected["data"] = sentinel.hex()
    elif operation == "write":
        expected["written"] = True
    else:
        expected.update(returncode=0, stdout="", stderr="")
    succeeded = ready and result.returncode == 0 and final == expected
    denied = (
        ready
        and result.returncode == 3
        and set(final) == {"phase", "operation", "errno"}
        and final["phase"] == "error"
        and final["operation"] == operation
        and type(final["errno"]) is int
        and final["errno"] in (errno.EPERM, errno.EACCES)
    )
    exposed = any(
        marker in stream
        for marker in (sentinel, sentinel.hex().encode())
        for stream in (result.stdout, result.stderr)
    )
    return {
        "operation": operation,
        "passed": bool(succeeded if allowed else denied and not exposed),
        "operation_succeeded": bool(succeeded),
        "denied": bool(denied),
        "sentinel_exposed": exposed,
        "returncode": result.returncode,
        "stdout_sha256": hashlib.sha256(result.stdout).hexdigest(),
        "stderr_sha256": hashlib.sha256(result.stderr).hexdigest(),
    }
