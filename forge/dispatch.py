from __future__ import annotations

from typing import Any
from uuid import uuid4

from forge.audio import render_audio_derivative
from forge.config import Config
from forge.db import init_db
from forge.executor import execute as execute_image
from forge.storage import ensure_identity, ensure_layout
from forge.video import render_video_derivative


def execute(request: dict[str, Any], *, local_job_id: str | None = None) -> dict[str, Any]:
    kind = str(request.get("kind") or "")
    if kind not in {"video_from_seed", "audio_from_seed"}:
        return execute_image(request, local_job_id=local_job_id)

    config = Config.load()
    ensure_layout(config)
    init_db(config)
    identity = ensure_identity(config)
    job_id = local_job_id or str(uuid4())
    if kind == "audio_from_seed":
        return render_audio_derivative(
            config,
            request,
            job_id=job_id,
            forge_id=str(identity["forge_id"]),
        )
    return render_video_derivative(
        config,
        request,
        job_id=job_id,
        forge_id=str(identity["forge_id"]),
    )
