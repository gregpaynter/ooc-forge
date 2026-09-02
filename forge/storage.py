from __future__ import annotations

import json
import os
import secrets
from pathlib import Path
from typing import Any
from uuid import uuid4

from forge.config import Config


DATA_DIRS = (
    "config",
    "identity",
    "database",
    "models",
    "workflows",
    "library/studies",
    "library/works",
    "library/experiences",
    "jobs",
    "imports",
    "exports",
    "provenance",
    "sync",
    "logs",
    "cache",
    "tmp",
    "comfyui-output",
    "comfyui-input",
)


def ensure_layout(config: Config) -> None:
    config.data_root.mkdir(parents=True, exist_ok=True)
    for relative in DATA_DIRS:
        (config.data_root / relative).mkdir(parents=True, exist_ok=True)
    (config.data_root / "config").chmod(0o700)
    ensure_identity(config)
    ensure_secrets(config)


def _write_private_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    with temp.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.chmod(temp, 0o600)
    temp.replace(path)


def ensure_identity(config: Config) -> dict[str, Any]:
    if config.identity_path.exists():
        return read_json(config.identity_path)
    identity = {
        "forge_id": str(uuid4()),
        "name": "OOC Forge",
        "schema": "ooc.forge-identity.v1",
    }
    _write_private_json(config.identity_path, identity)
    return identity


def update_identity(config: Config, **changes: Any) -> dict[str, Any]:
    identity = ensure_identity(config)
    identity.update(changes)
    _write_private_json(config.identity_path, identity)
    return identity


def ensure_secrets(config: Config) -> dict[str, Any]:
    if config.secrets_path.exists():
        return read_json(config.secrets_path)
    value = {"session_secret": secrets.token_urlsafe(48)}
    _write_private_json(config.secrets_path, value)
    return value


def read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise RuntimeError(f"Expected JSON object: {path}")
    return value


def update_secrets(config: Config, **changes: Any) -> dict[str, Any]:
    value = ensure_secrets(config)
    value.update(changes)
    _write_private_json(config.secrets_path, value)
    return value
