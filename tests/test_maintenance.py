from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from forge.config import Config
from forge.maintenance import git_update_status, request_git_update, validate_git_ref


ROOT = Path(__file__).resolve().parents[1]


def make_config(tmp_path: Path) -> Config:
    return Config(
        data_root=tmp_path,
        comfy_url="http://127.0.0.1:8188",
        ooc_origin=None,
        poll_interval=5,
        default_checkpoint=None,
    )


@pytest.mark.parametrize(
    "ref",
    ["main", "v0.1.0", "feature/git-update", "842edc91b6eb6859a4bb77cd8856ecb770658bec"],
)
def test_validate_git_ref_accepts_expected_refs(ref: str):
    assert validate_git_ref(ref) == ref


@pytest.mark.parametrize(
    "ref",
    ["-oops", "../main", "feature//bad", "main@{1}", "bad ref", "release.lock", "main/"],
)
def test_validate_git_ref_rejects_unsafe_refs(ref: str):
    with pytest.raises(ValueError):
        validate_git_ref(ref)


def test_request_git_update_writes_request_and_only_starts_fixed_service(tmp_path: Path, monkeypatch):
    config = make_config(tmp_path)
    calls: list[list[str]] = []

    def fake_run(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr("forge.maintenance.subprocess.run", fake_run)
    request_git_update(config, "feature/git-update")

    request_value = json.loads(
        (tmp_path / "maintenance" / "git-update-request.json").read_text(encoding="utf-8")
    )
    assert request_value["ref"] == "feature/git-update"
    assert calls == [
        [
            "/usr/bin/sudo",
            "-n",
            "/usr/bin/systemctl",
            "start",
            "ooc-forge-git-update.service",
        ]
    ]


def test_git_update_status_defaults_to_idle(tmp_path: Path):
    status = git_update_status(make_config(tmp_path))
    assert status["state"] == "IDLE"
    assert status["commit"] is None


def test_git_updater_seeds_all_immutable_workflows():
    updater = (ROOT / "scripts/ooc-forge-git-update").read_text(encoding="utf-8")
    for workflow in ("manual-image", "manual-image-reference", "print-upscale", "video-wan22-ti2v"):
        assert workflow in updater
    assert '"$FORGE_DATA_ROOT/workflows/$workflow/manifest.json"' in updater
    assert '"$FORGE_DATA_ROOT/workflows/$workflow/workflow.json"' in updater
