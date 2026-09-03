from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any, Iterator
from uuid import uuid4

from forge.config import Config

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    job_type TEXT NOT NULL,
    status TEXT NOT NULL,
    request_json TEXT NOT NULL,
    result_json TEXT,
    error TEXT,
    remote_job_id TEXT,
    attempt_token TEXT,
    lease_until TEXT,
    sync_status TEXT,
    remote_candidate_id TEXT,
    created_at TEXT NOT NULL,
    started_at TEXT,
    completed_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_jobs_status_created ON jobs(status, created_at);
CREATE UNIQUE INDEX IF NOT EXISTS uq_jobs_remote_job ON jobs(remote_job_id) WHERE remote_job_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS assets (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    relative_path TEXT NOT NULL,
    mime_type TEXT,
    size_bytes INTEGER NOT NULL,
    sha256 TEXT NOT NULL,
    client_asset_id TEXT,
    remote_media_asset_id TEXT,
    upload_status TEXT NOT NULL DEFAULT 'PENDING',
    created_at TEXT NOT NULL,
    FOREIGN KEY(job_id) REFERENCES jobs(id)
);
CREATE INDEX IF NOT EXISTS ix_assets_job ON assets(job_id, created_at);
CREATE UNIQUE INDEX IF NOT EXISTS uq_assets_client_id ON assets(client_asset_id) WHERE client_asset_id IS NOT NULL;
"""

_MIGRATIONS = {
    "jobs": {
        "attempt_token": "TEXT",
        "lease_until": "TEXT",
        "sync_status": "TEXT",
        "remote_candidate_id": "TEXT",
    },
    "assets": {
        "client_asset_id": "TEXT",
        "remote_media_asset_id": "TEXT",
        "upload_status": "TEXT NOT NULL DEFAULT 'PENDING'",
    },
}


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def connect(config: Config) -> sqlite3.Connection:
    connection = sqlite3.connect(config.database_path, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    return connection


def init_db(config: Config) -> None:
    with connect(config) as connection:
        # Existing appliances predate the durable remote inbox. Add columns first,
        # then create indexes from the current schema script.
        for table, columns in _MIGRATIONS.items():
            existing = {
                str(row["name"])
                for row in connection.execute(f"PRAGMA table_info({table})").fetchall()
            }
            if existing:
                for name, definition in columns.items():
                    if name not in existing:
                        connection.execute(
                            f"ALTER TABLE {table} ADD COLUMN {name} {definition}"
                        )
        connection.executescript(SCHEMA)


@contextmanager
def transaction(config: Config) -> Iterator[sqlite3.Connection]:
    connection = connect(config)
    try:
        connection.execute("BEGIN IMMEDIATE")
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def setting(config: Config, key: str) -> str | None:
    with connect(config) as connection:
        row = connection.execute(
            "SELECT value FROM settings WHERE key=?", (key,)
        ).fetchone()
    return str(row["value"]) if row else None


def set_setting(config: Config, key: str, value: str) -> None:
    with transaction(config) as connection:
        connection.execute(
            "INSERT INTO settings(key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )


def create_job(
    config: Config,
    *,
    source: str,
    job_type: str,
    request: dict[str, Any],
    remote_job_id: str | None = None,
    attempt_token: str | None = None,
    lease_until: str | None = None,
) -> str:
    job_id = str(uuid4())
    with transaction(config) as connection:
        connection.execute(
            """
            INSERT INTO jobs(
                id, source, job_type, status, request_json, remote_job_id,
                attempt_token, lease_until, sync_status, created_at
            ) VALUES (?, ?, ?, 'QUEUED', ?, ?, ?, ?, ?, ?)
            """,
            (
                job_id,
                source,
                job_type,
                json.dumps(request, sort_keys=True),
                remote_job_id,
                attempt_token,
                lease_until,
                "CLAIMED" if remote_job_id else None,
                utc_now(),
            ),
        )
    return job_id


def receive_remote_job(
    config: Config,
    *,
    remote_job_id: str,
    attempt_token: str,
    lease_until: str,
    request: dict[str, Any],
) -> str:
    """Durably accept a remote claim before any execution begins."""
    with transaction(config) as connection:
        existing = connection.execute(
            "SELECT id, attempt_token FROM jobs WHERE remote_job_id=?", (remote_job_id,)
        ).fetchone()
        if existing:
            if str(existing["attempt_token"] or "") != attempt_token:
                raise RuntimeError(
                    "Remote job was reclaimed with a different attempt token"
                )
            return str(existing["id"])
        job_id = str(uuid4())
        connection.execute(
            """
            INSERT INTO jobs(
                id, source, job_type, status, request_json, remote_job_id,
                attempt_token, lease_until, sync_status, created_at
            ) VALUES (?, 'OOC', 'PRODUCTION', 'QUEUED', ?, ?, ?, ?, 'CLAIMED', ?)
            """,
            (
                job_id,
                json.dumps(request, sort_keys=True),
                remote_job_id,
                attempt_token,
                lease_until,
                utc_now(),
            ),
        )
        return job_id


def list_jobs(config: Config, *, limit: int = 100) -> list[sqlite3.Row]:
    with connect(config) as connection:
        return list(
            connection.execute(
                "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        )


def get_job(config: Config, job_id: str) -> sqlite3.Row | None:
    with connect(config) as connection:
        return connection.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()


def claim_local_job(config: Config) -> sqlite3.Row | None:
    with transaction(config) as connection:
        row = connection.execute(
            "SELECT * FROM jobs WHERE status='QUEUED' ORDER BY created_at LIMIT 1"
        ).fetchone()
        if not row:
            return None
        connection.execute(
            "UPDATE jobs SET status='RUNNING', started_at=? WHERE id=? AND status='QUEUED'",
            (utc_now(), row["id"]),
        )
        return connection.execute(
            "SELECT * FROM jobs WHERE id=?", (row["id"],)
        ).fetchone()


def finish_job(config: Config, job_id: str, result: dict[str, Any]) -> None:
    with transaction(config) as connection:
        connection.execute(
            """
            UPDATE jobs SET status='COMPLETED', result_json=?,
                sync_status=CASE WHEN source='OOC' THEN 'READY_TO_UPLOAD' ELSE sync_status END,
                completed_at=?
            WHERE id=?
            """,
            (json.dumps(result, sort_keys=True), utc_now(), job_id),
        )


def fail_job(config: Config, job_id: str, error: str) -> None:
    with transaction(config) as connection:
        connection.execute(
            """UPDATE jobs SET status='FAILED', error=?,
               sync_status=CASE WHEN source='OOC' THEN 'READY_TO_REPORT_FAILURE' ELSE sync_status END,
               completed_at=? WHERE id=?""",
            (error[:4000], utc_now(), job_id),
        )


def remote_jobs_to_sync(config: Config) -> list[sqlite3.Row]:
    with connect(config) as connection:
        return list(
            connection.execute(
                """SELECT * FROM jobs
                   WHERE source='OOC' AND sync_status IN
                     ('CLAIMED','READY_TO_UPLOAD','READY_TO_COMPLETE','READY_TO_REPORT_FAILURE')
                   ORDER BY created_at"""
            ).fetchall()
        )


def update_remote_lease(config: Config, job_id: str, lease_until: str) -> None:
    with transaction(config) as connection:
        connection.execute(
            "UPDATE jobs SET lease_until=? WHERE id=?", (lease_until, job_id)
        )


def record_assets(config: Config, job_id: str, assets: list[dict[str, Any]]) -> None:
    with transaction(config) as connection:
        for asset in assets:
            relative_path = str(asset["relative_path"])
            client_asset_id = f"{job_id}:{asset['sha256']}:{relative_path}"
            connection.execute(
                """
                INSERT OR IGNORE INTO assets(
                    id, job_id, kind, relative_path, mime_type, size_bytes, sha256,
                    client_asset_id, upload_status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'PENDING', ?)
                """,
                (
                    str(uuid4()),
                    job_id,
                    str(asset.get("kind") or "generated"),
                    relative_path,
                    asset.get("mime_type"),
                    int(asset.get("size_bytes") or 0),
                    str(asset["sha256"]),
                    client_asset_id,
                    utc_now(),
                ),
            )


def pending_assets(config: Config, job_id: str) -> list[sqlite3.Row]:
    with connect(config) as connection:
        return list(
            connection.execute(
                "SELECT * FROM assets WHERE job_id=? AND upload_status!='UPLOADED' ORDER BY created_at",
                (job_id,),
            ).fetchall()
        )


def all_assets(config: Config, job_id: str) -> list[sqlite3.Row]:
    with connect(config) as connection:
        return list(
            connection.execute(
                "SELECT * FROM assets WHERE job_id=? ORDER BY created_at", (job_id,)
            ).fetchall()
        )


def mark_asset_uploaded(
    config: Config, *, asset_id: str, remote_media_asset_id: str
) -> None:
    with transaction(config) as connection:
        connection.execute(
            "UPDATE assets SET upload_status='UPLOADED', remote_media_asset_id=? WHERE id=?",
            (remote_media_asset_id, asset_id),
        )


def set_sync_status(
    config: Config, job_id: str, status: str, *, candidate_id: str | None = None
) -> None:
    with transaction(config) as connection:
        connection.execute(
            "UPDATE jobs SET sync_status=?, remote_candidate_id=COALESCE(?, remote_candidate_id) WHERE id=?",
            (status, candidate_id, job_id),
        )


def request_study_submission(config: Config, job_id: str) -> None:
    with transaction(config) as connection:
        row = connection.execute(
            "SELECT source, status FROM jobs WHERE id=?", (job_id,)
        ).fetchone()
        if not row:
            raise LookupError("Job not found")
        if row["source"] != "LOCAL" or row["status"] != "COMPLETED":
            raise ValueError("Only a completed local Study can be submitted to OOC")
        connection.execute(
            "UPDATE jobs SET sync_status='SUBMIT_REQUESTED' WHERE id=?", (job_id,)
        )


def studies_to_submit(config: Config) -> list[sqlite3.Row]:
    with connect(config) as connection:
        return list(
            connection.execute(
                """SELECT * FROM jobs
               WHERE source='LOCAL' AND status='COMPLETED' AND sync_status='SUBMIT_REQUESTED'
               ORDER BY created_at"""
            ).fetchall()
        )
