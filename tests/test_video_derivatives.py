from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from forge.config import Config
from forge.health import capabilities
from forge.models import REFERENCE_PROMPT_MODEL, REFERENCE_VIDEO_MODEL
from forge.prompt_compiler import _extract_json, _normalise_shots
from forge.video import VIDEO_PROFILES, _frames_for_duration, video_profile


def make_config(tmp_path: Path) -> Config:
    return Config(
        data_root=tmp_path,
        comfy_url="http://127.0.0.1:8188",
        ooc_origin=None,
        poll_interval=5,
        default_checkpoint=None,
    )


def install_image_and_video_workflows(config: Config) -> None:
    image = config.workflows_root / "manual-image"
    image.mkdir(parents=True)
    (image / "workflow.json").write_text("{}", encoding="utf-8")
    video = config.workflows_root / "video-wan22-ti2v"
    video.mkdir(parents=True)
    (video / "workflow.json").write_text("{}", encoding="utf-8")


def test_prompt_compiler_extracts_json_after_reasoning_block():
    text = '<think>private scratch</think>\n{"derived_video_prompt":"slow rain","resolved_video_prompt":"slow rain and dolly","shots":[]}'
    value = _extract_json(text)
    assert value["derived_video_prompt"] == "slow rain"
    assert value["resolved_video_prompt"] == "slow rain and dolly"


def test_shot_plan_covers_duration_with_max_five_second_segments():
    value = {
        "derived_video_prompt": "slow movement",
        "resolved_video_prompt": "slow movement preserving the work",
        "shots": [
            {"duration": 9, "instruction": "push forward"},
            {"duration": 2, "instruction": "track right"},
        ],
    }
    shots = _normalise_shots(value, 12.0)
    assert round(sum(float(item["duration"]) for item in shots), 6) == 12.0
    assert all(1.0 <= float(item["duration"]) <= 5.0 for item in shots)
    assert shots[0]["instruction"] == "push forward"


def test_wan_frame_count_is_4n_plus_1_and_covers_planned_duration():
    for seconds in (1.0, 2.5, 5.0):
        frames = _frames_for_duration(seconds)
        assert (frames - 1) % 4 == 0
        assert frames >= round(seconds * 24)


def test_video_profiles_preserve_production_and_add_fast_draft():
    production = video_profile("production")
    draft = video_profile("draft")

    assert production == {
        "id": "production",
        "label": "Production 720p",
        "width": 1280,
        "height": 704,
        "fps": 24,
        "steps": 20,
        "master_preset": "slow",
        "master_crf": 14,
        "mobile": True,
    }
    assert draft["width"] == 832
    assert draft["height"] == 480
    assert draft["fps"] == 16
    assert draft["steps"] == 16
    assert draft["mobile"] is False
    assert _frames_for_duration(5.0, fps=int(draft["fps"])) == 81
    assert _frames_for_duration(5.0, fps=int(production["fps"])) == 121
    assert VIDEO_PROFILES["draft"]["width"] < VIDEO_PROFILES["production"]["width"]


def test_video_workflow_exposes_sampling_steps_binding():
    root = Path(__file__).resolve().parents[1]
    manifest = json.loads((root / "workflows/video-wan22-ti2v/manifest.json").read_text(encoding="utf-8"))
    bindings = {(item["source"], str(item["node"]), item["input"]) for item in manifest["bindings"]}
    assert ("steps", "3", "steps") in bindings


def test_prompt_and_video_model_pins_are_exact():
    assert REFERENCE_PROMPT_MODEL["filename"] == "Qwen3-1.7B-Q4_K_M.gguf"
    assert REFERENCE_PROMPT_MODEL["sha256"] == "d2387ca2dbfee2ffabce7120d3770dadca0b293052bc2f0e138fdc940d9bc7b5"
    files = {(item["directory"], item["filename"]): item["sha256"] for item in REFERENCE_VIDEO_MODEL["files"]}
    assert files[("diffusion_models", "wan2.2_ti2v_5B_fp16.safetensors")] == "456f901338bd9eadbded3828b819109a9b68e8a525ca5cf8d0049a69fcfeca1e"
    assert files[("vae", "wan2.2_vae.safetensors")] == "e40321bd36b9709991dae2530eb4ac303dd168276980d3e9bc4b6e2b75fed156"
    assert files[("text_encoders", "umt5_xxl_fp8_e4m3fn_scaled.safetensors")] == "c3355d30191f1f066b26d93fba017ae9809dce6c627dda5f6a66eaa651204f68"


def test_video_capability_requires_prompt_and_wan_stacks(monkeypatch, tmp_path):
    config = make_config(tmp_path)
    install_image_and_video_workflows(config)
    checkpoint = tmp_path / "models" / "checkpoints" / "model.safetensors"
    checkpoint.parent.mkdir(parents=True)
    checkpoint.write_bytes(b"model")
    monkeypatch.setattr("forge.health.shutil.which", lambda name: "/usr/bin/ffmpeg" if name == "ffmpeg" else None)
    monkeypatch.setattr("forge.health.prompt_model_ready", lambda cfg: False)
    monkeypatch.setattr("forge.health.video_model_ready", lambda cfg: False)
    assert capabilities(config)["video"] is False

    monkeypatch.setattr("forge.health.prompt_model_ready", lambda cfg: True)
    assert capabilities(config)["video"] is False

    monkeypatch.setattr("forge.health.video_model_ready", lambda cfg: True)
    value = capabilities(config)
    assert value["video"] is True
    assert value["video_mobile"] is True


def test_video_workflow_model_preflight_fails_before_comfy_queue(tmp_path):
    from forge.comfy import ComfyError, load_workflow

    config = make_config(tmp_path)
    root = config.workflows_root / "video"
    root.mkdir(parents=True)
    (root / "workflow.json").write_text(
        json.dumps(
            {
                "1": {"class_type": "UNETLoader", "inputs": {"unet_name": "wan.safetensors"}},
                "2": {"class_type": "CLIPLoader", "inputs": {"clip_name": "umt5.safetensors"}},
                "3": {"class_type": "VAELoader", "inputs": {"vae_name": "wan-vae.safetensors"}},
            }
        ),
        encoding="utf-8",
    )
    (root / "manifest.json").write_text(json.dumps({"bindings": []}), encoding="utf-8")
    try:
        load_workflow(config, "video", {})
    except ComfyError as error:
        message = str(error)
        assert "diffusion_models/wan.safetensors" in message
        assert "text_encoders/umt5.safetensors" in message
        assert "vae/wan-vae.safetensors" in message
    else:
        raise AssertionError("video workflow should fail when model files are absent")
