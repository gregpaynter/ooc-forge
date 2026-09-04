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

REFERENCE_PROMPT_MODEL = {
    "id": "qwen3-1.7b-q4-k-m",
    "name": "Qwen3 1.7B Q4_K_M",
    "filename": "Qwen3-1.7B-Q4_K_M.gguf",
    "size_label": "about 1.28 GB",
    "sha256": "d2387ca2dbfee2ffabce7120d3770dadca0b293052bc2f0e138fdc940d9bc7b5",
    "source_url": "https://huggingface.co/ggml-org/Qwen3-1.7B-GGUF/resolve/main/Qwen3-1.7B-Q4_K_M.gguf?download=true",
    "repository_url": "https://huggingface.co/ggml-org/Qwen3-1.7B-GGUF",
    "license_url": "https://huggingface.co/ggml-org/Qwen3-1.7B-GGUF",
    "path": "models/llm/Qwen3-1.7B-Q4_K_M.gguf",
}

REFERENCE_VIDEO_MODEL = {
    "id": "wan2.2-ti2v-5b",
    "name": "Wan2.2 TI2V 5B",
    "repository_url": "https://huggingface.co/Comfy-Org/Wan_2.2_ComfyUI_Repackaged",
    "license_url": "https://huggingface.co/Comfy-Org/Wan_2.2_ComfyUI_Repackaged",
    "files": [
        {
            "directory": "diffusion_models",
            "filename": "wan2.2_ti2v_5B_fp16.safetensors",
            "size_label": "about 10 GB",
            "sha256": "456f901338bd9eadbded3828b819109a9b68e8a525ca5cf8d0049a69fcfeca1e",
        },
        {
            "directory": "vae",
            "filename": "wan2.2_vae.safetensors",
            "size_label": "about 1.4 GB",
            "sha256": "e40321bd36b9709991dae2530eb4ac303dd168276980d3e9bc4b6e2b75fed156",
        },
        {
            "directory": "text_encoders",
            "filename": "umt5_xxl_fp8_e4m3fn_scaled.safetensors",
            "size_label": "about 6.7 GB",
            "sha256": "c3355d30191f1f066b26d93fba017ae9809dce6c627dda5f6a66eaa651204f68",
        },
    ],
}

BUSY_MODEL_STATES = {"QUEUED", "DOWNLOADING", "VERIFYING", "INSTALLING"}
MODEL_INSTALL_SERVICE = "ooc-forge-model-install.service"
UPSCALE_MODEL_INSTALL_SERVICE = "ooc-forge-upscale-model-install.service"
PROMPT_MODEL_INSTALL_SERVICE = "ooc-forge-prompt-model-install.service"
VIDEO_MODEL_INSTALL_SERVICE = "ooc-forge-video-model-install.service"


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


def prompt_model_install_status(config: Config) -> dict[str, Any]:
    return _status(config.data_root / "maintenance" / "prompt-model-install-status.json")


def video_model_install_status(config: Config) -> dict[str, Any]:
    return _status(config.data_root / "maintenance" / "video-model-install-status.json")


def prompt_model_path(config: Config) -> Path:
    return config.data_root / str(REFERENCE_PROMPT_MODEL["path"])


def prompt_model_ready(config: Config) -> bool:
    return prompt_model_path(config).is_file() and Path("/usr/local/bin/ooc-llama-cli").is_file()


def video_model_ready(config: Config) -> bool:
    return all(
        (config.data_root / "models" / str(item["directory"]) / str(item["filename"])).is_file()
        for item in REFERENCE_VIDEO_MODEL["files"]
    )


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


def prompt_model_install_running() -> bool:
    return _service_running(PROMPT_MODEL_INSTALL_SERVICE)


def video_model_install_running() -> bool:
    return _service_running(VIDEO_MODEL_INSTALL_SERVICE)


def _request_install(*, status: dict[str, Any], service: str, running: bool, label: str) -> None:
    state = str(status.get("state") or "").upper()
    if state in BUSY_MODEL_STATES and running:
        raise RuntimeError(f"{label} installation is already running.")
    subprocess.run(
        ["sudo", "/usr/bin/systemctl", "--no-block", "start", service],
        check=True,
        timeout=10,
    )


def request_reference_model_install(config: Config) -> None:
    _request_install(status=model_install_status(config), service=MODEL_INSTALL_SERVICE, running=model_install_running(), label="Reference image model")


def request_reference_upscale_model_install(config: Config) -> None:
    _request_install(status=upscale_model_install_status(config), service=UPSCALE_MODEL_INSTALL_SERVICE, running=upscale_model_install_running(), label="Print upscaler")


def request_reference_prompt_model_install(config: Config) -> None:
    _request_install(status=prompt_model_install_status(config), service=PROMPT_MODEL_INSTALL_SERVICE, running=prompt_model_install_running(), label="Prompt compiler model")


def request_reference_video_model_install(config: Config) -> None:
    _request_install(status=video_model_install_status(config), service=VIDEO_MODEL_INSTALL_SERVICE, running=video_model_install_running(), label="Video model stack")
