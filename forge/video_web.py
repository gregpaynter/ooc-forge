from __future__ import annotations

import subprocess

from flask import Blueprint, current_app, flash, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash

from forge.config import Config
from forge.db import create_job, get_creative_session, setting, setting_int
from forge.health import capabilities
from forge.models import (
    REFERENCE_PROMPT_MODEL,
    REFERENCE_VIDEO_MODEL,
    prompt_model_install_running,
    prompt_model_install_status,
    prompt_model_ready,
    request_reference_prompt_model_install,
    request_reference_video_model_install,
    video_model_install_running,
    video_model_install_status,
    video_model_ready,
)


bp = Blueprint("video_derivative", __name__)


def _config() -> Config:
    return current_app.config["FORGE_CONFIG"]


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
        },
        creative_session_id=session_id,
        parent_job_id=str(creative["seed_source_job_id"] or "") or None,
        derivative_type="video",
    )
    flash("Video Experience queued. Forge will derive the temporal prompt and shot plan locally.")
    return redirect(url_for("creative_session", session_id=session_id))
