"""Recorded revisions distinguish tags, later commits, dirty trees and no Git."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from sarb_aod import contract
from sarb_aod.provenance import runtime_global_attrs


def test_revision_tags_and_tracked_modifications(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    def git(*args: str) -> str:
        return subprocess.check_output(
            [
                "git",
                "-C",
                str(tmp_path),
                "-c",
                "user.name=Provenance Test",
                "-c",
                "user.email=test@example.org",
                "-c",
                "commit.gpgsign=false",
                "-c",
                "tag.gpgsign=false",
                *args,
            ],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()

    git("init")
    source = tmp_path / "source.py"
    source.write_text("version = 1\n")
    git("add", "source.py")
    git("commit", "-m", "initial")
    monkeypatch.setattr(contract, "__file__", str(source))
    initial = contract.code_provenance()
    assert initial["repository_url"] == "https://github.com/NCAR/CERES-SARB-MODIS-VIIRS"
    assert initial["git_commit"] == git("rev-parse", "HEAD")
    assert initial["git_describe"] == initial["git_commit"][:12]
    assert initial["git_tags"] == "none"
    git("tag", "v1.0")
    tagged = contract.code_provenance()
    assert tagged["git_tags"] == "v1.0"
    assert tagged["git_describe"] == "v1.0"
    source.write_text("version = 2\n")
    assert contract.code_provenance()["git_describe"] == "v1.0-dirty"
    git("add", "source.py")
    git("commit", "-m", "next")
    later = contract.code_provenance()
    assert later["git_describe"] == f"v1.0-1-g{later['git_commit'][:12]}"
    assert later["git_tags"] == "none"


def test_revision_is_unknown_outside_checkout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(contract, "__file__", str(tmp_path / "source.py"))
    assert contract.code_provenance() == {
        "repository_url": "https://github.com/NCAR/CERES-SARB-MODIS-VIIRS",
        "git_commit": "unknown",
        "git_describe": "unknown",
        "git_tags": "unknown",
    }


def test_runtime_records_software_and_only_selected_environment(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("OPENBLAS_NUM_THREADS", "3")
    monkeypatch.setenv("EARTHDATA_PASSWORD", "must-not-appear")
    attrs = runtime_global_attrs()
    runtime = json.loads(attrs["compute_platform"])
    assert runtime["os_name"] and runtime["os_version"] and runtime["architecture"]
    environment = json.loads(attrs["python_environment"])
    assert environment["executable"] == sys.executable
    assert any(package["name"].lower() == "numpy" for package in environment["packages"])
    assert environment["native_libraries"]["hdf5"]
    execution = json.loads(attrs["execution_environment"])
    assert execution["OPENBLAS_NUM_THREADS"] == "3"
    assert "EARTHDATA_PASSWORD" not in execution
    assert "must-not-appear" not in json.dumps(attrs)
