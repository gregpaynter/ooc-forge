from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from forge.config import Config


REFERENCE_IMAGE_MODEL = {
    "id": "sdxl-base-1.0",
    "name": "Stable Diffusion XL Base 1.0",
    "filename": "sd_xl_base_1.0.safetensors",
    "size_label": "about 6.94 GB",
    "sha256": "31e35c80fc4829d14f90153f4c74cd59c90b779f6afe05a74cd6120b893f7e5b",
    "source_url": "https://huggingface.co/stabilityai/stable-diffusion-xl-base-1.0/resolve/main/sd_xl_base_1.0.safetensors?download=true",
    "repository_url": "https://huggingface.co/stabilityai/stable-diffusion-xl-base-1.0",
    "license_url": "https://huggingface.co/stabilityai/stable-diffusion-xl-base-1.0/blob/main/LICENSE.md",
}

REFERENCE_UPSCALE_MODEL = {
    "id": "realesrgan-x4plus",
    "name": "RealESRGAN x4plus",
    "filename": "RealESRGAN_x4plus.pth",
    "size_label": "about 67 MB",
    "sha256": "4fa0d38905f75ac06eb49a7951b426670021be3018265fd191d2125df9d682f1",
    "source_url": "https://github.com/xinntao/Real-ESRGAN/releases/download/v0.1.0/RealESRGAN_x4plus.pth",
    "repository_url": "https://github.com/xinntao/Real-ESRGAN",
    "license_url": "https://github.com/xinntao/Real-ESRGAN/blob/master/LICENSE",
    "scale": 4,
}

BUSY_MODEL_STATES = {"QUEUED", "DOWNLOADING", "VERIFYING", "INSTALLING"}
MODEL_INSTALL_SERVICE = "ooc-forge-model-install.service"
UPSCALE_MODEL_INSTALL_SERVICE = "ooc-forge-upscale-model-install.service"


def _status(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"state": "IDLE", "model": None, "message": None}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        return {"state": "UNKNOWN", "model": None, "message": str(error)}
    return value if isinstance(value, dict) else {"state": "UNKNOWN", "model": None}


def model_install_status(config: Config) -> dict[str, Any]:
    return _status(config.data_root / "maintenance" / "model-install-status.json")


def upscale_model_install_status(config: Config) -> dict[str, Any]:
    return _status(config.data_root / "maintenance" / "upscale-model-install-status.json")


def _service_running(service: str) -> bool:
    try:
        result = subprocess.run(
            ["/usr/bin/systemctl", "is-active", "--quiet", service],
            check=False,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def model_install_running() -> bool:
    return _service_running(MODEL_INSTALL_SERVICE)


def upscale_model_install_running() -> bool:
    return _service_running(UPSCALE_MODEL_INSTALL_SERVICE)


def _request_install(config: Config, *, status: dict[str, Any], service: str, running: bool, label: str) -> None:
    state = str(status.get("state") or "").upper()
    if state in BUSY_MODEL_STATES and running:
        raise RuntimeError(f"{label} installation is already running.")
    subprocess.run(
        ["sudo", "/usr/bin/systemctl", "--no-block", "start", service],
        check=True,
        timeout=10,
    )


def request_reference_model_install(config: Config) -> None:
    _request_install(
        config,
        status=model_install_status(config),
        service=MODEL_INSTALL_SERVICE,
        running=model_install_running(),
        label="Reference image model",
    )


def request_reference_upscale_model_install(config: Config) -> None:
    _request_install(
        config,
        status=upscale_model_install_status(config),
        service=UPSCALE_MODEL_INSTALL_SERVICE,
        running=upscale_model_install_running(),
        label="Print upscaler",
    )
