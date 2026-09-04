from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from forge.config import Config
from forge.creative import delete_job_and_files, delete_session_and_files, promote_seed_work
from forge.db import (
    create_creative_session,
    create_job,
    finish_job,
    get_creative_session,
    get_job,
    init_db,
    list_session_jobs,
)
from forge.executor import execute
from forge.storage import ensure_layout


def make_config(tmp_path: Path) -> Config:
    return Config(
        data_root=tmp_path,
        comfy_url="http://127.0.0.1:8188",
        ooc_origin=None,
        poll_interval=5,
        default_checkpoint=None,
    )


def test_init_db_upgrades_legacy_jobs_table_before_session_index(tmp_path):
    config = make_config(tmp_path)
    ensure_layout(config)
    with sqlite3.connect(config.database_path) as connection:
        connection.executescript(
            """
            CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE jobs (
                id TEXT PRIMARY KEY, source TEXT NOT NULL, job_type TEXT NOT NULL,
                status TEXT NOT NULL, request_json TEXT NOT NULL, result_json TEXT,
                error TEXT, remote_job_id TEXT, created_at TEXT NOT NULL,
                started_at TEXT, completed_at TEXT
            );
            CREATE TABLE assets (
                id TEXT PRIMARY KEY, job_id TEXT NOT NULL, kind TEXT NOT NULL,
                relative_path TEXT NOT NULL, mime_type TEXT, size_bytes INTEGER NOT NULL,
                sha256 TEXT NOT NULL, created_at TEXT NOT NULL
            );
            """
        )

    init_db(config)

    with sqlite3.connect(config.database_path) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(jobs)")}
        indexes = {row[1] for row in connection.execute("PRAGMA index_list(jobs)")}
    assert {"creative_session_id", "parent_job_id", "derivative_type"} <= columns
    assert "ix_jobs_session_created" in indexes


def test_candidate_batch_generates_distinct_studies(monkeypatch, tmp_path):
    config = make_config(tmp_path)
    ensure_layout(config)
    monkeypatch.setattr("forge.executor.Config.load", lambda: config)
    monkeypatch.setattr("forge.executor.ComfyClient.health", lambda self: None)

    prompt_ids = iter(["p1", "p2", "p3"])

    def fake_run_workflow(config_value, client, workflow_id, request, timeout):
        prompt_id = next(prompt_ids)
        return ({"version": "2"}, prompt_id, [{"filename": f"{prompt_id}.png"}])

    def fake_download(self, reference, destination):
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"fake-png-" + reference["filename"].encode())

    monkeypatch.setattr("forge.executor._run_workflow", fake_run_workflow)
    monkeypatch.setattr("forge.executor.ComfyClient.download", fake_download)
    seeds = iter([101, 202, 303])
    monkeypatch.setattr("forge.executor.secrets.randbits", lambda bits: next(seeds))

    result = execute(
        {
            "kind": "candidate_batch",
            "title": "Three",
            "prompt": "test prompt",
            "workflow_id": "manual-image",
            "candidate_count": 3,
            "seed": -1,
        },
        local_job_id="job-1",
    )

    assert result["candidate_count"] == 3
    assert [asset["seed"] for asset in result["assets"]] == [101, 202, 303]
    assert [asset["comfy_prompt_id"] for asset in result["assets"]] == ["p1", "p2", "p3"]
    assert len({asset["relative_path"] for asset in result["assets"]}) == 3


def test_seed_promotion_creates_stable_work_and_thumbnail(monkeypatch, tmp_path):
    config = make_config(tmp_path)
    ensure_layout(config)
    init_db(config)
    session_id = create_creative_session(config, title="Seed", prompt="prompt")
    job_id = create_job(
        config,
        source="LOCAL",
        job_type="CANDIDATE_BATCH",
        request={"prompt": "prompt"},
        creative_session_id=session_id,
        derivative_type="study_batch",
    )
    study = config.library_root / "studies" / job_id / "candidate-001.png"
    study.parent.mkdir(parents=True, exist_ok=True)
    study.write_bytes(b"study")
    relative = study.relative_to(config.data_root).as_posix()
    finish_job(
        config,
        job_id,
        {
            "assets": [
                {
                    "role": "study",
                    "kind": "image",
                    "relative_path": relative,
                    "sha256": "x",
                    "size_bytes": 5,
                    "mime_type": "image/png",
                }
            ]
        },
    )

    def fake_run(command, check, timeout):
        Path(command[-1]).write_bytes(b"webp")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr("forge.creative.subprocess.run", fake_run)
    promoted = promote_seed_work(
        config,
        session_id=session_id,
        source_job_id=job_id,
        source_ref=relative,
        thumbnail_max_edge=768,
    )

    session = get_creative_session(config, session_id)
    assert session is not None
    assert session["status"] == "SEED_READY"
    assert promoted["seed_work_ref"].endswith("/seed-work.png")
    assert promoted["thumbnail_ref"].endswith("/thumbnail.webp")
    assert (config.data_root / promoted["seed_work_ref"]).read_bytes() == b"study"
    assert (config.data_root / promoted["thumbnail_ref"]).read_bytes() == b"webp"

    delete_job_and_files(config, job_id)
    assert get_job(config, job_id) is None
    assert (config.data_root / promoted["seed_work_ref"]).exists()
    assert (config.data_root / promoted["thumbnail_ref"]).exists()


def test_session_delete_rejects_running_then_cleans_local_tree(tmp_path):
    config = make_config(tmp_path)
    ensure_layout(config)
    init_db(config)
    session_id = create_creative_session(config, title="Delete", prompt="prompt")
    job_id = create_job(
        config,
        source="LOCAL",
        job_type="CANDIDATE_BATCH",
        request={"prompt": "prompt"},
        creative_session_id=session_id,
        derivative_type="study_batch",
    )
    with sqlite3.connect(config.database_path) as connection:
        connection.execute("UPDATE jobs SET status='RUNNING' WHERE id=?", (job_id,))
        connection.commit()

    with pytest.raises(RuntimeError, match="running jobs"):
        delete_session_and_files(config, session_id)

    with sqlite3.connect(config.database_path) as connection:
        connection.execute("UPDATE jobs SET status='FAILED' WHERE id=?", (job_id,))
        connection.commit()
    work_root = config.library_root / "works" / session_id
    work_root.mkdir(parents=True)
    (work_root / "thumbnail.webp").write_bytes(b"thumb")

    delete_session_and_files(config, session_id)
    assert get_creative_session(config, session_id) is None
    assert list_session_jobs(config, session_id) == []
    assert not work_root.exists()
