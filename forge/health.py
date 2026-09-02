from __future__ import annotations

import shutil
import subprocess
from typing import Any

import requests

from forge import __version__
from forge.config import Config


def _gpu() -> dict[str, Any]:
    command = [
        "nvidia-smi",
        "--query-gpu=name,memory.total,memory.used,temperature.gpu,driver_version",
        "--format=csv,noheader,nounits",
    ]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=10, check=True)
        line = result.stdout.strip().splitlines()[0]
        name, total, used, temperature, driver = [part.strip() for part in line.split(",", 4)]
        return {
            "status": "ready",
            "name": name,
            "vram_mb": int(total),
            "vram_used_mb": int(used),
            "temperature_c": int(temperature),
            "driver": driver,
        }
    except Exception as error:
        return {"status": "unavailable", "error": str(error)}


def _comfy(config: Config) -> dict[str, Any]:
    try:
        response = requests.get(f"{config.comfy_url}/system_stats", timeout=3)
        response.raise_for_status()
        return {"status": "ready"}
    except Exception as error:
        return {"status": "unavailable", "error": str(error)}


def report(config: Config) -> dict[str, Any]:
    usage = shutil.disk_usage(config.data_root)
    gpu = _gpu()
    comfy = _comfy(config)
    status = "healthy" if gpu["status"] == "ready" and comfy["status"] == "ready" else "degraded"
    return {
        "status": status,
        "runtime_version": __version__,
        "gpu": gpu,
        "comfyui": comfy,
        "storage": {
            "total_bytes": usage.total,
            "used_bytes": usage.used,
            "free_bytes": usage.free,
        },
    }


def capabilities(config: Config) -> dict[str, Any]:
    # v1 advertises what the appliance runtime can execute; installed workflows may narrow this later.
    return {
        "manual_create": True,
        "comfyui": True,
        "image": (config.workflows_root / "manual-image" / "workflow.json").exists(),
        "video": False,
        "audio": False,
    }
