from __future__ import annotations

import hashlib
import secrets
import subprocess
from pathlib import Path
from typing import Any

from forge import __version__
from forge.comfy import ComfyClient, load_workflow, output_references
from forge.config import Config
from forge.db import update_job_progress, utc_now
from forge.models import REFERENCE_AUDIO_MODEL


AUDIO_WORKFLOW_ID = "audio-stable-audio3"
AUDIO_NEGATIVE_PROMPT = "clipping, distortion, harsh digital artifacts, low quality"
AUDIO_STEPS = 50
AUDIO_CFG = 7.0


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _asset(
    path: Path,
    config: Config,
    *,
    kind: str,
    role: str,
    mime_type: str,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    value: dict[str, Any] = {
        "kind": kind,
        "role": role,
        "relative_path": path.relative_to(config.data_root).as_posix(),
        "mime_type": mime_type,
        "size_bytes": path.stat().st_size,
        "sha256": _sha256(path),
    }
    if extra:
        value.update(extra)
    return value


def _safe_library_source(config: Config, relative_path: str) -> Path:
    source = (config.data_root / relative_path).resolve()
    root = config.library_root.resolve()
    if root not in source.parents or not source.is_file():
        raise RuntimeError("Linked Forge asset is missing or outside the persistent library boundary.")
    return source


def _resolve_audio_prompt(
    *,
    creative_prompt: str,
    user_audio_prompt: str,
    video_prompt: str,
    duration: float,
) -> dict[str, Any]:
    creative = creative_prompt.strip()
    user_direction = user_audio_prompt.strip()
    temporal = video_prompt.strip()
    derived = (
        "Create an instrumental cinematic soundscape that preserves the mood, texture and atmosphere "
        "of the visual work, with an evolving continuous structure rather than abrupt unrelated changes."
    )
    parts = [
        f"Visual concept: {creative[:800]}",
        derived,
    ]
    if temporal:
        parts.append(f"Temporal context: {temporal[:700]}")
    if user_direction:
        parts.append(f"Additional audio direction: {user_direction[:700]}")
    parts.append(f"Duration: {duration:.1f} seconds.")
    return {
        "creative_prompt": creative,
        "derived_audio_prompt": derived,
        "user_audio_prompt": user_direction or None,
        "video_prompt": temporal or None,
        "resolved_audio_prompt": "\n".join(parts),
        "duration_seconds": duration,
        "compiler": {
            "mode": "deterministic_prompt_resolution",
            "template_version": "audio-director.v1",
        },
    }


def _make_web_audio(master: Path, destination: Path) -> None:
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(master),
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-movflags",
            "+faststart",
            str(destination),
        ],
        check=True,
        timeout=1800,
    )


def _mux_video(video: Path, audio: Path, destination: Path) -> None:
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(video),
            "-i",
            str(audio),
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-b:a",
            "256k",
            "-shortest",
            "-movflags",
            "+faststart",
            str(destination),
        ],
        check=True,
        timeout=1800,
    )


def render_audio_derivative(
    config: Config,
    request: dict[str, Any],
    *,
    job_id: str,
    forge_id: str,
) -> dict[str, Any]:
    source_ref = str(request.get("source_ref") or "").strip()
    creative_prompt = str(request.get("creative_prompt") or "").strip()
    if not source_ref or not creative_prompt:
        raise RuntimeError("Audio derivative requires the Seed Work and original creative prompt.")
    _safe_library_source(config, source_ref)

    duration = max(1.0, min(600.0, float(request.get("duration_seconds") or 30.0)))
    user_audio_prompt = str(request.get("user_audio_prompt") or "").strip()
    video_prompt = str(request.get("linked_video_prompt") or "").strip()
    prompt_plan = _resolve_audio_prompt(
        creative_prompt=creative_prompt,
        user_audio_prompt=user_audio_prompt,
        video_prompt=video_prompt,
        duration=duration,
    )

    update_job_progress(
        config,
        job_id,
        stage="PLANNING",
        percent=5,
        message="Resolving the Seed Work, creative prompt and optional video context into an audio direction.",
    )

    client = ComfyClient(config)
    client.health()
    seed = secrets.randbits(63)
    workflow, _ = load_workflow(
        config,
        AUDIO_WORKFLOW_ID,
        {
            "checkpoint": REFERENCE_AUDIO_MODEL["files"][0]["filename"],
            "prompt": prompt_plan["resolved_audio_prompt"],
            "negative_prompt": AUDIO_NEGATIVE_PROMPT,
            "duration_seconds": duration,
            "seed": seed,
            "steps": AUDIO_STEPS,
            "cfg": AUDIO_CFG,
        },
    )

    update_job_progress(
        config,
        job_id,
        stage="GENERATING",
        percent=15,
        message=f"Generating {duration:.1f} seconds with Stable Audio 3 Medium Base.",
    )
    prompt_id = client.queue(workflow)
    history = client.wait(prompt_id, timeout=int(request.get("timeout_seconds") or 7200))
    references = [
        item
        for item in output_references(history)
        if str(item.get("filename") or "").lower().endswith((".flac", ".wav", ".mp3", ".m4a", ".ogg"))
    ]
    if not references:
        raise RuntimeError("Stable Audio workflow completed without a saved audio output.")

    root = config.library_root / "audio" / job_id
    root.mkdir(parents=True, exist_ok=True)
    master = root / "audio-master.flac"
    client.download(references[0], master)
    master_asset = _asset(
        master,
        config,
        kind="audio",
        role="audio_master",
        mime_type="audio/flac",
        extra={"duration_seconds": duration, "profile": "lossless-flac"},
    )

    update_job_progress(
        config,
        job_id,
        stage="ENCODING",
        percent=72,
        message="Creating the web/mobile AAC rendition from the lossless audio master.",
    )
    web_audio = root / "audio-web.m4a"
    _make_web_audio(master, web_audio)
    web_asset = _asset(
        web_audio,
        config,
        kind="audio",
        role="audio_web",
        mime_type="audio/mp4",
        extra={"duration_seconds": duration, "profile": "aac-192k"},
    )
    assets: list[dict[str, Any]] = [master_asset, web_asset]

    linked_video_job_id = str(request.get("linked_video_job_id") or "").strip() or None
    linked_master_ref = str(request.get("linked_video_master_ref") or "").strip()
    linked_mobile_ref = str(request.get("linked_video_mobile_ref") or "").strip()
    muxed_master_asset: dict[str, Any] | None = None
    muxed_mobile_asset: dict[str, Any] | None = None
    if linked_master_ref:
        update_job_progress(
            config,
            job_id,
            stage="MUXING",
            percent=84,
            message="Muxing the generated soundtrack into the linked Video Experience without re-rendering video.",
        )
        linked_master = _safe_library_source(config, linked_master_ref)
        muxed_master = root / "video-with-audio.mp4"
        _mux_video(linked_master, master, muxed_master)
        muxed_master_asset = _asset(
            muxed_master,
            config,
            kind="video",
            role="video_master_with_audio",
            mime_type="video/mp4",
            extra={"duration_seconds": duration, "source_video_ref": linked_master_ref},
        )
        assets.append(muxed_master_asset)

        if linked_mobile_ref:
            linked_mobile = _safe_library_source(config, linked_mobile_ref)
            muxed_mobile = root / "video-mobile-with-audio.mp4"
            _mux_video(linked_mobile, master, muxed_mobile)
            muxed_mobile_asset = _asset(
                muxed_mobile,
                config,
                kind="video",
                role="video_mobile_with_audio",
                mime_type="video/mp4",
                extra={"duration_seconds": duration, "source_video_ref": linked_mobile_ref},
            )
            assets.append(muxed_mobile_asset)

    update_job_progress(
        config,
        job_id,
        stage="FINALISING",
        percent=98,
        message="Finalising audio assets and generation provenance.",
    )
    evidence = {
        "schema": "ooc.generation-evidence.v1",
        "executor": {
            "agent": "ooc-forge-runtime",
            "agent_version": __version__,
            "forge_id": forge_id,
            "workflow_id": AUDIO_WORKFLOW_ID,
            "comfy_prompt_id": prompt_id,
            "completed_at": utc_now(),
        },
        "lineage": {
            "from": "seed_work",
            "source_ref": source_ref,
            "to": "audio_experience",
            "linked_video_job_id": linked_video_job_id,
        },
        "prompts": prompt_plan,
        "audio": {
            "model": REFERENCE_AUDIO_MODEL["files"][0]["filename"],
            "text_encoder": REFERENCE_AUDIO_MODEL["files"][1]["filename"],
            "workflow_id": AUDIO_WORKFLOW_ID,
            "seed": seed,
            "steps": AUDIO_STEPS,
            "cfg": AUDIO_CFG,
            "duration_seconds": duration,
            "master_ref": master_asset["relative_path"],
            "web_ref": web_asset["relative_path"],
            "video_master_with_audio_ref": muxed_master_asset["relative_path"] if muxed_master_asset else None,
            "video_mobile_with_audio_ref": muxed_mobile_asset["relative_path"] if muxed_mobile_asset else None,
        },
    }
    return {
        "title": str(request.get("title") or "Audio Experience"),
        "description": str(request.get("description") or "") or None,
        "local_job_id": job_id,
        "assets": assets,
        "media_ref": web_asset["relative_path"],
        "audio_master_ref": master_asset["relative_path"],
        "audio_web_ref": web_asset["relative_path"],
        "video_master_with_audio_ref": muxed_master_asset["relative_path"] if muxed_master_asset else None,
        "video_mobile_with_audio_ref": muxed_mobile_asset["relative_path"] if muxed_mobile_asset else None,
        "audio_prompt": prompt_plan,
        "generation_evidence": evidence,
    }
