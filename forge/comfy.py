from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from uuid import uuid4

import requests

from forge.config import Config


class ComfyError(RuntimeError):
    pass


CHECKPOINT_EXTENSIONS = {".safetensors", ".ckpt", ".pt", ".pth"}
UPSCALE_MODEL_EXTENSIONS = {".safetensors", ".pt", ".pth"}


def _installed_models(root: Path, extensions: set[str]) -> list[str]:
    if not root.exists():
        return []
    return sorted(
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and path.suffix.lower() in extensions
    )


def installed_checkpoints(config: Config) -> list[str]:
    return _installed_models(config.data_root / "models" / "checkpoints", CHECKPOINT_EXTENSIONS)


def installed_upscale_models(config: Config) -> list[str]:
    return _installed_models(config.data_root / "models" / "upscale_models", UPSCALE_MODEL_EXTENSIONS)


def _nodes(workflow: dict[str, Any], class_type: str) -> list[dict[str, Any]]:
    return [
        node
        for node in workflow.values()
        if isinstance(node, dict) and node.get("class_type") == class_type
    ]


def _apply_checkpoint(config: Config, workflow: dict[str, Any], request: dict[str, Any]) -> None:
    nodes = _nodes(workflow, "CheckpointLoaderSimple")
    if not nodes:
        return

    installed = installed_checkpoints(config)
    requested = str(request.get("checkpoint") or "").strip() or None
    selected = requested or config.default_checkpoint

    if selected is None:
        configured = str(nodes[0].get("inputs", {}).get("ckpt_name") or "").strip()
        if configured in installed:
            selected = configured
        elif len(installed) == 1:
            selected = installed[0]

    if selected is None:
        raise ComfyError(
            "No image checkpoint is installed/selected. Install a checkpoint under "
            "/forge-data/models/checkpoints/ and select it in Manual Create, or set "
            "FORGE_DEFAULT_CHECKPOINT."
        )

    if selected not in installed:
        available = ", ".join(installed) if installed else "none"
        raise ComfyError(
            f"Image checkpoint is not installed: {selected}. "
            f"Installed checkpoints: {available}. Models belong under "
            "/forge-data/models/checkpoints/."
        )

    for node in nodes:
        node.setdefault("inputs", {})["ckpt_name"] = selected


def _apply_upscale_model(config: Config, workflow: dict[str, Any], request: dict[str, Any]) -> None:
    nodes = _nodes(workflow, "UpscaleModelLoader")
    if not nodes:
        return

    installed = installed_upscale_models(config)
    requested = str(request.get("upscale_model") or "").strip() or None
    selected = requested

    if selected is None:
        configured = str(nodes[0].get("inputs", {}).get("model_name") or "").strip()
        if configured in installed:
            selected = configured
        elif len(installed) == 1:
            selected = installed[0]

    if selected is None:
        raise ComfyError(
            "No print upscale model is installed/selected. Install the reference print upscaler "
            "from Models or add a compatible model under /forge-data/models/upscale_models/."
        )

    if selected not in installed:
        available = ", ".join(installed) if installed else "none"
        raise ComfyError(
            f"Print upscale model is not installed: {selected}. "
            f"Installed upscale models: {available}."
        )

    for node in nodes:
        node.setdefault("inputs", {})["model_name"] = selected


class ComfyClient:
    def __init__(self, config: Config):
        self.config = config
        self.client_id = str(uuid4())

    def health(self) -> None:
        response = requests.get(f"{self.config.comfy_url}/system_stats", timeout=5)
        response.raise_for_status()

    def queue(self, workflow: dict[str, Any]) -> str:
        response = requests.post(
            f"{self.config.comfy_url}/prompt",
            json={"prompt": workflow, "client_id": self.client_id},
            timeout=20,
        )
        if not response.ok:
            try:
                detail = json.dumps(response.json(), sort_keys=True)
            except ValueError:
                detail = response.text.strip() or response.reason
            raise ComfyError(
                f"ComfyUI rejected the workflow (HTTP {response.status_code}): {detail}"
            )
        payload = response.json()
        if payload.get("error"):
            raise ComfyError(json.dumps(payload["error"], sort_keys=True))
        prompt_id = payload.get("prompt_id")
        if not prompt_id:
            raise ComfyError("ComfyUI did not return prompt_id")
        return str(prompt_id)

    def wait(self, prompt_id: str, *, timeout: int = 3600) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            response = requests.get(
                f"{self.config.comfy_url}/history/{prompt_id}", timeout=20
            )
            response.raise_for_status()
            payload = response.json()
            entry = payload.get(prompt_id)
            if entry:
                status = entry.get("status") or {}
                messages = status.get("messages") or []
                for message in messages:
                    if isinstance(message, list) and message and message[0] == "execution_error":
                        raise ComfyError(json.dumps(message, sort_keys=True))
                if status.get("completed") or entry.get("outputs"):
                    return dict(entry)
            time.sleep(1)
        raise TimeoutError(f"ComfyUI prompt {prompt_id} did not complete")

    def download(self, reference: dict[str, Any], destination: Path) -> None:
        params = {
            "filename": reference["filename"],
            "subfolder": reference.get("subfolder", ""),
            "type": reference.get("type", "output"),
        }
        response = requests.get(
            f"{self.config.comfy_url}/view?{urlencode(params)}",
            timeout=120,
            stream=True,
        )
        response.raise_for_status()
        destination.parent.mkdir(parents=True, exist_ok=True)
        temp = destination.with_suffix(destination.suffix + ".tmp")
        with temp.open("wb") as handle:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    handle.write(chunk)
        temp.replace(destination)


def load_workflow(config: Config, workflow_id: str, request: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    root = config.workflows_root / workflow_id
    workflow_path = root / "workflow.json"
    manifest_path = root / "manifest.json"
    if not workflow_path.exists() or not manifest_path.exists():
        raise FileNotFoundError(f"Workflow is not installed: {workflow_id}")
    workflow = json.loads(workflow_path.read_text(encoding="utf-8"))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for binding in manifest.get("bindings", []):
        source = str(binding["source"])
        value = request.get(source, binding.get("default"))
        if value is None:
            continue
        node = workflow[str(binding["node"])]
        node["inputs"][str(binding["input"])] = value
    _apply_checkpoint(config, workflow, request)
    _apply_upscale_model(config, workflow, request)
    return workflow, manifest


def output_references(history: dict[str, Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for output in (history.get("outputs") or {}).values():
        if not isinstance(output, dict):
            continue
        for key in ("images", "gifs", "audio"):
            values = output.get(key)
            if not isinstance(values, list):
                continue
            for item in values:
                if isinstance(item, dict) and item.get("filename"):
                    result.append(dict(item))
    return result
