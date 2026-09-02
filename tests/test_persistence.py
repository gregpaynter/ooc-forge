from __future__ import annotations

import json
from pathlib import Path

from forge.config import Config
from forge.db import create_job, get_job, init_db
from forge.storage import ensure_identity, ensure_layout


def test_identity_and_job_survive_reopen(tmp_path: Path):
    config = Config(
        data_root=tmp_path,
        comfy_url="http://127.0.0.1:8188",
        ooc_origin=None,
        poll_interval=5,
        default_checkpoint=None,
    )
    ensure_layout(config)
    init_db(config)
    identity = ensure_identity(config)
    job_id = create_job(
        config,
        source="LOCAL",
        job_type="MANUAL_IMAGE",
        request={"prompt": "test"},
    )

    assert ensure_identity(config)["forge_id"] == identity["forge_id"]
    row = get_job(config, job_id)
    assert row is not None
    assert row["status"] == "QUEUED"
    assert json.loads(row["request_json"])["prompt"] == "test"
