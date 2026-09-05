from __future__ import annotations

import json
import subprocess
from functools import wraps
from typing import Any

import requests
from flask import Flask, flash, redirect, render_template, request, send_file, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from forge import __version__
from forge.comfy import installed_checkpoints, installed_upscale_models
from forge.config import Config
from forge.creative import delete_job_and_files, delete_session_and_files, promote_seed_work
from forge.db import (
    create_creative_session,
    create_job,
    get_creative_session,
    get_job,
    init_db,
    list_creative_sessions,
    list_jobs,
    list_session_jobs,
    set_setting,
    setting,
    setting_int,
)
from forge.health import capabilities, report
from forge.maintenance import git_update_status, installed_source_ref, request_git_update
from forge.models import (
    REFERENCE_IMAGE_MODEL,
    REFERENCE_UPSCALE_MODEL,
    model_install_running,
    model_install_status,
    request_reference_model_install,
    request_reference_upscale_model_install,
    upscale_model_install_running,
    upscale_model_install_status,
)
from forge.storage import (
    ensure_identity,
    ensure_layout,
    ensure_secrets,
    update_identity,
    update_secrets,
)


def _json(value: Any) -> dict[str, Any]:
    if not value:
        return {}
    try:
        parsed = json.loads(str(value))
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def create_app() -> Flask:
    config = Config.load()
    ensure_layout(config)
    init_db(config)
    secrets_value = ensure_secrets(config)
    app = Flask(__name__)
    app.secret_key = str(secrets_value["session_secret"])
    app.config["FORGE_CONFIG"] = config

    def login_required(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if not setting(config, "admin_password_hash"):
                return redirect(url_for("setup"))
            if not session.get("admin"):
                return redirect(url_for("login"))
            return view(*args, **kwargs)

        return wrapped

    @app.get("/setup")
    @app.post("/setup")
    def setup():
        if setting(config, "admin_password_hash"):
            return redirect(url_for("dashboard"))
        if request.method == "POST":
            password = request.form.get("password", "")
            confirmation = request.form.get("confirmation", "")
            if len(password) < 10:
                flash("Use a password of at least 10 characters.")
            elif password != confirmation:
                flash("Passwords do not match.")
            else:
                set_setting(config, "admin_password_hash", generate_password_hash(password))
                name = request.form.get("forge_name", "").strip() or "OOC Forge"
                update_identity(config, name=name)
                session["admin"] = True
                return redirect(url_for("dashboard"))
        return render_template("setup.html", identity=ensure_identity(config))

    @app.get("/login")
    @app.post("/login")
    def login():
        if request.method == "POST":
            digest = setting(config, "admin_password_hash")
            if digest and check_password_hash(digest, request.form.get("password", "")):
                session["admin"] = True
                return redirect(url_for("dashboard"))
            flash("Invalid password.")
        return render_template("login.html")

    @app.post("/logout")
    def logout():
        session.clear()
        return redirect(url_for("login"))

    @app.get("/")
    @login_required
    def dashboard():
        return render_template(
            "dashboard.html",
            identity=ensure_identity(config),
            health=report(config),
            capabilities=capabilities(config),
            jobs=list_jobs(config, limit=10),
            creative_sessions=list_creative_sessions(config, limit=6),
            version=__version__,
            ooc=ensure_secrets(config),
        )

    @app.get("/create")
    @app.post("/create")
    @login_required
    def create():
        checkpoints = installed_checkpoints(config)
        selected_checkpoint = (
            request.form.get("checkpoint", "").strip()
            if request.method == "POST"
            else (config.default_checkpoint or (checkpoints[0] if len(checkpoints) == 1 else ""))
        )
        candidate_count = max(1, min(12, setting_int(config, "default_candidate_count", 3)))
        if request.method == "POST":
            title = request.form.get("title", "").strip() or "Untitled"
            prompt = request.form.get("prompt", "").strip()
            negative_prompt = request.form.get("negative_prompt", "").strip()
            if not prompt:
                flash("Prompt is required.")
            elif not checkpoints:
                flash("No image checkpoint is installed. Install the reference model from Models before generating.")
            elif selected_checkpoint not in checkpoints:
                flash("Select an installed image checkpoint.")
            else:
                creative_session_id = create_creative_session(
                    config,
                    title=title,
                    prompt=prompt,
                    negative_prompt=negative_prompt,
                )
                payload: dict[str, Any] = {
                    "kind": "candidate_batch",
                    "title": title,
                    "prompt": prompt,
                    "negative_prompt": negative_prompt,
                    "workflow_id": "manual-image",
                    "checkpoint": selected_checkpoint,
                    "width": int(request.form.get("width") or 1024),
                    "height": int(request.form.get("height") or 1024),
                    "steps": int(request.form.get("steps") or 24),
                    "seed": -1,
                    "candidate_count": candidate_count,
                }
                create_job(
                    config,
                    source="LOCAL",
                    job_type="CANDIDATE_BATCH",
                    request=payload,
                    creative_session_id=creative_session_id,
                    derivative_type="study_batch",
                )
                return redirect(url_for("creative_session", session_id=creative_session_id))
        return render_template(
            "create.html",
            checkpoints=checkpoints,
            selected_checkpoint=selected_checkpoint,
            candidate_count=candidate_count,
        )

    @app.get("/sessions")
    @login_required
    def creative_sessions():
        return render_template("sessions.html", creative_sessions=list_creative_sessions(config))

    @app.get("/sessions/<session_id>")
    @login_required
    def creative_session(session_id: str):
        row = get_creative_session(config, session_id)
        if not row:
            return "Creative session not found", 404
        jobs = list_session_jobs(config, session_id)
        candidates: list[dict[str, Any]] = []
        derivatives: list[dict[str, Any]] = []
        active = False
        for job_row in jobs:
            active = active or str(job_row["status"]) in {"QUEUED", "RUNNING"}
            result = _json(job_row["result_json"])
            entry = {"row": job_row, "result": result}
            if str(job_row["derivative_type"] or "") == "study_batch":
                for asset in result.get("assets") or []:
                    if isinstance(asset, dict) and asset.get("role") == "study":
                        candidates.append({"job": job_row, "asset": asset})
            else:
                derivatives.append(entry)
        return render_template(
            "session.html",
            creative_session=row,
            jobs=jobs,
            candidates=candidates,
            derivatives=derivatives,
            active=active,
            upscale_models=installed_upscale_models(config),
            reference_upscale_model=REFERENCE_UPSCALE_MODEL,
            default_video_duration=setting_int(config, "default_video_duration_seconds", 30),
        )

    @app.post("/sessions/<session_id>/more")
    @login_required
    def creative_session_more(session_id: str):
        row = get_creative_session(config, session_id)
        if not row:
            return "Creative session not found", 404
        jobs = list_session_jobs(config, session_id)
        prior = next(
            (job_row for job_row in reversed(jobs) if str(job_row["derivative_type"] or "") == "study_batch"),
            None,
        )
        if not prior:
            flash("No sampling job is available to extend.")
            return redirect(url_for("creative_session", session_id=session_id))
        payload = _json(prior["request_json"])
        payload.update(
            {
                "kind": "candidate_batch",
                "title": str(row["title"]),
                "prompt": str(row["prompt"]),
                "negative_prompt": str(row["negative_prompt"] or ""),
                "seed": -1,
                "candidate_count": max(1, min(12, setting_int(config, "default_candidate_count", 3))),
            }
        )
        create_job(
            config,
            source="LOCAL",
            job_type="CANDIDATE_BATCH",
            request=payload,
            creative_session_id=session_id,
            parent_job_id=str(prior["id"]),
            derivative_type="study_batch",
        )
        return redirect(url_for("creative_session", session_id=session_id))

    @app.post("/sessions/<session_id>/seed")
    @login_required
    def creative_session_seed(session_id: str):
        try:
            promote_seed_work(
                config,
                session_id=session_id,
                source_job_id=request.form.get("source_job_id", ""),
                source_ref=request.form.get("source_ref", ""),
                thumbnail_max_edge=max(128, min(2048, setting_int(config, "thumbnail_max_edge", 768))),
            )
            flash("Seed Work selected; website thumbnail and inverse etching plate created.")
        except (RuntimeError, subprocess.SubprocessError) as error:
            flash(str(error))
        return redirect(url_for("creative_session", session_id=session_id))

    @app.post("/sessions/<session_id>/print")
    @login_required
    def creative_session_print(session_id: str):
        row = get_creative_session(config, session_id)
        if not row or not row["seed_work_ref"]:
            flash("Select a Seed Work before creating a printable Work.")
            return redirect(url_for("creative_session", session_id=session_id))
        upscale_models = installed_upscale_models(config)
        selected = (
            REFERENCE_UPSCALE_MODEL["filename"]
            if REFERENCE_UPSCALE_MODEL["filename"] in upscale_models
            else (upscale_models[0] if len(upscale_models) == 1 else "")
        )
        if not selected:
            flash("Install a print upscaler from Models before creating a printable Work.")
            return redirect(url_for("creative_session", session_id=session_id))
        create_job(
            config,
            source="LOCAL",
            job_type="PRINT_WORK",
            request={
                "kind": "print_from_seed",
                "title": str(row["title"]),
                "source_ref": str(row["seed_work_ref"]),
                "upscale_model": selected,
            },
            creative_session_id=session_id,
            parent_job_id=str(row["seed_source_job_id"] or "") or None,
            derivative_type="print_work",
        )
        return redirect(url_for("creative_session", session_id=session_id))

    @app.post("/sessions/<session_id>/delete")
    @login_required
    def creative_session_delete(session_id: str):
        try:
            delete_session_and_files(config, session_id)
            flash("Creative session and Forge-owned local assets deleted.")
            return redirect(url_for("creative_sessions"))
        except RuntimeError as error:
            flash(str(error))
            return redirect(url_for("creative_session", session_id=session_id))

    @app.get("/models")
    @login_required
    def models():
        checkpoints = installed_checkpoints(config)
        upscale_models = installed_upscale_models(config)
        return render_template(
            "models.html",
            checkpoints=checkpoints,
            upscale_models=upscale_models,
            reference_model=REFERENCE_IMAGE_MODEL,
            reference_installed=REFERENCE_IMAGE_MODEL["filename"] in checkpoints,
            install_status=model_install_status(config),
            install_running=model_install_running(),
            reference_upscale_model=REFERENCE_UPSCALE_MODEL,
            reference_upscale_installed=REFERENCE_UPSCALE_MODEL["filename"] in upscale_models,
            upscale_install_status=upscale_model_install_status(config),
            upscale_install_running=upscale_model_install_running(),
            capabilities=capabilities(config),
        )

    @app.post("/models/reference/install")
    @login_required
    def install_reference_model():
        if request.form.get("accept_license") != "yes":
            flash("Acknowledge the model licence before installation.")
            return redirect(url_for("models"))
        digest = setting(config, "admin_password_hash")
        if not digest or not check_password_hash(digest, request.form.get("password", "")):
            flash("Admin password is required to install the reference image model.")
            return redirect(url_for("models"))
        if REFERENCE_IMAGE_MODEL["filename"] in installed_checkpoints(config):
            flash("The reference image model is already installed.")
            return redirect(url_for("models"))
        try:
            request_reference_model_install(config)
            flash("Reference image model installation started. Refresh Models to see status.")
        except (RuntimeError, subprocess.SubprocessError) as error:
            flash(f"Could not start model installation: {error}")
        return redirect(url_for("models"))

    @app.post("/models/upscale/install")
    @login_required
    def install_reference_upscale_model():
        if request.form.get("accept_license") != "yes":
            flash("Acknowledge the print upscaler licence before installation.")
            return redirect(url_for("models"))
        digest = setting(config, "admin_password_hash")
        if not digest or not check_password_hash(digest, request.form.get("password", "")):
            flash("Admin password is required to install the print upscaler.")
            return redirect(url_for("models"))
        if REFERENCE_UPSCALE_MODEL["filename"] in installed_upscale_models(config):
            flash("The reference print upscaler is already installed.")
            return redirect(url_for("models"))
        try:
            request_reference_upscale_model_install(config)
            flash("Print upscaler installation started. Refresh Models to see status.")
        except (RuntimeError, subprocess.SubprocessError) as error:
            flash(f"Could not start print upscaler installation: {error}")
        return redirect(url_for("models"))

    @app.get("/jobs")
    @login_required
    def jobs():
        return render_template("jobs.html", jobs=list_jobs(config))

    @app.get("/jobs/<job_id>")
    @login_required
    def job(job_id: str):
        row = get_job(config, job_id)
        if not row:
            return "Job not found", 404
        result = _json(row["result_json"])
        return render_template("job.html", job=row, result=result)

    @app.post("/jobs/<job_id>/delete")
    @login_required
    def delete_job(job_id: str):
        row = get_job(config, job_id)
        if not row:
            return "Job not found", 404
        session_id = str(row["creative_session_id"] or "")
        try:
            delete_job_and_files(config, job_id)
            flash("Job and Forge-owned local job assets deleted.")
        except RuntimeError as error:
            flash(str(error))
        if session_id and get_creative_session(config, session_id):
            return redirect(url_for("creative_session", session_id=session_id))
        return redirect(url_for("jobs"))

    @app.get("/media/<path:relative_path>")
    @login_required
    def media(relative_path: str):
        candidate = (config.data_root / relative_path).resolve()
        root = config.library_root.resolve()
        if root not in candidate.parents:
            return "Not found", 404
        return send_file(candidate)

    @app.get("/settings/ooc")
    @app.post("/settings/ooc")
    @login_required
    def ooc_settings():
        if request.method == "POST":
            origin = request.form.get("origin", "").strip().rstrip("/")
            if not origin.startswith("https://"):
                flash("OOC System origin must use HTTPS.")
            else:
                identity = ensure_identity(config)
                health_value = report(config)
                try:
                    response = requests.post(
                        f"{origin}/api/forge/commission",
                        json={
                            "forge_id": identity["forge_id"],
                            "name": identity.get("name", "OOC Forge"),
                            "version": __version__,
                            "capabilities": capabilities(config),
                            "status": health_value["status"],
                        },
                        timeout=15,
                    )
                    response.raise_for_status()
                    value = response.json()
                    update_secrets(
                        config,
                        ooc_origin=origin,
                        forge_token=value["forge_token"],
                        commissioned=True,
                    )
                    flash("Forge commissioned successfully.")
                except Exception as error:
                    flash(f"Commissioning failed: {error}")
        return render_template(
            "ooc_settings.html",
            identity=ensure_identity(config),
            ooc=ensure_secrets(config),
        )

    @app.get("/system")
    @app.post("/system")
    @login_required
    def system():
        if request.method == "POST":
            action = request.form.get("action")
            if action == "settings":
                set_setting(
                    config,
                    "default_candidate_count",
                    str(max(1, min(12, int(request.form.get("default_candidate_count") or 3)))),
                )
                set_setting(
                    config,
                    "default_video_duration_seconds",
                    str(max(1, min(600, int(request.form.get("default_video_duration_seconds") or 30)))),
                )
                set_setting(
                    config,
                    "thumbnail_max_edge",
                    str(max(128, min(2048, int(request.form.get("thumbnail_max_edge") or 768)))),
                )
                flash("Creative defaults saved.")
            elif action == "git-update":
                digest = setting(config, "admin_password_hash")
                if not digest or not check_password_hash(digest, request.form.get("password", "")):
                    flash("Admin password is required for a Git update.")
                else:
                    try:
                        request_git_update(request.form.get("ref", "").strip() or "iso-usb-boot")
                        flash("Git update started. The Forge will return after the validated update is installed.")
                    except (RuntimeError, subprocess.SubprocessError) as error:
                        flash(f"Could not start Git update: {error}")
        return render_template(
            "system.html",
            health=report(config),
            capabilities=capabilities(config),
            git_status=git_update_status(config),
            installed_source=installed_source_ref(),
            default_candidate_count=setting_int(config, "default_candidate_count", 3),
            default_video_duration=setting_int(config, "default_video_duration_seconds", 30),
            thumbnail_max_edge=setting_int(config, "thumbnail_max_edge", 768),
        )

    return app
