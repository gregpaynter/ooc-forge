from __future__ import annotations

import hashlib
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from forge.config import Config


MAX_REFERENCE_IMAGE_BYTES = 20 * 1024 * 1024


@dataclass(frozen=True)
class PreparedReferenceImage:
    data: bytes
    suffix: str
    mime_type: str
    sha256: str


def _detect_image(data: bytes) -> tuple[str, str]:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png", "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return ".jpg", "image/jpeg"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp", "image/webp"
    raise RuntimeError("Reference Image must be a PNG, JPEG or WebP image.")


def prepare_reference_image(upload: Any) -> PreparedReferenceImage | None:
    if upload is None or not str(getattr(upload, "filename", "") or "").strip():
        return None
    data = upload.stream.read(MAX_REFERENCE_IMAGE_BYTES + 1)
    if not data:
        raise RuntimeError("Reference Image is empty.")
    if len(data) > MAX_REFERENCE_IMAGE_BYTES:
        raise RuntimeError("Reference Image must be 20 MB or smaller.")
    suffix, mime_type = _detect_image(data)
    return PreparedReferenceImage(
        data=data,
        suffix=suffix,
        mime_type=mime_type,
        sha256=hashlib.sha256(data).hexdigest(),
    )


def store_reference_image(
    config: Config,
    *,
    session_id: str,
    prepared: PreparedReferenceImage,
) -> dict[str, str]:
    work_root = config.library_root / "works" / session_id
    work_root.mkdir(parents=True, exist_ok=True)
    reference = work_root / f"reference-image{prepared.suffix}"
    temp_reference = work_root / f".reference-image.tmp{prepared.suffix}"
    temp_reference.write_bytes(prepared.data)
    temp_reference.replace(reference)

    comfy_root = config.data_root / "comfyui-input"
    comfy_root.mkdir(parents=True, exist_ok=True)
    input_name = f"ooc-reference-{session_id}{prepared.suffix}"
    comfy_input = comfy_root / input_name
    temp_comfy = comfy_root / f".{input_name}.tmp"
    shutil.copy2(reference, temp_comfy)
    temp_comfy.replace(comfy_input)

    return {
        "reference_image_ref": reference.relative_to(config.data_root).as_posix(),
        "reference_image_sha256": prepared.sha256,
        "reference_image_mime_type": prepared.mime_type,
        "input_image": input_name,
    }


def remove_staged_reference_image(config: Config, session_id: str) -> None:
    comfy_root = config.data_root / "comfyui-input"
    if not comfy_root.exists():
        return
    for path in comfy_root.glob(f"ooc-reference-{session_id}.*"):
        if path.is_file():
            path.unlink(missing_ok=True)
