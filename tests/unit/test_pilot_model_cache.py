"""BND-2/MON-6/MON-8: prepare only pinned public identity files for worker runtime."""

import hashlib
import json

import pytest

from aisle.harness.pilot_model_cache import prepare_cache


def inputs(tmp_path):
    root = tmp_path / "controller"
    lock = root / "src/aisle/verifier/models.lock"
    lock.parent.mkdir(parents=True)
    storage = tmp_path / "models--public--model"
    source = storage / "snapshots" / ("a" * 40)
    source.mkdir(parents=True)
    (source / "weights.bin").write_bytes(b"public pinned weights")
    (source / "private-note.txt").write_text("must never be copied")
    tree = storage / "trees" / ("a" * 40 + ".json")
    tree.parent.mkdir()
    tree.write_text(
        json.dumps(
            {
                "format_version": 1,
                "files": {
                    "weights.bin": {"size": 21, "blob_id": "b" * 40},
                    "private-note.txt": {"size": 20, "blob_id": "c" * 40},
                },
            }
        )
    )
    lock.write_text(
        json.dumps(
            {
                "lock_version": 1,
                "models": {
                    "identity": {
                        "repo": "public/model",
                        "revision": "a" * 40,
                        "files_sha256": {
                            "weights.bin": hashlib.sha256(b"public pinned weights").hexdigest()
                        },
                    }
                },
            }
        )
    )
    return root, source, tmp_path / "cache"


@pytest.mark.unit
def test_cache_copies_only_verified_pin_and_declares_offline_worker_environment(tmp_path):
    """BND-2/MON-6: operator cache extensions and private files confer no new authority."""
    root, source, output = inputs(tmp_path)
    report = prepare_cache(root, source, output)
    files = [p for p in output.rglob("*") if p.is_file()]
    assert len(files) == 2
    weights = next(p for p in files if p.name == "weights.bin")
    assert weights.read_bytes() == b"public pinned weights"
    assert all(p.stat().st_mode & 0o222 == 0 for p in files)
    assert report["worker_environment"] == {
        "HF_HOME": str(output),
        "HF_HUB_OFFLINE": "1",
        "TRANSFORMERS_OFFLINE": "1",
    }
    assert report["study_collection_authorized"] is False


@pytest.mark.sim
def test_prepared_snapshot_loads_without_repository_tree_network_request(tmp_path, monkeypatch):
    """BND-2/MON-6/MON-8: the real pinned downloader works with network forbidden."""
    from huggingface_hub import HfApi, snapshot_download

    root, source, output = inputs(tmp_path)
    report = prepare_cache(root, source, output)

    def forbidden(*args, **kwargs):
        raise AssertionError("model preparation left a network dependency")

    monkeypatch.setattr(HfApi, "list_repo_tree", forbidden)
    snapshot = snapshot_download("public/model", revision="a" * 40, cache_dir=output / "hub")
    assert (type(output)(snapshot) / "weights.bin").read_bytes() == b"public pinned weights"
    tree = output / "hub/models--public--model/trees" / ("a" * 40 + ".json")
    assert set(json.loads(tree.read_text())["files"]) == {"weights.bin"}
    assert (
        report["files"][str(tree.relative_to(output))]
        == hashlib.sha256(tree.read_bytes()).hexdigest()
    )


@pytest.mark.unit
@pytest.mark.parametrize("field,value", [("size", 22), ("lfs_sha256", "d" * 64)])
def test_tree_metadata_must_match_verified_snapshot(tmp_path, field, value):
    """MON-8: stale public download metadata cannot be bound to pinned bytes."""
    root, source, output = inputs(tmp_path)
    tree = source.parent.parent / "trees" / ("a" * 40 + ".json")
    data = json.loads(tree.read_text())
    data["files"]["weights.bin"][field] = value
    tree.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="tree metadata"):
        prepare_cache(root, source, output)


@pytest.mark.unit
def test_changed_model_refuses_and_preserves_failure_directory(tmp_path):
    """MON-8: mismatched model bytes cannot become an admitted cache."""
    root, source, output = inputs(tmp_path)
    (source / "weights.bin").write_bytes(b"different model")
    with pytest.raises(ValueError, match="digest"):
        prepare_cache(root, source, output)
    assert output.exists()


@pytest.mark.unit
def test_existing_cache_cannot_be_overwritten(tmp_path):
    """MON-6/MON-8: do not overwrite a runtime tree that may already be bound."""
    root, source, output = inputs(tmp_path)
    output.mkdir()
    with pytest.raises(ValueError, match="fresh"):
        prepare_cache(root, source, output)


@pytest.mark.unit
def test_missing_cli_arguments_produce_json_refusal(monkeypatch, capsys):
    """CON-8: invalid cache-preparation commands return one JSON refusal."""
    from aisle.harness.pilot_model_cache import main

    monkeypatch.setattr("sys.argv", ["pilot-model-cache"])
    assert main() == 1
    assert json.loads(capsys.readouterr().out)["ok"] is False
