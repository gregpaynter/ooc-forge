from __future__ import annotations

import json
import re
import subprocess
from typing import Any

from forge.config import Config
from forge.models import REFERENCE_PROMPT_MODEL, prompt_model_path, prompt_model_ready


PROMPT_TEMPLATE_VERSION = "video-director.v2"
MAX_SHOT_SECONDS = 5.0
MAX_OUTPUT_TOKENS = 640
PROMPT_TIMEOUT_SECONDS = 240


def _extract_json(text: str) -> dict[str, Any]:
    cleaned = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start < 0 or end <= start:
        raise RuntimeError("Prompt compiler did not return a JSON object.")
    try:
        value = json.loads(cleaned[start : end + 1])
    except json.JSONDecodeError as error:
        raise RuntimeError(f"Prompt compiler returned invalid JSON: {error}") from error
    if not isinstance(value, dict):
        raise RuntimeError("Prompt compiler response must be a JSON object.")
    return value


def _normalise_shots(value: dict[str, Any], duration: float) -> list[dict[str, Any]]:
    raw = value.get("shots")
    shots: list[dict[str, Any]] = []
    if isinstance(raw, list):
        cursor = 0.0
        for item in raw:
            if not isinstance(item, dict):
                continue
            instruction = str(item.get("instruction") or "").strip()
            if not instruction:
                continue
            try:
                shot_duration = float(item.get("duration") or MAX_SHOT_SECONDS)
            except (TypeError, ValueError):
                shot_duration = MAX_SHOT_SECONDS
            shot_duration = max(1.0, min(MAX_SHOT_SECONDS, shot_duration, duration - cursor))
            if duration - cursor <= 0:
                break
            shots.append(
                {
                    "start": round(cursor, 3),
                    "duration": round(shot_duration, 3),
                    "instruction": instruction,
                }
            )
            cursor += shot_duration
            if cursor >= duration:
                break

    cursor = sum(float(item["duration"]) for item in shots)
    fallback = str(value.get("resolved_video_prompt") or value.get("derived_video_prompt") or "subtle cinematic motion").strip()
    while cursor < duration - 1e-6:
        remaining = duration - cursor
        shot_duration = min(MAX_SHOT_SECONDS, remaining)
        shots.append(
            {
                "start": round(cursor, 3),
                "duration": round(shot_duration, 3),
                "instruction": fallback,
            }
        )
        cursor += shot_duration
    return shots


def compile_video_prompt(
    config: Config,
    *,
    creative_prompt: str,
    user_video_prompt: str,
    duration_seconds: float,
) -> dict[str, Any]:
    if not prompt_model_ready(config):
        raise RuntimeError("The local prompt compiler model/runtime is not installed.")
    duration = max(1.0, float(duration_seconds))
    user_direction = user_video_prompt.strip()
    instruction = f"""
You are the OOC Forge temporal director. Convert a still-image creative prompt into a concise image-to-video direction and shot plan.

Preserve the identity, composition, subject, style and atmosphere of the Seed Work. Add only plausible temporal behavior: camera movement, subject movement, environmental motion, pacing and continuity. Do not invent unrelated subjects, text, logos, scene changes or new locations unless the user explicitly asks for them.

Creative prompt:
{creative_prompt.strip()}

Additional user video direction:
{user_direction if user_direction else '(none — infer restrained motion from the creative prompt)'}

Requested duration: {duration:.2f} seconds.

Return ONLY one compact JSON object with this schema:
{{
  "derived_video_prompt": "concise motion/camera interpretation",
  "resolved_video_prompt": "concise final prompt including optional user direction",
  "camera": "short camera description",
  "motion": "short subject/environment motion description",
  "pacing": "short pacing description",
  "continuity_rules": ["short rule", "short rule"],
  "shots": [
    {{"duration": 5.0, "instruction": "short specific motion instruction"}}
  ]
}}

Rules:
- shots must cover the entire requested duration
- each shot must be at most {MAX_SHOT_SECONDS:.1f} seconds
- use sequential continuity; later shots continue from the prior shot rather than restarting the scene
- the resolved prompt must preserve the creative prompt while incorporating the additional user direction
- keep every field concise so the complete JSON fits comfortably within {MAX_OUTPUT_TOKENS} tokens
- do not think aloud
- no markdown, commentary or code fences
""".strip()

    command = [
        "/usr/local/bin/ooc-llama-cli",
        "-m",
        str(prompt_model_path(config)),
        "-p",
        instruction,
        "-n",
        str(MAX_OUTPUT_TOKENS),
        "--ctx-size",
        "4096",
        "--temp",
        "0.2",
        "--reasoning",
        "off",
        "--no-display-prompt",
        "--simple-io",
    ]
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=PROMPT_TIMEOUT_SECONDS,
            check=True,
        )
    except subprocess.TimeoutExpired as error:
        raise RuntimeError(
            f"Local Qwen video planning exceeded {PROMPT_TIMEOUT_SECONDS} seconds. "
            "The video model was not started; retry the job after checking CPU load."
        ) from error
    except subprocess.CalledProcessError as error:
        detail = (error.stderr or error.stdout or "").strip()
        suffix = f" {detail[-500:]}" if detail else ""
        raise RuntimeError(f"Local Qwen video planning failed.{suffix}") from error

    value = _extract_json(result.stdout)
    derived = str(value.get("derived_video_prompt") or "").strip()
    resolved = str(value.get("resolved_video_prompt") or "").strip()
    if not derived or not resolved:
        raise RuntimeError("Prompt compiler omitted required video prompt fields.")
    shots = _normalise_shots(value, duration)
    return {
        "creative_prompt": creative_prompt.strip(),
        "derived_video_prompt": derived,
        "user_video_prompt": user_direction or None,
        "resolved_video_prompt": resolved,
        "camera": str(value.get("camera") or "").strip() or None,
        "motion": str(value.get("motion") or "").strip() or None,
        "pacing": str(value.get("pacing") or "").strip() or None,
        "continuity_rules": [str(item).strip() for item in value.get("continuity_rules") or [] if str(item).strip()],
        "shots": shots,
        "duration_seconds": duration,
        "compiler": {
            "runtime": "llama.cpp",
            "model": REFERENCE_PROMPT_MODEL["filename"],
            "model_sha256": REFERENCE_PROMPT_MODEL["sha256"],
            "template_version": PROMPT_TEMPLATE_VERSION,
            "reasoning": "off",
            "max_output_tokens": MAX_OUTPUT_TOKENS,
        },
    }
