from __future__ import annotations

import json
import subprocess
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

BUSY_MODEL_STATES = {"QUEUED", "DOWNLOADING", "VERIFYING", "INSTALLING"}
MODEL_INSTALL_SERVICE = "ooc-forge-model-install.service"


def model_install_status(config: Config) -> dict[str, Any]:
    path = config.data_root / "maintenance" / "model-install-status.json"
    if not path.exists():
        return {"state": "IDLE", "model": None, "message": None}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        return {"state": "UNKNOWN", "model": None, "message": str(error)}
    return value if isinstance(value, dict) else {"state": "UNKNOWN", "model": None}


def model_install_running() -> bool:
    try:
        result = subprocess.run(
            ["/usr/bin/systemctl", "is-active", "--quiet", MODEL_INSTALL_SERVICE],
            check=False,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def request_reference_model_install(config: Config) -> None:
    status = model_install_status(config)
    state = str(status.get("state") or "").upper()
    if state in BUSY_MODEL_STATES and model_install_running():
        raise RuntimeError("Reference image model installation is already running.")
    # The installer is intentionally long-running (a multi-GB verified download).
    # Queue the systemd job and return immediately so the HTTP request does not
    # wait for the oneshot service to finish and falsely time out after 10s.
    subprocess.run(
        ["sudo", "/usr/bin/systemctl", "--no-block", "start", MODEL_INSTALL_SERVICE],
        check=True,
        timeout=10,
    )
