from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from forge.config import Config
from forge.models import (
    REFERENCE_IMAGE_MODEL,
    model_install_running,
    model_install_status,
    request_reference_model_install,
)


def make_config(tmp_path):
    return Config(
        data_root=tmp_path,
        comfy_url="http://127.0.0.1:8188",
        ooc_origin=None,
        poll_interval=5,
        default_checkpoint=None,
    )


def test_reference_image_model_is_pinned_to_official_sdxl_checkpoint():
    assert REFERENCE_IMAGE_MODEL["id"] == "sdxl-base-1.0"
    assert REFERENCE_IMAGE_MODEL["filename"] == "sd_xl_base_1.0.safetensors"
    assert REFERENCE_IMAGE_MODEL["sha256"] == "31e35c80fc4829d14f90153f4c74cd59c90b779f6afe05a74cd6120b893f7e5b"
    assert REFERENCE_IMAGE_MODEL["repository_url"] == "https://huggingface.co/stabilityai/stable-diffusion-xl-base-1.0"
    assert REFERENCE_IMAGE_MODEL["source_url"].startswith(
        "https://huggingface.co/stabilityai/stable-diffusion-xl-base-1.0/resolve/main/"
    )


def test_model_install_status_defaults_to_idle(tmp_path):
    assert model_install_status(make_config(tmp_path)) == {
        "state": "IDLE",
        "model": None,
        "message": None,
    }


def test_model_install_status_reads_persistent_status(tmp_path):
    config = make_config(tmp_path)
    path = tmp_path / "maintenance" / "model-install-status.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"state": "COMPLETED", "message": "ready"}), encoding="utf-8")
    assert model_install_status(config)["state"] == "COMPLETED"


def test_model_install_running_uses_systemd_state(monkeypatch):
    monkeypatch.setattr(
        "forge.models.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0),
    )
    assert model_install_running() is True


def test_request_reference_model_install_starts_fixed_service_without_blocking(monkeypatch, tmp_path):
    config = make_config(tmp_path)
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        if command[:3] == ["/usr/bin/systemctl", "is-active", "--quiet"]:
            return SimpleNamespace(returncode=3)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr("forge.models.subprocess.run", fake_run)
    request_reference_model_install(config)
    command, kwargs = calls[-1]
    assert command == [
        "sudo",
        "/usr/bin/systemctl",
        "--no-block",
        "start",
        "ooc-forge-model-install.service",
    ]
    assert kwargs["check"] is True
    assert kwargs["timeout"] == 10


def test_request_reference_model_install_rejects_live_duplicate(monkeypatch, tmp_path):
    config = make_config(tmp_path)
    path = tmp_path / "maintenance" / "model-install-status.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"state": "DOWNLOADING"}), encoding="utf-8")
    monkeypatch.setattr(
        "forge.models.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0),
    )
    with pytest.raises(RuntimeError, match="already running"):
        request_reference_model_install(config)
