from __future__ import annotations

import shutil
import subprocess
from typing import Any

import requests

from forge import __version__
from forge.comfy import installed_checkpoints, installed_upscale_models
from forge.config import Config
from forge.models import REFERENCE_AUDIO_MODEL, audio_model_ready, prompt_model_ready, video_model_ready


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
    image_workflow_ready = (config.workflows_root / "manual-image" / "workflow.json").exists()
    audio_checkpoint_names = {
        str(item["filename"])
        for item in REFERENCE_AUDIO_MODEL["files"]
        if str(item["directory"]) == "checkpoints"
    }
    image_model_ready = any(name not in audio_checkpoint_names for name in installed_checkpoints(config))
    image_ready = image_workflow_ready and image_model_ready
    deterministic_image_ready = image_ready and shutil.which("ffmpeg") is not None
    print_workflow_ready = (config.workflows_root / "print-upscale" / "workflow.json").exists()
    print_model_ready = bool(installed_upscale_models(config))
    print_ready = image_ready and print_workflow_ready and print_model_ready
    video_workflow_ready = (config.workflows_root / "video-wan22-ti2v" / "workflow.json").exists()
    video_ready = (
        image_ready
        and video_workflow_ready
        and video_model_ready(config)
        and prompt_model_ready(config)
        and shutil.which("ffmpeg") is not None
    )
    audio_workflow_ready = (config.workflows_root / "audio-stable-audio3" / "workflow.json").exists()
    audio_ready = (
        image_ready
        and audio_workflow_ready
        and audio_model_ready(config)
        and shutil.which("ffmpeg") is not None
    )
    return {
        "manual_create": image_ready,
        "comfyui": True,
        "image": image_ready,
        "web_thumbnail": deterministic_image_ready,
        "etching_plate": deterministic_image_ready,
        "print_work": print_ready,
        "video": video_ready,
        "video_mobile": video_ready,
        "audio": audio_ready,
    }
