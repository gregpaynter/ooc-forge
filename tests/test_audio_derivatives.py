from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from forge.audio import _mux_video, _resolve_audio_prompt
from forge.comfy import installed_checkpoints, load_workflow
from forge.config import Config
from forge.health import capabilities
from forge.models import REFERENCE_AUDIO_MODEL


def make_config(tmp_path: Path) -> Config:
    return Config(
        data_root=tmp_path,
        comfy_url="http://127.0.0.1:8188",
        ooc_origin=None,
        poll_interval=5,
        default_checkpoint=None,
    )


def install_audio_workflow(config: Config) -> None:
    root = config.workflows_root / "audio-stable-audio3"
    root.mkdir(parents=True, exist_ok=True)
    source = Path(__file__).resolve().parents[1] / "workflows" / "audio-stable-audio3"
    (root / "workflow.json").write_text((source / "workflow.json").read_text(encoding="utf-8"), encoding="utf-8")
    (root / "manifest.json").write_text((source / "manifest.json").read_text(encoding="utf-8"), encoding="utf-8")


def test_audio_model_pins_are_exact():
    files = {(item["directory"], item["filename"]): item["sha256"] for item in REFERENCE_AUDIO_MODEL["files"]}
    assert files[("checkpoints", "stable_audio_3_medium_base.safetensors")] == "c443fcc4d491475064cd0ff3eb92459b1e5f5060e86d96d016f048e528e24195"
    assert files[("text_encoders", "t5gemma_b_b_ul2.safetensors")] == "1e1eba25be8872edb0d3c6335c6658fd6388e7b14b60da6e454e404cfcd8150e"


def test_audio_prompt_preserves_creative_user_and_video_context():
    value = _resolve_audio_prompt(
        creative_prompt="ball in the forest, woodblock print",
        user_audio_prompt="distant bells and low wind, no vocals",
        video_prompt="the ball slowly becomes a moon face",
        duration=30.0,
    )
    assert value["creative_prompt"] == "ball in the forest, woodblock print"
    assert value["user_audio_prompt"] == "distant bells and low wind, no vocals"
    assert value["video_prompt"] == "the ball slowly becomes a moon face"
    assert "ball in the forest" in value["resolved_audio_prompt"]
    assert "distant bells" in value["resolved_audio_prompt"]
    assert "moon face" in value["resolved_audio_prompt"]
    assert value["duration_seconds"] == 30.0
    assert value["compiler"]["mode"] == "deterministic_prompt_resolution"


def test_audio_checkpoint_is_not_offered_as_image_checkpoint(tmp_path):
    config = make_config(tmp_path)
    checkpoint_root = config.data_root / "models" / "checkpoints"
    checkpoint_root.mkdir(parents=True)
    (checkpoint_root / "stable_audio_3_medium_base.safetensors").write_bytes(b"audio")
    (checkpoint_root / "sd_xl_base_1.0.safetensors").write_bytes(b"image")
    assert installed_checkpoints(config) == ["sd_xl_base_1.0.safetensors"]


def test_audio_workflow_loads_explicit_non_image_checkpoint(tmp_path):
    config = make_config(tmp_path)
    install_audio_workflow(config)
    checkpoint_root = config.data_root / "models" / "checkpoints"
    text_root = config.data_root / "models" / "text_encoders"
    checkpoint_root.mkdir(parents=True)
    text_root.mkdir(parents=True)
    (checkpoint_root / "stable_audio_3_medium_base.safetensors").write_bytes(b"audio")
    (text_root / "t5gemma_b_b_ul2.safetensors").write_bytes(b"text")

    workflow, _ = load_workflow(
        config,
        "audio-stable-audio3",
        {
            "checkpoint": "stable_audio_3_medium_base.safetensors",
            "prompt": "dark ambient forest",
            "duration_seconds": 12.0,
            "seed": 123,
        },
    )
    assert workflow["1"]["inputs"]["ckpt_name"] == "stable_audio_3_medium_base.safetensors"
    assert workflow["2"]["inputs"]["clip_name"] == "t5gemma_b_b_ul2.safetensors"
    assert workflow["2"]["inputs"]["type"] == "stable_audio"
    assert workflow["5"]["inputs"]["seconds"] == 12.0
    assert workflow["6"]["inputs"]["seed"] == 123


def test_audio_capability_requires_audio_workflow_models_and_image_seed_capability(monkeypatch, tmp_path):
    config = make_config(tmp_path)
    image = config.workflows_root / "manual-image"
    image.mkdir(parents=True)
    (image / "workflow.json").write_text("{}", encoding="utf-8")
    install_audio_workflow(config)

    checkpoint_root = config.data_root / "models" / "checkpoints"
    checkpoint_root.mkdir(parents=True)
    (checkpoint_root / "sd_xl_base_1.0.safetensors").write_bytes(b"image")

    monkeypatch.setattr("forge.health.shutil.which", lambda name: "/usr/bin/ffmpeg" if name == "ffmpeg" else None)
    monkeypatch.setattr("forge.health.audio_model_ready", lambda cfg: False)
    assert capabilities(config)["audio"] is False

    monkeypatch.setattr("forge.health.audio_model_ready", lambda cfg: True)
    assert capabilities(config)["audio"] is True


def test_video_audio_mux_copies_video_stream_without_wan_rerender(monkeypatch, tmp_path):
    video = tmp_path / "video.mp4"
    audio = tmp_path / "audio.flac"
    destination = tmp_path / "muxed.mp4"
    calls: list[list[str]] = []

    def fake_run(command, **kwargs):
        calls.append(command)
        destination.write_bytes(b"muxed")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr("forge.audio.subprocess.run", fake_run)
    _mux_video(video, audio, destination)
    command = calls[0]
    assert "-c:v" in command
    assert command[command.index("-c:v") + 1] == "copy"
    assert "-shortest" in command
    assert destination.exists()
