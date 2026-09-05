from __future__ import annotations

import json
import subprocess

from flask import Blueprint, current_app, flash, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash

from forge.config import Config
from forge.db import create_job, get_creative_session, list_session_jobs, setting, setting_int
from forge.health import capabilities
from forge.models import (
    REFERENCE_AUDIO_MODEL,
    REFERENCE_PROMPT_MODEL,
    REFERENCE_VIDEO_MODEL,
    audio_model_install_running,
    audio_model_install_status,
    audio_model_ready,
    prompt_model_install_running,
    prompt_model_install_status,
    prompt_model_ready,
    request_reference_audio_model_install,
    request_reference_prompt_model_install,
    request_reference_video_model_install,
    video_model_install_running,
    video_model_install_status,
    video_model_ready,
)
from forge.video import VIDEO_PROFILES


bp = Blueprint("video_derivative", __name__)


def _config() -> Config:
    return current_app.config["FORGE_CONFIG"]


def _json(value: object) -> dict:
    if not value:
        return {}
    try:
        parsed = json.loads(str(value))
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


@bp.before_request
def require_admin():
    config = _config()
    if not setting(config, "admin_password_hash"):
        return redirect(url_for("setup"))
    if not session.get("admin"):
        return redirect(url_for("login"))
    return None


def _admin_password_ok(config: Config) -> bool:
    digest = setting(config, "admin_password_hash")
    return bool(digest and check_password_hash(digest, request.form.get("password", "")))


@bp.get("/creative-models")
def creative_models():
    config = _config()
    return render_template(
        "creative_models.html",
        prompt_model=REFERENCE_PROMPT_MODEL,
        prompt_ready=prompt_model_ready(config),
        prompt_status=prompt_model_install_status(config),
        prompt_running=prompt_model_install_running(),
        video_model=REFERENCE_VIDEO_MODEL,
        video_ready=video_model_ready(config),
        video_status=video_model_install_status(config),
        video_running=video_model_install_running(),
        audio_model=REFERENCE_AUDIO_MODEL,
        audio_ready=audio_model_ready(config),
        audio_status=audio_model_install_status(config),
        audio_running=audio_model_install_running(),
        capabilities=capabilities(config),
    )


@bp.post("/creative-models/prompt/install")
def install_prompt_model():
    config = _config()
    if request.form.get("accept_license") != "yes":
        flash("Acknowledge the Qwen/Apache 2.0 model licence before installation.")
        return redirect(url_for("video_derivative.creative_models"))
    if not _admin_password_ok(config):
        flash("Admin password is required to install the prompt compiler model.")
        return redirect(url_for("video_derivative.creative_models"))
    if prompt_model_ready(config):
        flash("The local prompt compiler model is already installed.")
        return redirect(url_for("video_derivative.creative_models"))
    try:
        request_reference_prompt_model_install(config)
        flash("Prompt compiler model installation started.")
    except (RuntimeError, subprocess.SubprocessError) as error:
        flash(f"Could not start prompt model installation: {error}")
    return redirect(url_for("video_derivative.creative_models"))


@bp.post("/creative-models/video/install")
def install_video_model():
    config = _config()
    if request.form.get("accept_license") != "yes":
        flash("Acknowledge the Wan/Comfy model licence before installation.")
        return redirect(url_for("video_derivative.creative_models"))
    if not _admin_password_ok(config):
        flash("Admin password is required to install the video model stack.")
        return redirect(url_for("video_derivative.creative_models"))
    if video_model_ready(config):
        flash("The Wan2.2 video model stack is already installed.")
        return redirect(url_for("video_derivative.creative_models"))
    try:
        request_reference_video_model_install(config)
        flash("Wan2.2 video model installation started. This is a large multi-file download and is resumable.")
    except (RuntimeError, subprocess.SubprocessError) as error:
        flash(f"Could not start video model installation: {error}")
    return redirect(url_for("video_derivative.creative_models"))


@bp.post("/creative-models/audio/install")
def install_audio_model():
    config = _config()
    if request.form.get("accept_license") != "yes":
        flash("Acknowledge the Stable Audio Community model licence before installation.")
        return redirect(url_for("video_derivative.creative_models"))
    if not _admin_password_ok(config):
        flash("Admin password is required to install the audio model stack.")
        return redirect(url_for("video_derivative.creative_models"))
    if audio_model_ready(config):
        flash("The Stable Audio 3 model stack is already installed.")
        return redirect(url_for("video_derivative.creative_models"))
    try:
        request_reference_audio_model_install(config)
        flash("Stable Audio 3 model installation started. The verified multi-file download is resumable.")
    except (RuntimeError, subprocess.SubprocessError) as error:
        flash(f"Could not start audio model installation: {error}")
    return redirect(url_for("video_derivative.creative_models"))


@bp.post("/sessions/<session_id>/video")
def create_video(session_id: str):
    config = _config()
    creative = get_creative_session(config, session_id)
    if not creative:
        return "Creative session not found", 404
    if not creative["seed_work_ref"]:
        flash("Select a Seed Work before creating a Video Experience.")
        return redirect(url_for("creative_session", session_id=session_id))
    if not capabilities(config).get("video"):
        flash("Video capability is not ready. Install the local prompt compiler and Wan2.2 model stack from Creative Models.")
        return redirect(url_for("creative_session", session_id=session_id))
    try:
        duration = float(request.form.get("duration_seconds") or setting_int(config, "default_video_duration_seconds", 30))
    except ValueError:
        flash("Video duration must be a number of seconds.")
        return redirect(url_for("creative_session", session_id=session_id))
    duration = max(1.0, min(600.0, duration))
    user_direction = request.form.get("user_video_prompt", "").strip()
    quality_profile = request.form.get("quality_profile", "production").strip().lower()
    if quality_profile not in VIDEO_PROFILES:
        flash("Select a valid video quality profile.")
        return redirect(url_for("creative_session", session_id=session_id))
    create_job(
        config,
        source="LOCAL",
        job_type="VIDEO_EXPERIENCE",
        request={
            "kind": "video_from_seed",
            "title": str(creative["title"]),
            "source_ref": str(creative["seed_work_ref"]),
            "creative_prompt": str(creative["prompt"]),
            "user_video_prompt": user_direction,
            "duration_seconds": duration,
            "quality_profile": quality_profile,
        },
        creative_session_id=session_id,
        parent_job_id=str(creative["seed_source_job_id"] or "") or None,
        derivative_type="video",
    )
    label = str(VIDEO_PROFILES[quality_profile]["label"])
    flash(f"{label} video queued. Forge will derive the temporal prompt and shot plan locally.")
    return redirect(url_for("creative_session", session_id=session_id))


@bp.post("/sessions/<session_id>/audio")
def create_audio(session_id: str):
    config = _config()
    creative = get_creative_session(config, session_id)
    if not creative:
        return "Creative session not found", 404
    if not creative["seed_work_ref"]:
        flash("Select a Seed Work before creating an Audio Experience.")
        return redirect(url_for("creative_session", session_id=session_id))
    if not capabilities(config).get("audio"):
        flash("Audio capability is not ready. Install Stable Audio 3 from Creative Models.")
        return redirect(url_for("creative_session", session_id=session_id))
    try:
        duration = float(request.form.get("duration_seconds") or setting_int(config, "default_video_duration_seconds", 30))
    except ValueError:
        flash("Audio duration must be a number of seconds.")
        return redirect(url_for("creative_session", session_id=session_id))
    duration = max(1.0, min(600.0, duration))
    user_direction = request.form.get("user_audio_prompt", "").strip()
    attach_video = request.form.get("attach_video") == "yes"

    linked_video_job_id = None
    linked_video_master_ref = None
    linked_video_mobile_ref = None
    linked_video_prompt = None
    if attach_video:
        for row in reversed(list_session_jobs(config, session_id)):
            if str(row["derivative_type"] or "") != "video" or str(row["status"]) != "COMPLETED":
                continue
            result = _json(row["result_json"])
            master_ref = result.get("video_master_ref") or result.get("video_preview_ref") or result.get("media_ref")
            if not master_ref:
                continue
            linked_video_job_id = str(row["id"])
            linked_video_master_ref = str(master_ref)
            linked_video_mobile_ref = str(result.get("video_mobile_ref") or "") or None
            video_prompt = result.get("video_prompt")
            if isinstance(video_prompt, dict):
                linked_video_prompt = str(video_prompt.get("resolved_video_prompt") or "") or None
                try:
                    duration = max(1.0, min(600.0, float(video_prompt.get("duration_seconds") or duration)))
                except (TypeError, ValueError):
                    pass
            break

    create_job(
        config,
        source="LOCAL",
        job_type="AUDIO_EXPERIENCE",
        request={
            "kind": "audio_from_seed",
            "title": str(creative["title"]),
            "source_ref": str(creative["seed_work_ref"]),
            "creative_prompt": str(creative["prompt"]),
            "user_audio_prompt": user_direction,
            "duration_seconds": duration,
            "linked_video_job_id": linked_video_job_id,
            "linked_video_master_ref": linked_video_master_ref,
            "linked_video_mobile_ref": linked_video_mobile_ref,
            "linked_video_prompt": linked_video_prompt,
        },
        creative_session_id=session_id,
        parent_job_id=linked_video_job_id or (str(creative["seed_source_job_id"] or "") or None),
        derivative_type="audio",
    )
    if linked_video_master_ref:
        flash("Audio Experience queued and linked to the latest completed Video Experience.")
    else:
        flash("Standalone Audio Experience queued.")
    return redirect(url_for("creative_session", session_id=session_id))
