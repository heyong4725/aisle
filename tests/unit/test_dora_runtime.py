"""CON-3/CON-5/CON-8: source-pinned CLI identity and paired API verification."""

import hashlib
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
pytestmark = pytest.mark.unit


def fixture(tmp_path):
    from dora_runtime import RECEIPT, load_pin

    pin_path = Path(__file__).resolve().parents[2] / "dora-runtime.json"
    pin = load_pin(pin_path)
    prefix = tmp_path / "runtime"
    (prefix / "bin").mkdir(parents=True)
    binary = prefix / "bin" / "dora"
    binary.write_bytes(b"test executable")
    binary.chmod(0o755)
    receipt = {
        "schema_version": 1,
        "pin_sha256": hashlib.sha256(pin_path.read_bytes()).hexdigest(),
        "commit": pin["commit"],
        "cargo_lock_sha256": pin["cargo_lock_sha256"],
        "binary_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
        "python_api_version": pin["python_api_version"],
    }
    (prefix / RECEIPT).write_text(json.dumps(receipt))
    return pin_path, prefix, binary


def test_identity_verifies_without_claiming_acceptance(tmp_path):
    """CON-5: matching bytes prove installation identity, not upstream correctness."""
    from dora_runtime import verify

    pin, prefix, _ = fixture(tmp_path)
    report = verify(pin, prefix, api_version="1.0.1")
    assert report["ok"] is True
    assert report["acceptance_ready"] is False
    assert report["upstream_status"] == "candidate"


@pytest.mark.parametrize("field", ["binary", "pin", "receipt", "api"])
def test_drift_refuses_before_launch(tmp_path, field):
    """CON-5: changed binary, source pin, receipt, or Python API cannot be attested."""
    from dora_runtime import RECEIPT, verify

    pin, prefix, binary = fixture(tmp_path)
    if field == "binary":
        binary.write_bytes(b"changed")
    elif field == "pin":
        altered = tmp_path / "pin.json"
        altered.write_bytes(pin.read_bytes() + b"\n")
        pin = altered
    elif field == "receipt":
        receipt = json.loads((prefix / RECEIPT).read_text())
        receipt["commit"] = "0" * 40
        (prefix / RECEIPT).write_text(json.dumps(receipt))
    with pytest.raises(ValueError):
        verify(pin, prefix, api_version="9.9.9" if field == "api" else "1.0.1")


@pytest.mark.parametrize(
    "key,value",
    [
        ("commit", "main"),
        ("commit", "--help"),
        ("repository", "https://example.com/repo"),
        ("cargo_lock_sha256", "bad"),
        ("rust_toolchain", "stable"),
    ],
)
def test_mutable_or_untrusted_build_inputs_refused(tmp_path, key, value):
    """CON-3/CON-5: installation resolves only explicit pinned upstream inputs."""
    from dora_runtime import load_pin

    original = Path(__file__).resolve().parents[2] / "dora-runtime.json"
    value_map = json.loads(original.read_text())
    value_map[key] = value
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(value_map))
    with pytest.raises(ValueError):
        load_pin(path)
