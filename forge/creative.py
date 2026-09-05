from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

from forge.config import Config
from forge.db import (
    delete_creative_session_record,
    delete_job_record,
    get_creative_session,
    get_job,
    list_session_jobs,
    set_session_seed,
)
from forge.reference_image import remove_staged_reference_image


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _safe_library_path(config: Config, relative_path: str) -> Path:
    candidate = (config.data_root / relative_path).resolve()
    root = config.library_root.resolve()
    if root not in candidate.parents:
        raise RuntimeError("Forge asset is outside the persistent library boundary.")
    return candidate


def _safe_remove_tree(config: Config, path: Path) -> None:
    root = config.library_root.resolve()
    candidate = path.resolve()
    if root in candidate.parents and candidate.exists():
        shutil.rmtree(candidate)


def _job_assets(row: Any) -> list[dict[str, Any]]:
    if not row or not row["result_json"]:
        return []
    value = json.loads(str(row["result_json"]))
    assets = value.get("assets") if isinstance(value, dict) else None
    return [dict(item) for item in assets or [] if isinstance(item, dict)]


def promote_seed_work(
    config: Config,
    *,
    session_id: str,
    source_job_id: str,
    source_ref: str,
    thumbnail_max_edge: int = 768,
) -> dict[str, str]:
    session = get_creative_session(config, session_id)
    if not session:
        raise RuntimeError("Creative session not found.")
    job = get_job(config, source_job_id)
    if not job or str(job["creative_session_id"] or "") != session_id:
        raise RuntimeError("Selected Study does not belong to this creative session.")
    if str(job["status"]) != "COMPLETED":
        raise RuntimeError("Only a completed Study can become the Seed Work.")

    assets = _job_assets(job)
    study = next(
        (
            asset
            for asset in assets
            if str(asset.get("relative_path")) == source_ref
            and str(asset.get("role") or "study") == "study"
        ),
        None,
    )
    if not study:
        raise RuntimeError("Selected Study asset was not found in the completed job.")

    source = _safe_library_path(config, source_ref)
    if not source.exists():
        raise RuntimeError("Selected Study file is missing from Forge Data.")

    destination_root = config.library_root / "works" / session_id
    destination_root.mkdir(parents=True, exist_ok=True)
    suffix = source.suffix.lower() or ".png"
    seed_work = destination_root / f"seed-work{suffix}"
    temp_seed = destination_root / f".seed-work.tmp{suffix}"
    shutil.copy2(source, temp_seed)
    temp_seed.replace(seed_work)

    thumbnail = destination_root / "thumbnail.webp"
    temp_thumbnail = destination_root / ".thumbnail.tmp.webp"
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(seed_work),
            "-vf",
            f"scale={thumbnail_max_edge}:{thumbnail_max_edge}:force_original_aspect_ratio=decrease",
            "-frames:v",
            "1",
            "-c:v",
            "libwebp",
            "-quality",
            "82",
            str(temp_thumbnail),
        ],
        check=True,
        timeout=120,
    )
    temp_thumbnail.replace(thumbnail)

    # Plate-preparation artwork is deterministic: horizontally reverse the image
    # so the transferred print reads in the Seed Work orientation, and invert its
    # tonal polarity for the etching plate preparation image. Never mutate the Seed.
    etching_plate = destination_root / "etching-plate-inverse.png"
    temp_etching_plate = destination_root / ".etching-plate-inverse.tmp.png"
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(seed_work),
            "-vf",
            "hflip,negate",
            "-frames:v",
            "1",
            str(temp_etching_plate),
        ],
        check=True,
        timeout=120,
    )
    temp_etching_plate.replace(etching_plate)

    seed_ref = seed_work.relative_to(config.data_root).as_posix()
    thumbnail_ref = thumbnail.relative_to(config.data_root).as_posix()
    etching_plate_ref = etching_plate.relative_to(config.data_root).as_posix()
    seed_sha256 = _sha256(seed_work)
    thumbnail_sha256 = _sha256(thumbnail)
    etching_plate_sha256 = _sha256(etching_plate)
    set_session_seed(
        config,
        session_id,
        source_job_id=source_job_id,
        source_ref=source_ref,
        seed_work_ref=seed_ref,
        seed_work_sha256=seed_sha256,
        thumbnail_ref=thumbnail_ref,
        thumbnail_sha256=thumbnail_sha256,
        etching_plate_ref=etching_plate_ref,
        etching_plate_sha256=etching_plate_sha256,
    )
    return {
        "seed_work_ref": seed_ref,
        "seed_work_sha256": seed_sha256,
        "thumbnail_ref": thumbnail_ref,
        "thumbnail_sha256": thumbnail_sha256,
        "etching_plate_ref": etching_plate_ref,
        "etching_plate_sha256": etching_plate_sha256,
    }


def delete_job_and_files(config: Config, job_id: str) -> None:
    row = get_job(config, job_id)
    if not row:
        return
    if str(row["status"]) == "RUNNING":
        raise RuntimeError("Running jobs must finish or be cancelled before deletion.")
    for asset in _job_assets(row):
        relative = str(asset.get("relative_path") or "")
        if not relative:
            continue
        try:
            path = _safe_library_path(config, relative)
        except RuntimeError:
            continue
        path.unlink(missing_ok=True)
    _safe_remove_tree(config, config.library_root / "experiences" / job_id)
    study_root = config.library_root / "studies" / job_id
    if study_root.exists():
        _safe_remove_tree(config, study_root)
    delete_job_record(config, job_id)


def delete_session_and_files(config: Config, session_id: str) -> None:
    session = get_creative_session(config, session_id)
    if not session:
        return
    jobs = list_session_jobs(config, session_id)
    if any(str(row["status"]) == "RUNNING" for row in jobs):
        raise RuntimeError("A creative session with running jobs cannot be deleted.")

    for row in jobs:
        for asset in _job_assets(row):
            relative = str(asset.get("relative_path") or "")
            if relative:
                try:
                    _safe_library_path(config, relative).unlink(missing_ok=True)
                except RuntimeError:
                    pass

    _safe_remove_tree(config, config.library_root / "works" / session_id)
    remove_staged_reference_image(config, session_id)
    for row in jobs:
        job_id = str(row["id"])
        _safe_remove_tree(config, config.library_root / "studies" / job_id)
        _safe_remove_tree(config, config.library_root / "experiences" / job_id)

    delete_creative_session_record(config, session_id)
