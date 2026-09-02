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
        response.raise_for_status()
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
    if config.default_checkpoint:
        for node in workflow.values():
            if isinstance(node, dict) and node.get("class_type") == "CheckpointLoaderSimple":
                node.setdefault("inputs", {})["ckpt_name"] = config.default_checkpoint
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
