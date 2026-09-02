from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Config:
    data_root: Path
    comfy_url: str
    ooc_origin: str | None
    poll_interval: float
    default_checkpoint: str | None

    @classmethod
    def load(cls) -> "Config":
        origin = os.environ.get("OOC_SYSTEM_ORIGIN", "").strip().rstrip("/") or None
        return cls(
            data_root=Path(os.environ.get("FORGE_DATA_ROOT", "/forge-data")),
            comfy_url=os.environ.get("COMFYUI_URL", "http://127.0.0.1:8188").rstrip("/"),
            ooc_origin=origin,
            poll_interval=float(os.environ.get("FORGE_POLL_INTERVAL", "5")),
            default_checkpoint=os.environ.get("FORGE_DEFAULT_CHECKPOINT", "").strip() or None,
        )

    @property
    def database_path(self) -> Path:
        return self.data_root / "database" / "forge.db"

    @property
    def secrets_path(self) -> Path:
        return self.data_root / "config" / "secrets.json"

    @property
    def identity_path(self) -> Path:
        return self.data_root / "identity" / "forge.json"

    @property
    def workflows_root(self) -> Path:
        return self.data_root / "workflows"

    @property
    def library_root(self) -> Path:
        return self.data_root / "library"
