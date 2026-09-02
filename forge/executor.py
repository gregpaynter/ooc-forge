from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import secrets
import sys
from pathlib import Path
from typing import Any
from uuid import uuid4

from forge import __version__
from forge.comfy import ComfyClient, load_workflow, output_references
from forge.config import Config
from forge.db import init_db, utc_now
from forge.storage import ensure_identity, ensure_layout


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def execute(request: dict[str, Any], *, local_job_id: str | None = None) -> dict[str, Any]:
    config = Config.load()
    ensure_layout(config)
    init_db(config)
    identity = ensure_identity(config)
    workflow_id = str(request.get("workflow_id") or "manual-image")
    if request.get("kind") == "commissioning_test":
        request = {
            **request,
            "prompt": request.get("prompt") or "OOC Forge commissioning self-test",
            "negative_prompt": "text, watermark, low quality",
            "width": 512,
            "height": 512,
            "steps": 12,
        }
        workflow_id = str(request.get("workflow_id") or "manual-image")

    if int(request.get("seed", 0) or 0) < 0:
        request = {**request, "seed": secrets.randbits(63)}

    workflow, manifest = load_workflow(config, workflow_id, request)
    client = ComfyClient(config)
    client.health()
    prompt_id = client.queue(workflow)
    history = client.wait(prompt_id, timeout=int(request.get("timeout_seconds") or 3600))
    references = output_references(history)
    if not references:
        raise RuntimeError("ComfyUI completed without a downloadable output")

    job_id = local_job_id or str(uuid4())
    destination_root = config.library_root / "studies" / job_id
    destination_root.mkdir(parents=True, exist_ok=True)
    assets: list[dict[str, Any]] = []
    for index, reference in enumerate(references, start=1):
        source_name = Path(str(reference["filename"])).name
        suffix = Path(source_name).suffix or ".bin"
        destination = destination_root / f"asset-{index:03d}{suffix}"
        client.download(reference, destination)
        relative = destination.relative_to(config.data_root).as_posix()
        assets.append(
            {
                "kind": manifest.get("output_kind", "generated"),
                "relative_path": relative,
                "mime_type": mimetypes.guess_type(destination.name)[0] or "application/octet-stream",
                "size_bytes": destination.stat().st_size,
                "sha256": _sha256(destination),
            }
        )

    provenance = {
        "schema": "ooc.generation-evidence.v1",
        "executor": {
            "agent": "ooc-forge-runtime",
            "agent_version": __version__,
            "forge_id": identity["forge_id"],
            "workflow_id": workflow_id,
            "comfy_prompt_id": prompt_id,
            "completed_at": utc_now(),
        },
        "workflow": {
            "id": workflow_id,
            "manifest_version": manifest.get("version", "1"),
        },
    }
    return {
        "title": str(request.get("title") or "Forge Candidate"),
        "description": str(request.get("description") or "") or None,
        "local_job_id": job_id,
        "assets": assets,
        "media_ref": assets[0]["relative_path"],
        "generation_evidence": provenance,
    }


def main() -> int:
    try:
        payload = json.load(sys.stdin)
        if not isinstance(payload, dict):
            raise ValueError("Forge execution request must be a JSON object")
        result = execute(payload)
        json.dump(result, sys.stdout, sort_keys=True)
        sys.stdout.write("\n")
        return 0
    except Exception as error:
        print(str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
