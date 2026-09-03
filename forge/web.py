from __future__ import annotations

import json
from functools import wraps
from typing import Any

import requests
from flask import (
    Flask,
    flash,
    redirect,
    render_template,
    request,
    send_file,
    session,
    url_for,
)
from werkzeug.security import check_password_hash, generate_password_hash

from forge import __version__
from forge.config import Config
from forge.db import (
    create_job,
    get_job,
    init_db,
    list_jobs,
    request_study_submission,
    set_setting,
    setting,
)
from forge.health import capabilities, report
from forge.maintenance import (
    git_update_status,
    installed_source_ref,
    request_git_update,
)
from forge.storage import (
    ensure_identity,
    ensure_layout,
    ensure_secrets,
    update_identity,
    update_secrets,
)


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
                set_setting(
                    config, "admin_password_hash", generate_password_hash(password)
                )
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
            version=__version__,
            ooc=ensure_secrets(config),
        )

    @app.get("/create")
    @app.post("/create")
    @login_required
    def create():
        if request.method == "POST":
            payload: dict[str, Any] = {
                "title": request.form.get("title", "").strip() or "Untitled",
                "prompt": request.form.get("prompt", "").strip(),
                "negative_prompt": request.form.get("negative_prompt", "").strip(),
                "workflow_id": request.form.get("workflow_id", "manual-image"),
                "width": int(request.form.get("width") or 1024),
                "height": int(request.form.get("height") or 1024),
                "steps": int(request.form.get("steps") or 24),
                "seed": int(request.form.get("seed") or -1),
            }
            if not payload["prompt"]:
                flash("Prompt is required.")
            else:
                job_id = create_job(
                    config, source="LOCAL", job_type="MANUAL_IMAGE", request=payload
                )
                return redirect(url_for("job", job_id=job_id))
        return render_template("create.html")

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
        result = json.loads(row["result_json"]) if row["result_json"] else None
        return render_template("job.html", job=row, result=result)

    @app.post("/jobs/<job_id>/submit")
    @login_required
    def submit_study(job_id: str):
        if not ensure_secrets(config).get("machine_token"):
            flash("Pair this Forge with OOC before submitting a Study.")
            return redirect(url_for("job", job_id=job_id))
        try:
            request_study_submission(config, job_id)
            flash("Study queued for governed OOC Candidate submission.")
        except (LookupError, ValueError) as error:
            flash(str(error))
        return redirect(url_for("job", job_id=job_id))

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
                        f"{origin}/api/forge/pairing/start",
                        json={
                            "forge_id": identity["forge_id"],
                            "name": identity["name"],
                            "runtime_version": __version__,
                            "hardware": {
                                "gpu": health_value["gpu"],
                                "storage": health_value["storage"],
                            },
                            "capabilities": capabilities(config),
                            "health": health_value,
                        },
                        timeout=30,
                    )
                    response.raise_for_status()
                    value = response.json()
                    update_secrets(
                        config,
                        ooc_origin=origin,
                        pairing_id=value["pairing_id"],
                        pairing_code=value["pairing_code"],
                        pairing_poll_secret=value["poll_secret"],
                        pairing_status="PENDING",
                    )
                    return redirect(url_for("ooc_settings"))
                except Exception as error:
                    flash(f"Could not start pairing: {error}")
        return render_template(
            "ooc.html",
            identity=ensure_identity(config),
            ooc=ensure_secrets(config),
        )

    @app.get("/system")
    @login_required
    def system():
        return render_template(
            "system.html",
            identity=ensure_identity(config),
            health=report(config),
            capabilities=capabilities(config),
            version=__version__,
            source_ref=installed_source_ref(),
            git_update=git_update_status(config),
        )

    @app.post("/system/maintenance/git-update")
    @login_required
    def maintenance_git_update():
        if request.form.get("confirm") != "yes":
            flash("Confirm that this is a Developer/Maintenance update.")
            return redirect(url_for("system"))
        digest = setting(config, "admin_password_hash")
        if not digest or not check_password_hash(
            digest, request.form.get("password", "")
        ):
            flash("Admin password is required to start a Git maintenance update.")
            return redirect(url_for("system"))
        try:
            git_ref = request.form.get("git_ref", "main")
            request_git_update(config, git_ref)
            flash(f"Developer/Maintenance Git update requested for {git_ref.strip()}.")
        except (ValueError, RuntimeError) as error:
            flash(str(error))
        return redirect(url_for("system"))

    return app
