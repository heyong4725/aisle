"""BMK-7/BMK-8: archive source identity must be verified without a checkout."""

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
pytestmark = pytest.mark.unit


def test_archive_round_trip_verifies_committed_bytes(tmp_path):
    """BMK-8/BMK-13: an archive retains checkable Git content identity."""
    import tarfile

    from source_archive import build_archive, verify_source

    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / "src").mkdir()
    (repo / "src" / "example.py").write_text("print('source')\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "commit",
            "-qm",
            "fixture",
        ],
        cwd=repo,
        check=True,
    )
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    # The export must use committed blobs, not an uncommitted working-tree edit.
    (repo / "src" / "example.py").write_text("uncommitted edit")
    archive = tmp_path / "source.tar"
    build_archive(repo, revision, archive)
    extracted = tmp_path / "extracted"
    extracted.mkdir()
    with tarfile.open(archive) as source:
        source.extractall(extracted, filter="data")
    assert not (extracted / ".git").exists()
    record = verify_source(extracted, expected_commit=revision)
    assert record["git_sha"] == revision
    assert record["files_verified"] == 1
    with pytest.raises(ValueError, match="commit mismatch"):
        verify_source(extracted, expected_commit="0" * 40)
    assert record["publisher_authenticated"] is False
    from quickstart import _source_provenance

    provenance = _source_provenance({"git_sha": ""}, extracted)
    assert provenance["git_sha"] == revision
    assert provenance["source_archive"] == record
    (extracted / "src" / "example.py").write_text("print('changed')\n")
    with pytest.raises(ValueError, match="mismatch"):
        verify_source(extracted, expected_commit=revision)


def test_archive_identity_requires_commit_evidence(tmp_path):
    """BMK-14: a directory without archive provenance cannot acquire an attested revision."""
    from source_archive import verify_source

    with pytest.raises(ValueError, match="provenance"):
        verify_source(tmp_path)
