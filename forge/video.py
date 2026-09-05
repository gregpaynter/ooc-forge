from __future__ import annotations

import hashlib
import secrets
import shutil
import subprocess
from pathlib import Path
from typing import Any

from forge import __version__
from forge.comfy import ComfyClient, load_workflow, output_references
from forge.config import Config
from forge.db import update_job_progress, utc_now
from forge.prompt_compiler import compile_video_prompt


VIDEO_WORKFLOW_ID = "video-wan22-ti2v"
VIDEO_NEGATIVE_PROMPT = (
    "oversaturated, overexposed, static, blurry, subtitles, text, watermark, low quality, "
    "distorted anatomy, duplicate subjects, abrupt scene change, identity drift, flicker"
)
VIDEO_PROFILES: dict[str, dict[str, Any]] = {
    "draft": {
        "label": "Draft preview",
        "width": 832,
        "height": 480,
        "fps": 16,
        "steps": 16,
        "master_preset": "veryfast",
        "master_crf": 20,
        "mobile": False,
    },
    "production": {
        "label": "Production 720p",
        "width": 1280,
        "height": 704,
        "fps": 24,
        "steps": 20,
        "master_preset": "slow",
        "master_crf": 14,
        "mobile": True,
    },
}


def video_profile(name: str) -> dict[str, Any]:
    key = str(name or "production").strip().lower()
    if key not in VIDEO_PROFILES:
        raise RuntimeError(f"Unknown video quality profile: {key}")
    return dict(VIDEO_PROFILES[key], id=key)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _asset(path: Path, config: Config, *, role: str, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    value: dict[str, Any] = {
        "kind": "video",
        "role": role,
        "relative_path": path.relative_to(config.data_root).as_posix(),
        "mime_type": "video/mp4",
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
        raise RuntimeError("Seed Work source is missing or outside the Forge library boundary.")
    return source


def _frames_for_duration(seconds: float, fps: int = 24) -> int:
    target = max(17, int(round(seconds * fps)))
    return ((target - 1 + 3) // 4) * 4 + 1


def _run_segment(
    config: Config,
    client: ComfyClient,
    *,
    input_name: str,
    prompt: str,
    width: int,
    height: int,
    frames: int,
    fps: int,
    steps: int,
    seed: int,
    destination: Path,
    timeout: int,
) -> str:
    workflow, _ = load_workflow(
        config,
        VIDEO_WORKFLOW_ID,
        {
            "input_image": input_name,
            "prompt": prompt,
            "negative_prompt": VIDEO_NEGATIVE_PROMPT,
            "width": width,
            "height": height,
            "frames": frames,
            "fps": fps,
            "steps": steps,
            "seed": seed,
        },
    )
    prompt_id = client.queue(workflow)
    history = client.wait(prompt_id, timeout=timeout)
    references = [
        item
        for item in output_references(history)
        if str(item.get("filename") or "").lower().endswith((".mp4", ".webm", ".mkv", ".mov"))
    ]
    if not references:
        raise RuntimeError("Wan video workflow completed without a saved video output.")
    client.download(references[0], destination)
    return prompt_id


def _extract_last_frame(video: Path, destination: Path) -> None:
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-sseof",
            "-0.08",
            "-i",
            str(video),
            "-frames:v",
            "1",
            str(destination),
        ],
        check=True,
        timeout=120,
    )


def _assemble_master(
    segments: list[Path],
    destination: Path,
    duration: float,
    *,
    preset: str,
    crf: int,
) -> None:
    concat = destination.parent / ".segments.txt"
    concat.write_text(
        "".join(f"file '{path.as_posix()}'\n" for path in segments),
        encoding="utf-8",
    )
    try:
        subprocess.run(
            [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(concat),
                "-t",
                f"{duration:.3f}",
                "-an",
                "-c:v",
                "libx264",
                "-preset",
                preset,
                "-crf",
                str(crf),
                "-pix_fmt",
                "yuv420p",
                "-movflags",
                "+faststart",
                str(destination),
            ],
            check=True,
            timeout=1800,
        )
    finally:
        concat.unlink(missing_ok=True)


def _make_mobile(master: Path, destination: Path) -> None:
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(master),
            "-an",
            "-vf",
            "scale=720:-2:force_original_aspect_ratio=decrease",
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            "23",
            "-maxrate",
            "2500k",
            "-bufsize",
            "5000k",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            str(destination),
        ],
        check=True,
        timeout=1800,
    )


def render_video_derivative(
    config: Config,
    request: dict[str, Any],
    *,
    job_id: str,
    forge_id: str,
) -> dict[str, Any]:
    source_ref = str(request.get("source_ref") or "").strip()
    creative_prompt = str(request.get("creative_prompt") or "").strip()
    if not source_ref or not creative_prompt:
        raise RuntimeError("Video derivative requires the Seed Work and original creative prompt.")

    profile = video_profile(str(request.get("quality_profile") or "production"))
    width = int(profile["width"])
    height = int(profile["height"])
    fps = int(profile["fps"])
    steps = int(profile["steps"])
    source = _safe_library_source(config, source_ref)
    duration = max(1.0, min(600.0, float(request.get("duration_seconds") or 30)))
    user_video_prompt = str(request.get("user_video_prompt") or "").strip()

    update_job_progress(
        config,
        job_id,
        stage="PLANNING",
        percent=3,
        message=f"Deriving temporal direction and shot plan for {profile['label']}.",
    )
    prompt_plan = compile_video_prompt(
        config,
        creative_prompt=creative_prompt,
        user_video_prompt=user_video_prompt,
        duration_seconds=duration,
    )

    root = config.library_root / "experiences" / job_id
    segments_root = root / "segments"
    root.mkdir(parents=True, exist_ok=True)
    segments_root.mkdir(parents=True, exist_ok=True)
    client = ComfyClient(config)
    update_job_progress(
        config,
        job_id,
        stage="PREPARING",
        percent=8,
        message=(
            f"Shot plan ready. Preparing Wan2.2 {profile['label']} generation "
            f"at {width}×{height}, {fps} fps, {steps} steps."
        ),
        current=0,
        total=len(prompt_plan["shots"]),
    )
    client.health()
    timeout = int(request.get("timeout_seconds") or 7200)

    current_start = source
    segments: list[Path] = []
    segment_evidence: list[dict[str, Any]] = []
    total_shots = len(prompt_plan["shots"])
    for index, shot in enumerate(prompt_plan["shots"], start=1):
        progress_before = 10 + int(((index - 1) / max(1, total_shots)) * 72)
        update_job_progress(
            config,
            job_id,
            stage="GENERATING",
            percent=progress_before,
            message=f"Generating Wan2.2 {profile['label']} shot {index} of {total_shots}.",
            current=index,
            total=total_shots,
        )
        input_name = f"ooc-video-{job_id}-{index:03d}{current_start.suffix.lower() or '.png'}"
        comfy_input = config.data_root / "comfyui-input" / input_name
        shutil.copy2(current_start, comfy_input)
        segment = segments_root / f"segment-{index:03d}.mp4"
        seed = secrets.randbits(63)
        frames = _frames_for_duration(float(shot["duration"]), fps=fps)
        segment_prompt = (
            f"{prompt_plan['resolved_video_prompt']}\n"
            f"Current shot: {shot['instruction']}\n"
            "Maintain exact subject and visual continuity from the supplied starting frame."
        )
        try:
            prompt_id = _run_segment(
                config,
                client,
                input_name=input_name,
                prompt=segment_prompt,
                width=width,
                height=height,
                frames=frames,
                fps=fps,
                steps=steps,
                seed=seed,
                destination=segment,
                timeout=timeout,
            )
        finally:
            comfy_input.unlink(missing_ok=True)
        segments.append(segment)
        next_frame = segments_root / f"continuity-{index:03d}.png"
        _extract_last_frame(segment, next_frame)
        current_start = next_frame
        segment_evidence.append(
            {
                "index": index,
                "start": shot["start"],
                "planned_duration": shot["duration"],
                "frames": frames,
                "fps": fps,
                "steps": steps,
                "seed": seed,
                "instruction": shot["instruction"],
                "comfy_prompt_id": prompt_id,
                "segment_ref": segment.relative_to(config.data_root).as_posix(),
            }
        )
        progress_after = 10 + int((index / max(1, total_shots)) * 72)
        update_job_progress(
            config,
            job_id,
            stage="GENERATING",
            percent=progress_after,
            message=f"Completed shot {index} of {total_shots}; continuity frame prepared.",
            current=index,
            total=total_shots,
        )

    is_production = profile["id"] == "production"
    master = root / ("video-master.mp4" if is_production else "video-preview.mp4")
    update_job_progress(
        config,
        job_id,
        stage="ASSEMBLING",
        percent=86,
        message=(
            f"Assembling {total_shots} generated shots into the "
            f"{'high-quality master' if is_production else 'draft preview'}."
        ),
        current=total_shots,
        total=total_shots,
    )
    _assemble_master(
        segments,
        master,
        duration,
        preset=str(profile["master_preset"]),
        crf=int(profile["master_crf"]),
    )

    primary_role = "video_master" if is_production else "video_preview"
    master_asset = _asset(
        master,
        config,
        role=primary_role,
        extra={
            "duration_seconds": duration,
            "width": width,
            "height": height,
            "fps": fps,
            "steps": steps,
            "profile": str(profile["id"]),
        },
    )
    assets = [master_asset]
    mobile_asset: dict[str, Any] | None = None
    if bool(profile["mobile"]):
        mobile = root / "video-mobile.mp4"
        update_job_progress(
            config,
            job_id,
            stage="TRANSCODING",
            percent=94,
            message="Creating the mobile rendition from the high-quality master.",
            current=total_shots,
            total=total_shots,
        )
        _make_mobile(master, mobile)
        mobile_asset = _asset(
            mobile,
            config,
            role="video_mobile",
            extra={
                "duration_seconds": duration,
                "profile": "mobile-h264-720",
                "source_profile": str(profile["id"]),
            },
        )
        assets.append(mobile_asset)

    update_job_progress(
        config,
        job_id,
        stage="FINALISING",
        percent=99,
        message="Finalising video assets and generation provenance.",
        current=total_shots,
        total=total_shots,
    )
    evidence = {
        "schema": "ooc.generation-evidence.v1",
        "executor": {
            "agent": "ooc-forge-runtime",
            "agent_version": __version__,
            "forge_id": forge_id,
            "workflow_id": VIDEO_WORKFLOW_ID,
            "completed_at": utc_now(),
        },
        "lineage": {"from": "seed_work", "source_ref": source_ref, "to": "video_experience"},
        "prompts": prompt_plan,
        "video": {
            "model": "wan2.2_ti2v_5B_fp16.safetensors",
            "workflow_id": VIDEO_WORKFLOW_ID,
            "quality_profile": str(profile["id"]),
            "quality_label": str(profile["label"]),
            "duration_seconds": duration,
            "fps": fps,
            "steps": steps,
            "generation_width": width,
            "generation_height": height,
            "segments": segment_evidence,
            "primary_ref": master_asset["relative_path"],
            "master_ref": master_asset["relative_path"] if is_production else None,
            "mobile_ref": mobile_asset["relative_path"] if mobile_asset else None,
        },
    }
    result: dict[str, Any] = {
        "title": str(request.get("title") or "Video Experience"),
        "description": str(request.get("description") or "") or None,
        "local_job_id": job_id,
        "quality_profile": str(profile["id"]),
        "quality_label": str(profile["label"]),
        "assets": assets,
        "media_ref": master_asset["relative_path"],
        "video_prompt": prompt_plan,
        "generation_evidence": evidence,
    }
    if is_production:
        result["video_master_ref"] = master_asset["relative_path"]
        if mobile_asset:
            result["video_mobile_ref"] = mobile_asset["relative_path"]
    else:
        result["video_preview_ref"] = master_asset["relative_path"]
    return result
