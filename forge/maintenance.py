from __future__ import annotations

import json
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from forge.config import Config

_GIT_REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,127}$")


def validate_git_ref(value: str) -> str:
    ref = value.strip()
    if (
        not _GIT_REF.fullmatch(ref)
        or ".." in ref
        or "//" in ref
        or "@{" in ref
        or ref.endswith("/")
        or ref.endswith(".lock")
    ):
        raise ValueError("Use a branch, tag, or commit ref containing only letters, numbers, '.', '_', '/', or '-'.")
    return ref


def _maintenance_root(config: Config) -> Path:
    return config.data_root / "maintenance"


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None
    return value if isinstance(value, dict) else None


def installed_source_ref() -> str:
    path = Path("/opt/ooc-forge/.ooc-source-ref")
    try:
        return path.read_text(encoding="utf-8").strip() or "unknown"
    except OSError:
        return "unknown"


def git_update_status(config: Config) -> dict[str, Any]:
    status = _read_json(_maintenance_root(config) / "git-update-status.json")
    if status:
        return status
    return {"state": "IDLE", "message": "No maintenance update has run.", "ref": None, "commit": None}


def request_git_update(config: Config, ref: str) -> None:
    clean_ref = validate_git_ref(ref)
    root = _maintenance_root(config)
    root.mkdir(parents=True, exist_ok=True)
    request_path = root / "git-update-request.json"
    temporary = root / ".git-update-request.json.tmp"
    temporary.write_text(
        json.dumps(
            {
                "ref": clean_ref,
                "requested_at": datetime.now(timezone.utc).isoformat(),
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    temporary.replace(request_path)
    try:
        subprocess.run(
            ["/usr/bin/sudo", "-n", "/usr/bin/systemctl", "start", "ooc-forge-git-update.service"],
            check=True,
            timeout=10,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise RuntimeError("Could not start the maintenance update service.") from error
