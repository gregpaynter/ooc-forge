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

CREATE TABLE IF NOT EXISTS creative_sessions (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    prompt TEXT NOT NULL,
    negative_prompt TEXT,
    status TEXT NOT NULL DEFAULT 'SAMPLING',
    seed_source_job_id TEXT,
    seed_source_ref TEXT,
    seed_work_ref TEXT,
    thumbnail_ref TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_creative_sessions_updated ON creative_sessions(updated_at);

CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    job_type TEXT NOT NULL,
    status TEXT NOT NULL,
    request_json TEXT NOT NULL,
    result_json TEXT,
    error TEXT,
    remote_job_id TEXT,
    creative_session_id TEXT,
    parent_job_id TEXT,
    derivative_type TEXT,
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

JOB_COLUMNS = {
    "creative_session_id": "TEXT",
    "parent_job_id": "TEXT",
    "derivative_type": "TEXT",
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
        connection.executescript(SCHEMA)
        existing = {
            str(row["name"])
            for row in connection.execute("PRAGMA table_info(jobs)").fetchall()
        }
        for name, sql_type in JOB_COLUMNS.items():
            if name not in existing:
                connection.execute(f"ALTER TABLE jobs ADD COLUMN {name} {sql_type}")
        connection.execute(
            "CREATE INDEX IF NOT EXISTS ix_jobs_session_created "
            "ON jobs(creative_session_id, created_at)"
        )


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


def setting_int(config: Config, key: str, default: int) -> int:
    value = setting(config, key)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def set_setting(config: Config, key: str, value: str) -> None:
    with transaction(config) as connection:
        connection.execute(
            "INSERT INTO settings(key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )


def create_creative_session(
    config: Config,
    *,
    title: str,
    prompt: str,
    negative_prompt: str = "",
) -> str:
    session_id = str(uuid4())
    now = utc_now()
    with transaction(config) as connection:
        connection.execute(
            """
            INSERT INTO creative_sessions(
                id, title, prompt, negative_prompt, status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, 'SAMPLING', ?, ?)
            """,
            (session_id, title, prompt, negative_prompt or None, now, now),
        )
    return session_id


def get_creative_session(config: Config, session_id: str) -> sqlite3.Row | None:
    with connect(config) as connection:
        return connection.execute(
            "SELECT * FROM creative_sessions WHERE id=?", (session_id,)
        ).fetchone()


def list_creative_sessions(config: Config, *, limit: int = 100) -> list[sqlite3.Row]:
    with connect(config) as connection:
        return list(
            connection.execute(
                "SELECT * FROM creative_sessions ORDER BY updated_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        )


def set_session_seed(
    config: Config,
    session_id: str,
    *,
    source_job_id: str,
    source_ref: str,
    seed_work_ref: str,
    thumbnail_ref: str,
) -> None:
    with transaction(config) as connection:
        connection.execute(
            """
            UPDATE creative_sessions
            SET status='SEED_READY', seed_source_job_id=?, seed_source_ref=?,
                seed_work_ref=?, thumbnail_ref=?, updated_at=?
            WHERE id=?
            """,
            (
                source_job_id,
                source_ref,
                seed_work_ref,
                thumbnail_ref,
                utc_now(),
                session_id,
            ),
        )


def touch_creative_session(config: Config, session_id: str) -> None:
    with transaction(config) as connection:
        connection.execute(
            "UPDATE creative_sessions SET updated_at=? WHERE id=?",
            (utc_now(), session_id),
        )


def create_job(
    config: Config,
    *,
    source: str,
    job_type: str,
    request: dict[str, Any],
    remote_job_id: str | None = None,
    creative_session_id: str | None = None,
    parent_job_id: str | None = None,
    derivative_type: str | None = None,
) -> str:
    job_id = str(uuid4())
    with transaction(config) as connection:
        connection.execute(
            """
            INSERT INTO jobs(
                id, source, job_type, status, request_json, remote_job_id,
                creative_session_id, parent_job_id, derivative_type, created_at
            ) VALUES (?, ?, ?, 'QUEUED', ?, ?, ?, ?, ?, ?)
            """,
            (
                job_id,
                source,
                job_type,
                json.dumps(request, sort_keys=True),
                remote_job_id,
                creative_session_id,
                parent_job_id,
                derivative_type,
                utc_now(),
            ),
        )
        if creative_session_id:
            connection.execute(
                "UPDATE creative_sessions SET updated_at=? WHERE id=?",
                (utc_now(), creative_session_id),
            )
    return job_id


def list_jobs(config: Config, *, limit: int = 100) -> list[sqlite3.Row]:
    with connect(config) as connection:
        return list(
            connection.execute(
                "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        )


def list_session_jobs(config: Config, session_id: str) -> list[sqlite3.Row]:
    with connect(config) as connection:
        return list(
            connection.execute(
                "SELECT * FROM jobs WHERE creative_session_id=? ORDER BY created_at",
                (session_id,),
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
        row = connection.execute(
            "SELECT creative_session_id FROM jobs WHERE id=?", (job_id,)
        ).fetchone()
        connection.execute(
            """
            UPDATE jobs SET status='COMPLETED', result_json=?, completed_at=?
            WHERE id=?
            """,
            (json.dumps(result, sort_keys=True), utc_now(), job_id),
        )
        if row and row["creative_session_id"]:
            connection.execute(
                "UPDATE creative_sessions SET updated_at=? WHERE id=?",
                (utc_now(), row["creative_session_id"]),
            )


def fail_job(config: Config, job_id: str, error: str) -> None:
    with transaction(config) as connection:
        row = connection.execute(
            "SELECT creative_session_id FROM jobs WHERE id=?", (job_id,)
        ).fetchone()
        connection.execute(
            "UPDATE jobs SET status='FAILED', error=?, completed_at=? WHERE id=?",
            (error[:4000], utc_now(), job_id),
        )
        if row and row["creative_session_id"]:
            connection.execute(
                "UPDATE creative_sessions SET updated_at=? WHERE id=?",
                (utc_now(), row["creative_session_id"]),
            )


def delete_job_record(config: Config, job_id: str) -> None:
    with transaction(config) as connection:
        row = connection.execute("SELECT status FROM jobs WHERE id=?", (job_id,)).fetchone()
        if not row:
            return
        if str(row["status"]) == "RUNNING":
            raise RuntimeError("Running jobs must finish or be cancelled before deletion.")
        connection.execute("DELETE FROM assets WHERE job_id=?", (job_id,))
        connection.execute("DELETE FROM jobs WHERE id=?", (job_id,))


def delete_creative_session_record(config: Config, session_id: str) -> None:
    with transaction(config) as connection:
        running = connection.execute(
            "SELECT COUNT(*) AS count FROM jobs WHERE creative_session_id=? AND status='RUNNING'",
            (session_id,),
        ).fetchone()
        if running and int(running["count"]) > 0:
            raise RuntimeError("A creative session with running jobs cannot be deleted.")
        connection.execute(
            "DELETE FROM assets WHERE job_id IN (SELECT id FROM jobs WHERE creative_session_id=?)",
            (session_id,),
        )
        connection.execute("DELETE FROM jobs WHERE creative_session_id=?", (session_id,))
        connection.execute("DELETE FROM creative_sessions WHERE id=?", (session_id,))
