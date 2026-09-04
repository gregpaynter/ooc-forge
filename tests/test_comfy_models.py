from __future__ import annotations

import json

import pytest

from forge.comfy import (
    ComfyClient,
    ComfyError,
    installed_checkpoints,
    installed_upscale_models,
    load_workflow,
)
from forge.config import Config
from forge.health import capabilities


def make_config(tmp_path, *, default_checkpoint=None):
    return Config(
        data_root=tmp_path,
        comfy_url="http://127.0.0.1:8188",
        ooc_origin=None,
        poll_interval=5,
        default_checkpoint=default_checkpoint,
    )


def install_image_workflow(config: Config) -> None:
    root = config.workflows_root / "manual-image"
    root.mkdir(parents=True)
    (root / "workflow.json").write_text(
        json.dumps(
            {
                "4": {
                    "inputs": {"ckpt_name": "OOC_CORE_IMAGE.safetensors"},
                    "class_type": "CheckpointLoaderSimple",
                },
                "6": {"inputs": {"text": "OOC Forge"}, "class_type": "CLIPTextEncode"},
            }
        ),
        encoding="utf-8",
    )
    (root / "manifest.json").write_text(
        json.dumps(
            {
                "id": "manual-image",
                "version": "2",
                "output_kind": "image",
                "bindings": [
                    {"source": "prompt", "node": "6", "input": "text"},
                    {"source": "checkpoint", "node": "4", "input": "ckpt_name"},
                ],
            }
        ),
        encoding="utf-8",
    )


def install_print_workflow(config: Config) -> None:
    root = config.workflows_root / "print-upscale"
    root.mkdir(parents=True)
    (root / "workflow.json").write_text(
        json.dumps(
            {
                "1": {"inputs": {"image": "study.png"}, "class_type": "LoadImage"},
                "2": {
                    "inputs": {"model_name": "RealESRGAN_x4plus.pth"},
                    "class_type": "UpscaleModelLoader",
                },
            }
        ),
        encoding="utf-8",
    )
    (root / "manifest.json").write_text(
        json.dumps(
            {
                "id": "print-upscale",
                "version": "1",
                "output_kind": "print_work",
                "scale": 4,
                "bindings": [
                    {"source": "input_image", "node": "1", "input": "image"},
                    {"source": "upscale_model", "node": "2", "input": "model_name"},
                ],
            }
        ),
        encoding="utf-8",
    )


def test_installed_checkpoints_discovers_nested_models(tmp_path):
    config = make_config(tmp_path)
    root = tmp_path / "models" / "checkpoints"
    (root / "sdxl").mkdir(parents=True)
    (root / "sdxl" / "base.safetensors").write_bytes(b"model")
    (root / "notes.txt").write_text("ignore", encoding="utf-8")

    assert installed_checkpoints(config) == ["sdxl/base.safetensors"]


def test_installed_upscale_models_discovers_print_models(tmp_path):
    config = make_config(tmp_path)
    root = tmp_path / "models" / "upscale_models"
    root.mkdir(parents=True)
    (root / "RealESRGAN_x4plus.pth").write_bytes(b"model")
    (root / "notes.txt").write_text("ignore", encoding="utf-8")

    assert installed_upscale_models(config) == ["RealESRGAN_x4plus.pth"]


def test_load_workflow_fails_clearly_without_model(tmp_path):
    config = make_config(tmp_path)
    install_image_workflow(config)

    with pytest.raises(ComfyError, match="No image checkpoint is installed/selected"):
        load_workflow(config, "manual-image", {"prompt": "test"})


def test_load_workflow_uses_only_installed_checkpoint(tmp_path):
    config = make_config(tmp_path)
    install_image_workflow(config)
    root = tmp_path / "models" / "checkpoints"
    root.mkdir(parents=True)
    (root / "model.safetensors").write_bytes(b"model")

    workflow, _ = load_workflow(config, "manual-image", {"prompt": "test"})

    assert workflow["4"]["inputs"]["ckpt_name"] == "model.safetensors"


def test_load_workflow_rejects_uninstalled_selected_checkpoint(tmp_path):
    config = make_config(tmp_path)
    install_image_workflow(config)
    root = tmp_path / "models" / "checkpoints"
    root.mkdir(parents=True)
    (root / "installed.safetensors").write_bytes(b"model")

    with pytest.raises(ComfyError, match="Image checkpoint is not installed: missing.safetensors"):
        load_workflow(
            config,
            "manual-image",
            {"prompt": "test", "checkpoint": "missing.safetensors"},
        )


def test_print_workflow_requires_installed_upscale_model(tmp_path):
    config = make_config(tmp_path)
    install_print_workflow(config)

    with pytest.raises(ComfyError, match="No print upscale model is installed/selected"):
        load_workflow(config, "print-upscale", {"input_image": "study.png"})

    root = tmp_path / "models" / "upscale_models"
    root.mkdir(parents=True)
    (root / "RealESRGAN_x4plus.pth").write_bytes(b"model")
    workflow, _ = load_workflow(config, "print-upscale", {"input_image": "study.png"})
    assert workflow["2"]["inputs"]["model_name"] == "RealESRGAN_x4plus.pth"


def test_capabilities_separate_image_from_print_work(tmp_path):
    config = make_config(tmp_path)
    install_image_workflow(config)
    install_print_workflow(config)
    value = capabilities(config)
    assert value["image"] is False
    assert value["manual_create"] is False
    assert value["print_work"] is False

    checkpoints = tmp_path / "models" / "checkpoints"
    checkpoints.mkdir(parents=True)
    (checkpoints / "model.safetensors").write_bytes(b"model")
    value = capabilities(config)
    assert value["image"] is True
    assert value["manual_create"] is True
    assert value["print_work"] is False

    upscale = tmp_path / "models" / "upscale_models"
    upscale.mkdir(parents=True)
    (upscale / "RealESRGAN_x4plus.pth").write_bytes(b"model")
    assert capabilities(config)["print_work"] is True


def test_queue_preserves_comfyui_validation_error(monkeypatch, tmp_path):
    config = make_config(tmp_path)

    class FakeResponse:
        ok = False
        status_code = 400
        reason = "Bad Request"
        text = "validation failed"

        def json(self):
            return {"error": {"type": "prompt_outputs_failed_validation"}}

    monkeypatch.setattr("forge.comfy.requests.post", lambda *args, **kwargs: FakeResponse())

    with pytest.raises(ComfyError) as captured:
        ComfyClient(config).queue({})

    message = str(captured.value)
    assert "HTTP 400" in message
    assert "prompt_outputs_failed_validation" in message
