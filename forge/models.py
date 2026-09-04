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
    "size_bytes": 6_939_102_400,
    "sha256": "31e35c80fc4829d14f90153f4c74cd59c90b779f6afe05a74cd6120b893f7e5b",
    "source_url": "https://huggingface.co/stabilityai/stable-diffusion-xl-base-1.0/resolve/main/sd_xl_base_1.0.safetensors?download=true",
    "license_url": "https://huggingface.co/stabilityai/stable-diffusion-xl-base-1.0/blob/main/LICENSE.md",
}


def model_install_status(config: Config) -> dict[str, Any]:
    path = config.data_root / "maintenance" / "model-install-status.json"
    if not path.exists():
        return {"state": "IDLE", "model": None, "message": None}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        return {"state": "UNKNOWN", "model": None, "message": str(error)}
    return value if isinstance(value, dict) else {"state": "UNKNOWN", "model": None}


def request_reference_model_install(config: Config) -> None:
    status = model_install_status(config)
    if str(status.get("state") or "").upper() in {"QUEUED", "DOWNLOADING", "VERIFYING", "INSTALLING"}:
        raise RuntimeError("Reference image model installation is already running.")
    subprocess.run(
        ["sudo", "/usr/bin/systemctl", "start", "ooc-forge-model-install.service"],
        check=True,
        timeout=10,
    )
