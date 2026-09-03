from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from forge.config import Config
from forge.db import (
    all_assets,
    finish_job,
    get_job,
    init_db,
    mark_asset_uploaded,
    receive_remote_job,
    record_assets,
    remote_jobs_to_sync,
)


def config(tmp_path: Path) -> Config:
    return Config(tmp_path, "http://127.0.0.1:8188", None, 5, None)


def test_remote_claim_is_persisted_once_and_survives_reopen(tmp_path: Path):
    value = config(tmp_path)
    (tmp_path / "database").mkdir(parents=True)
    init_db(value)
    first = receive_remote_job(
        value,
        remote_job_id="remote-1",
        attempt_token="attempt-1",
        lease_until="2026-09-03T12:00:00Z",
        request={"prompt": "Melbourne rain"},
    )
    second = receive_remote_job(
        value,
        remote_job_id="remote-1",
        attempt_token="attempt-1",
        lease_until="2026-09-03T12:00:00Z",
        request={"prompt": "Melbourne rain"},
    )
    assert second == first
    assert (
        json.loads(get_job(value, first)["request_json"])["prompt"] == "Melbourne rain"
    )
    init_db(value)
    assert get_job(value, first)["sync_status"] == "CLAIMED"


def test_changed_attempt_token_cannot_replace_durable_claim(tmp_path: Path):
    value = config(tmp_path)
    (tmp_path / "database").mkdir(parents=True)
    init_db(value)
    receive_remote_job(
        value,
        remote_job_id="remote-1",
        attempt_token="attempt-1",
        lease_until="later",
        request={},
    )
    with pytest.raises(RuntimeError, match="different attempt token"):
        receive_remote_job(
            value,
            remote_job_id="remote-1",
            attempt_token="attempt-2",
            lease_until="later",
            request={},
        )


def test_completed_remote_job_retains_all_assets_for_retry(tmp_path: Path):
    value = config(tmp_path)
    (tmp_path / "database").mkdir(parents=True)
    init_db(value)
    job_id = receive_remote_job(
        value,
        remote_job_id="remote-1",
        attempt_token="attempt-1",
        lease_until="later",
        request={},
    )
    assets = [
        {
            "relative_path": "library/studies/a/image.png",
            "kind": "work-image",
            "mime_type": "image/png",
            "size_bytes": 10,
            "sha256": "a" * 64,
        },
        {
            "relative_path": "library/studies/a/audio.wav",
            "kind": "audio",
            "mime_type": "audio/wav",
            "size_bytes": 20,
            "sha256": "b" * 64,
        },
    ]
    record_assets(value, job_id, assets)
    record_assets(value, job_id, assets)
    finish_job(value, job_id, {"title": "Candidate", "assets": assets})
    stored = all_assets(value, job_id)
    assert len(stored) == 2
    mark_asset_uploaded(
        value, asset_id=stored[0]["id"], remote_media_asset_id="media-1"
    )
    init_db(value)
    assert len(all_assets(value, job_id)) == 2
    assert remote_jobs_to_sync(value)[0]["sync_status"] == "READY_TO_UPLOAD"


def test_existing_v1_database_is_migrated_in_place(tmp_path: Path):
    value = config(tmp_path)
    (tmp_path / "database").mkdir(parents=True)
    with sqlite3.connect(value.database_path) as connection:
        connection.executescript(
            """
            CREATE TABLE jobs(id TEXT PRIMARY KEY, source TEXT, job_type TEXT, status TEXT,
              request_json TEXT, result_json TEXT, error TEXT, remote_job_id TEXT,
              created_at TEXT, started_at TEXT, completed_at TEXT);
            CREATE TABLE assets(id TEXT PRIMARY KEY, job_id TEXT, kind TEXT,
              relative_path TEXT, mime_type TEXT, size_bytes INTEGER, sha256 TEXT,
              created_at TEXT);
            """
        )
    init_db(value)
    with sqlite3.connect(value.database_path) as connection:
        job_columns = {row[1] for row in connection.execute("PRAGMA table_info(jobs)")}
        asset_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(assets)")
        }
    assert {
        "attempt_token",
        "lease_until",
        "sync_status",
        "remote_candidate_id",
    } <= job_columns
    assert {
        "client_asset_id",
        "remote_media_asset_id",
        "upload_status",
    } <= asset_columns
