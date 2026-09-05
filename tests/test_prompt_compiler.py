from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

from forge.config import Config
from forge.prompt_compiler import (
    MAX_OUTPUT_TOKENS,
    PROMPT_TEMPLATE_VERSION,
    PROMPT_TIMEOUT_SECONDS,
    compile_video_prompt,
)


def make_config(tmp_path: Path) -> Config:
    return Config(
        data_root=tmp_path,
        comfy_url="http://127.0.0.1:8188",
        ooc_origin=None,
        poll_interval=5,
        default_checkpoint=None,
    )


def test_compile_video_prompt_preserves_creative_and_user_layers(monkeypatch, tmp_path):
    config = make_config(tmp_path)
    captured: list[list[str]] = []
    response = {
        "derived_video_prompt": "slow cinematic movement through rain",
        "resolved_video_prompt": "slow cinematic movement through rain with restless handheld camera",
        "camera": "handheld push forward",
        "motion": "rain and tram move naturally",
        "pacing": "restrained",
        "continuity_rules": ["preserve tram identity", "retain blue light"],
        "shots": [
            {"duration": 5, "instruction": "push toward the tram"},
            {"duration": 5, "instruction": "track alongside the tram"},
        ],
    }

    monkeypatch.setattr("forge.prompt_compiler.prompt_model_ready", lambda cfg: True)
    monkeypatch.setattr(
        "forge.prompt_compiler.prompt_model_path",
        lambda cfg: tmp_path / "models" / "llm" / "qwen.gguf",
    )

    def fake_run(command, **kwargs):
        captured.append(command)
        assert kwargs["timeout"] == PROMPT_TIMEOUT_SECONDS
        return SimpleNamespace(stdout=json.dumps(response), returncode=0)

    monkeypatch.setattr("forge.prompt_compiler.subprocess.run", fake_run)
    result = compile_video_prompt(
        config,
        creative_prompt="a blue tram in rainy Melbourne",
        user_video_prompt="make the camera restless and handheld",
        duration_seconds=10,
    )

    assert result["creative_prompt"] == "a blue tram in rainy Melbourne"
    assert result["user_video_prompt"] == "make the camera restless and handheld"
    assert result["derived_video_prompt"] == "slow cinematic movement through rain"
    assert result["resolved_video_prompt"].endswith("restless handheld camera")
    assert result["duration_seconds"] == 10
    assert result["compiler"]["template_version"] == PROMPT_TEMPLATE_VERSION
    assert result["compiler"]["reasoning"] == "off"
    assert result["compiler"]["max_output_tokens"] == MAX_OUTPUT_TOKENS
    assert result["compiler"]["mode"] == "local_llm"
    assert sum(float(item["duration"]) for item in result["shots"]) == 10
    command = captured[0]
    prompt = command[command.index("-p") + 1]
    assert "a blue tram in rainy Melbourne" in prompt
    assert "make the camera restless and handheld" in prompt
    assert "Requested duration: 10.00 seconds" in prompt
    assert command[command.index("-n") + 1] == str(MAX_OUTPUT_TOKENS)
    assert command[command.index("--reasoning") + 1] == "off"


def test_compile_video_prompt_allows_no_user_direction(monkeypatch, tmp_path):
    config = make_config(tmp_path)
    response = {
        "derived_video_prompt": "subtle drifting mist",
        "resolved_video_prompt": "subtle drifting mist preserving the still composition",
        "shots": [{"duration": 3, "instruction": "mist drifts slowly"}],
    }
    monkeypatch.setattr("forge.prompt_compiler.prompt_model_ready", lambda cfg: True)
    monkeypatch.setattr("forge.prompt_compiler.prompt_model_path", lambda cfg: tmp_path / "q.gguf")
    monkeypatch.setattr(
        "forge.prompt_compiler.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(stdout=json.dumps(response), returncode=0),
    )
    result = compile_video_prompt(
        config,
        creative_prompt="quiet fog over a lake",
        user_video_prompt="",
        duration_seconds=3,
    )
    assert result["user_video_prompt"] is None
    assert result["resolved_video_prompt"].startswith("subtle drifting mist")


def test_compile_video_prompt_timeout_continues_with_fallback(monkeypatch, tmp_path):
    config = make_config(tmp_path)
    monkeypatch.setattr("forge.prompt_compiler.prompt_model_ready", lambda cfg: True)
    monkeypatch.setattr("forge.prompt_compiler.prompt_model_path", lambda cfg: tmp_path / "q.gguf")

    def timeout(command, **kwargs):
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])

    monkeypatch.setattr("forge.prompt_compiler.subprocess.run", timeout)
    result = compile_video_prompt(
        config,
        creative_prompt="quiet forest",
        user_video_prompt="slow push forward",
        duration_seconds=30,
    )
    assert result["compiler"]["mode"] == "deterministic_fallback"
    assert "exceeded" in result["compiler"]["fallback_reason"]
    assert result["user_video_prompt"] == "slow push forward"
    assert len(result["shots"]) == 6
    assert sum(float(item["duration"]) for item in result["shots"]) == 30.0
