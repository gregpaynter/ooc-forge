from __future__ import annotations

import hashlib
import json
import mimetypes
import secrets
import shutil
import struct
import sys
import zlib
from pathlib import Path
from typing import Any
from uuid import uuid4

from forge import __version__
from forge.comfy import ComfyClient, load_workflow, output_references
from forge.config import Config
from forge.db import init_db, utc_now
from forge.storage import ensure_identity, ensure_layout


PRINT_DPI = 300
PRINT_WORKFLOW_ID = "print-upscale"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _png_dimensions(path: Path) -> tuple[int, int]:
    with path.open("rb") as handle:
        header = handle.read(24)
    if len(header) < 24 or header[:8] != b"\x89PNG\r\n\x1a\n" or header[12:16] != b"IHDR":
        raise RuntimeError(f"Printable Work output is not a valid PNG: {path.name}")
    return struct.unpack(">II", header[16:24])


def _tag_png_dpi(path: Path, dpi: int = PRINT_DPI) -> None:
    data = path.read_bytes()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise RuntimeError(f"Cannot add print metadata to non-PNG output: {path.name}")

    pixels_per_meter = round(dpi / 0.0254)
    payload = struct.pack(">IIB", pixels_per_meter, pixels_per_meter, 1)
    chunk_type = b"pHYs"
    crc = zlib.crc32(chunk_type + payload) & 0xFFFFFFFF
    print_chunk = struct.pack(">I", len(payload)) + chunk_type + payload + struct.pack(">I", crc)

    output = bytearray(data[:8])
    offset = 8
    inserted = False
    while offset < len(data):
        if offset + 12 > len(data):
            raise RuntimeError(f"Malformed PNG output: {path.name}")
        length = struct.unpack(">I", data[offset : offset + 4])[0]
        end = offset + 12 + length
        if end > len(data):
            raise RuntimeError(f"Malformed PNG output: {path.name}")
        current_type = data[offset + 4 : offset + 8]
        if current_type == b"pHYs":
            output.extend(print_chunk)
            inserted = True
        else:
            if current_type == b"IDAT" and not inserted:
                output.extend(print_chunk)
                inserted = True
            output.extend(data[offset:end])
        offset = end
    path.write_bytes(output)


def _asset(path: Path, config: Config, *, kind: str, role: str, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    value: dict[str, Any] = {
        "kind": kind,
        "role": role,
        "relative_path": path.relative_to(config.data_root).as_posix(),
        "mime_type": mimetypes.guess_type(path.name)[0] or "application/octet-stream",
        "size_bytes": path.stat().st_size,
        "sha256": _sha256(path),
    }
    if extra:
        value.update(extra)
    return value


def _run_workflow(
    config: Config,
    client: ComfyClient,
    workflow_id: str,
    request: dict[str, Any],
    *,
    timeout: int,
) -> tuple[dict[str, Any], str, list[dict[str, Any]]]:
    workflow, manifest = load_workflow(config, workflow_id, request)
    prompt_id = client.queue(workflow)
    history = client.wait(prompt_id, timeout=timeout)
    references = output_references(history)
    if not references:
        raise RuntimeError(f"ComfyUI completed {workflow_id} without a downloadable output")
    return manifest, prompt_id, references


def _create_print_master(
    config: Config,
    client: ComfyClient,
    *,
    job_id: str,
    study_path: Path,
    request: dict[str, Any],
    timeout: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    suffix = study_path.suffix.lower() or ".png"
    input_name = f"ooc-print-{job_id}{suffix}"
    comfy_input = config.data_root / "comfyui-input" / input_name
    shutil.copy2(study_path, comfy_input)
    try:
        print_request = {
            "input_image": input_name,
            "upscale_model": request.get("upscale_model"),
        }
        manifest, prompt_id, references = _run_workflow(
            config,
            client,
            PRINT_WORKFLOW_ID,
            print_request,
            timeout=timeout,
        )
    finally:
        comfy_input.unlink(missing_ok=True)

    destination_root = config.library_root / "works" / job_id
    destination_root.mkdir(parents=True, exist_ok=True)
    reference = references[0]
    destination = destination_root / "print-master.png"
    client.download(reference, destination)
    _tag_png_dpi(destination, PRINT_DPI)
    width_px, height_px = _png_dimensions(destination)
    print_metadata = {
        "dpi": PRINT_DPI,
        "width_px": width_px,
        "height_px": height_px,
        "width_mm": round((width_px / PRINT_DPI) * 25.4, 1),
        "height_mm": round((height_px / PRINT_DPI) * 25.4, 1),
        "scale": int(manifest.get("scale") or 4),
        "workflow_id": PRINT_WORKFLOW_ID,
        "comfy_prompt_id": prompt_id,
        "source_study": study_path.relative_to(config.data_root).as_posix(),
    }
    return _asset(
        destination,
        config,
        kind="print_work",
        role="print_master",
        extra={"print": print_metadata},
    ), print_metadata


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
            "create_printable_work": False,
        }
        workflow_id = str(request.get("workflow_id") or "manual-image")

    if int(request.get("seed", 0) or 0) < 0:
        request = {**request, "seed": secrets.randbits(63)}

    timeout = int(request.get("timeout_seconds") or 3600)
    client = ComfyClient(config)
    client.health()
    manifest, prompt_id, references = _run_workflow(
        config,
        client,
        workflow_id,
        request,
        timeout=timeout,
    )

    job_id = local_job_id or str(uuid4())
    destination_root = config.library_root / "studies" / job_id
    destination_root.mkdir(parents=True, exist_ok=True)
    assets: list[dict[str, Any]] = []
    study_paths: list[Path] = []
    for index, reference in enumerate(references, start=1):
        source_name = Path(str(reference["filename"])).name
        suffix = Path(source_name).suffix or ".bin"
        destination = destination_root / f"asset-{index:03d}{suffix}"
        client.download(reference, destination)
        study_paths.append(destination)
        assets.append(
            _asset(
                destination,
                config,
                kind=manifest.get("output_kind", "generated"),
                role="study",
            )
        )

    print_metadata = None
    print_asset = None
    if bool(request.get("create_printable_work")):
        if not study_paths or study_paths[0].suffix.lower() != ".png":
            raise RuntimeError("Printable Work promotion requires a PNG Study output")
        print_asset, print_metadata = _create_print_master(
            config,
            client,
            job_id=job_id,
            study_path=study_paths[0],
            request=request,
            timeout=timeout,
        )
        assets.append(print_asset)

    provenance: dict[str, Any] = {
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
    if print_asset and print_metadata:
        provenance["promotion"] = {
            "from": "study",
            "to": "print_work",
            "workflow_id": PRINT_WORKFLOW_ID,
            "comfy_prompt_id": print_metadata["comfy_prompt_id"],
            "source_ref": print_metadata["source_study"],
            "output_ref": print_asset["relative_path"],
            "upscale_model": str(request.get("upscale_model") or "RealESRGAN_x4plus.pth"),
            "scale": print_metadata["scale"],
            "dpi": print_metadata["dpi"],
        }

    result: dict[str, Any] = {
        "title": str(request.get("title") or "Forge Candidate"),
        "description": str(request.get("description") or "") or None,
        "local_job_id": job_id,
        "assets": assets,
        "media_ref": assets[0]["relative_path"],
        "study_ref": assets[0]["relative_path"],
        "generation_evidence": provenance,
    }
    if print_asset and print_metadata:
        result["print_ref"] = print_asset["relative_path"]
        result["print_master"] = print_metadata
    return result


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
