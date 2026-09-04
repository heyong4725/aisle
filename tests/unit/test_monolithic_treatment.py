from __future__ import annotations

import pytest

from aisle.harness.monolithic import TreatmentTableError, validate_treatment_table

pytestmark = pytest.mark.unit


def _table() -> dict:
    h = "a" * 64
    return {
        "schema_version": "aisle.monolithic-treatment.v1",
        "rows": [
            {
                "id": "surface:runtime",
                "surface": "runtime",
                "classification": "identical",
                "typed": f"typed/runtime.py#sha256:{h}",
                "monolithic": f"mono/runtime.py#sha256:{h}",
                "justification": "same pinned runtime",
                "analysis": "hold constant",
            }
        ],
    }


def test_complete_table_is_content_addressed():
    report = validate_treatment_table(_table())
    assert report["valid"] is True
    assert report["immutable_id"].startswith("sha256:")


@pytest.mark.parametrize("field", ["schema_version", "rows"])
def test_empty_baseline_fails_closed(field):
    table = _table()
    table[field] = None
    with pytest.raises(TreatmentTableError):
        validate_treatment_table(table)


def test_missing_hash_fails_closed():
    table = _table()
    table["rows"][0]["typed"] = "typed/runtime.py"
    with pytest.raises(TreatmentTableError):
        validate_treatment_table(table)
