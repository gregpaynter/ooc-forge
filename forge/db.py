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
    created_at TEXT NOT NULL,
    started_at TEXT,
    completed_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_jobs_status_created ON jobs(status, created_at);

CREATE TABLE IF NOT EXISTS assets (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    relative_path TEXT NOT NULL,
    mime_type TEXT,
    size_bytes INTEGER NOT NULL,
    sha256 TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY(job_id) REFERENCES jobs(id)
);
CREATE INDEX IF NOT EXISTS ix_assets_job ON assets(job_id, created_at);
"""


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def connect(config: Config) -> sqlite3.Connection:
    connection = sqlite3.connect(config.database_path, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    return connection


def init_db(config: Config) -> None:
    with connect(config) as connection:
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
        row = connection.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
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
) -> str:
    job_id = str(uuid4())
    with transaction(config) as connection:
        connection.execute(
            """
            INSERT INTO jobs(id, source, job_type, status, request_json, remote_job_id, created_at)
            VALUES (?, ?, ?, 'QUEUED', ?, ?, ?)
            """,
            (job_id, source, job_type, json.dumps(request, sort_keys=True), remote_job_id, utc_now()),
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
        return connection.execute("SELECT * FROM jobs WHERE id=?", (row["id"],)).fetchone()


def finish_job(config: Config, job_id: str, result: dict[str, Any]) -> None:
    with transaction(config) as connection:
        connection.execute(
            """
            UPDATE jobs SET status='COMPLETED', result_json=?, completed_at=?
            WHERE id=?
            """,
            (json.dumps(result, sort_keys=True), utc_now(), job_id),
        )


def fail_job(config: Config, job_id: str, error: str) -> None:
    with transaction(config) as connection:
        connection.execute(
            "UPDATE jobs SET status='FAILED', error=?, completed_at=? WHERE id=?",
            (error[:4000], utc_now(), job_id),
        )
