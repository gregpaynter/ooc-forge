from __future__ import annotations

import json
import re
import subprocess
from typing import Any

from forge.config import Config
from forge.models import REFERENCE_PROMPT_MODEL, prompt_model_path, prompt_model_ready


PROMPT_TEMPLATE_VERSION = "video-director.v4"
MAX_SHOT_SECONDS = 5.0
MAX_OUTPUT_TOKENS = 384
PROMPT_TIMEOUT_SECONDS = 120
SEED_AESTHETIC_RULE = (
    "Preserve the Seed Work's medium, palette, texture, line quality, rendering style, "
    "compositional language, visual density and atmosphere. Animate it; never restyle it."
)


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
            if duration - cursor <= 0:
                break
            shot_duration = max(1.0, min(MAX_SHOT_SECONDS, shot_duration, duration - cursor))
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
    fallback = str(
        value.get("resolved_video_prompt")
        or value.get("derived_video_prompt")
        or "subtle cinematic motion"
    ).strip()
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


def _compiler_metadata(*, mode: str, fallback_reason: str | None = None) -> dict[str, Any]:
    value: dict[str, Any] = {
        "runtime": "llama.cpp",
        "model": REFERENCE_PROMPT_MODEL["filename"],
        "model_sha256": REFERENCE_PROMPT_MODEL["sha256"],
        "template_version": PROMPT_TEMPLATE_VERSION,
        "reasoning": "off",
        "max_output_tokens": MAX_OUTPUT_TOKENS,
        "timeout_seconds": PROMPT_TIMEOUT_SECONDS,
        "mode": mode,
        "seed_aesthetic_locked": True,
    }
    if fallback_reason:
        value["fallback_reason"] = fallback_reason[:500]
    return value


def _fallback_plan(
    *,
    creative_prompt: str,
    user_video_prompt: str,
    duration: float,
    reason: str,
) -> dict[str, Any]:
    creative = creative_prompt.strip()
    user_direction = user_video_prompt.strip()
    derived = (
        user_direction
        if user_direction
        else "Add restrained camera, subject and environmental motion while preserving the Seed Work."
    )
    resolved = (
        f"{creative}\nTemporal direction: {derived}\n"
        f"Aesthetic constraint: {SEED_AESTHETIC_RULE}"
    ).strip()
    raw_shots: list[dict[str, Any]] = []
    cursor = 0.0
    index = 0
    while cursor < duration - 1e-6:
        shot_duration = min(MAX_SHOT_SECONDS, duration - cursor)
        continuity = (
            "Begin from the exact Seed Work"
            if index == 0
            else "Continue seamlessly from the final frame of the previous shot"
        )
        raw_shots.append(
            {
                "duration": shot_duration,
                "instruction": (
                    f"{continuity}; {derived}. {SEED_AESTHETIC_RULE} "
                    "Preserve subject identity and composition."
                ),
            }
        )
        cursor += shot_duration
        index += 1
    value = {
        "resolved_video_prompt": resolved,
        "derived_video_prompt": derived,
        "shots": raw_shots,
    }
    return {
        "creative_prompt": creative,
        "derived_video_prompt": derived,
        "user_video_prompt": user_direction or None,
        "resolved_video_prompt": resolved,
        "camera": "Restrained continuity-preserving camera movement.",
        "motion": derived,
        "pacing": "Continuous and measured.",
        "aesthetic_rule": SEED_AESTHETIC_RULE,
        "continuity_rules": [
            "Continue from the previous shot's final frame.",
            "Preserve subject identity and composition.",
            SEED_AESTHETIC_RULE,
        ],
        "shots": _normalise_shots(value, duration),
        "duration_seconds": duration,
        "compiler": _compiler_metadata(mode="deterministic_fallback", fallback_reason=reason),
    }


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
You are the OOC Forge temporal director. Convert a still-image creative prompt into concise motion direction and a shot plan for the exact Seed Work supplied to the video model.

The Seed Work is the visual authority. Its aesthetic is immutable for this derivative. {SEED_AESTHETIC_RULE}

Preserve subject identity and the visual world of the Seed Work. Add only temporal behaviour: camera movement, subject movement, environmental motion, pacing and continuity. A user may request a transformation or event, but that event must occur inside the Seed Work's existing aesthetic. Do not invent unrelated subjects, text, logos, scene changes or new locations unless explicitly requested. Never convert the Work into a different photographic, painterly, illustrative, 3D, cinematic or graphic style.

Creative prompt:
{creative_prompt.strip()}

Additional user video direction:
{user_direction if user_direction else '(none — infer restrained motion from the creative prompt)'}

Requested duration: {duration:.2f} seconds.

Return ONLY one compact JSON object with this schema:
{{
  "derived_video_prompt": "concise motion/camera interpretation",
  "resolved_video_prompt": "concise final prompt preserving Seed aesthetic and optional user direction",
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
- every shot must preserve the Seed Work aesthetic; motion is allowed, restyling is not
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
        "3072",
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
    except subprocess.TimeoutExpired:
        return _fallback_plan(
            creative_prompt=creative_prompt,
            user_video_prompt=user_direction,
            duration=duration,
            reason=f"Local Qwen planning exceeded {PROMPT_TIMEOUT_SECONDS} seconds.",
        )
    except subprocess.CalledProcessError as error:
        detail = (error.stderr or error.stdout or "").strip()
        return _fallback_plan(
            creative_prompt=creative_prompt,
            user_video_prompt=user_direction,
            duration=duration,
            reason=(f"Local Qwen planning failed: {detail[-400:]}" if detail else "Local Qwen planning failed."),
        )

    try:
        value = _extract_json(result.stdout)
        derived = str(value.get("derived_video_prompt") or "").strip()
        resolved = str(value.get("resolved_video_prompt") or "").strip()
        if not derived or not resolved:
            raise RuntimeError("Prompt compiler omitted required video prompt fields.")
    except RuntimeError as error:
        return _fallback_plan(
            creative_prompt=creative_prompt,
            user_video_prompt=user_direction,
            duration=duration,
            reason=str(error),
        )

    shots = _normalise_shots(value, duration)
    resolved_locked = f"{resolved}\nAesthetic constraint: {SEED_AESTHETIC_RULE}".strip()
    return {
        "creative_prompt": creative_prompt.strip(),
        "derived_video_prompt": derived,
        "user_video_prompt": user_direction or None,
        "resolved_video_prompt": resolved_locked,
        "camera": str(value.get("camera") or "").strip() or None,
        "motion": str(value.get("motion") or "").strip() or None,
        "pacing": str(value.get("pacing") or "").strip() or None,
        "aesthetic_rule": SEED_AESTHETIC_RULE,
        "continuity_rules": [
            str(item).strip()
            for item in value.get("continuity_rules") or []
            if str(item).strip()
        ] + [SEED_AESTHETIC_RULE],
        "shots": shots,
        "duration_seconds": duration,
        "compiler": _compiler_metadata(mode="local_llm"),
    }
