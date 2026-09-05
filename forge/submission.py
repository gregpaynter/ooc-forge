from __future__ import annotations

import hashlib
import json
import mimetypes
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import requests

from forge.config import Config
from forge.db import get_creative_session, list_session_jobs, transaction
from forge.storage import ensure_identity, ensure_secrets


SUBMISSION_SCHEMA = "ooc.studio-submission.v1"
SUBMISSION_ENDPOINT = "/api/studio/submissions"
WORK_IMAGE_ROLES = {"thumbnail", "web_image", "work_image"}
EXPERIENCE_ROLE = "video_mobile_with_audio"


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _json(value: object) -> dict[str, Any]:
    if not value:
        return {}
    try:
        parsed = json.loads(str(value))
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _safe_path(config: Config, relative_path: str) -> Path:
    candidate = (config.data_root / relative_path).resolve()
    root = config.library_root.resolve()
    if root not in candidate.parents:
        raise RuntimeError("Forge artifact is outside the persistent library boundary.")
    return candidate


def _state_path(config: Config, session_id: str) -> Path:
    return config.library_root / "works" / session_id / "ooc-submission-state.json"


def load_submission_state(config: Config, session_id: str) -> dict[str, Any]:
    path = _state_path(config, session_id)
    if not path.is_file():
        return {
            "work_image_ref": None,
            "experience_ref": None,
            "proposed_artist_id": None,
            "proposed_artist_name": None,
            "last_submission_id": None,
            "last_presented_at": None,
            "last_presented_manifest_sha256": None,
        }
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _save_submission_state(config: Config, session_id: str, value: dict[str, Any]) -> None:
    path = _state_path(config, session_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temp.replace(path)


def _asset_metadata(config: Config, relative_path: str, *, role: str, kind: str | None = None) -> dict[str, Any]:
    path = _safe_path(config, relative_path)
    if not path.is_file():
        raise RuntimeError(f"Forge artifact is missing: {relative_path}")
    mime_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    inferred_kind = kind or ("image" if mime_type.startswith("image/") else "video" if mime_type.startswith("video/") else "file")
    return {
        "role": role,
        "kind": inferred_kind,
        "relative_path": relative_path,
        "filename": path.name,
        "mime_type": mime_type,
        "size_bytes": path.stat().st_size,
        "sha256": _sha256(path),
    }


def session_assets(config: Config, session_id: str) -> list[dict[str, Any]]:
    session = get_creative_session(config, session_id)
    if not session:
        raise RuntimeError("Creative session not found.")

    assets: dict[str, dict[str, Any]] = {}

    specials = (
        ("seed_work_ref", "seed_work", "image"),
        ("thumbnail_ref", "thumbnail", "image"),
        ("etching_plate_ref", "print_plate", "image"),
    )
    for column, role, kind in specials:
        ref = str(session[column] or "").strip()
        if ref:
            try:
                assets[ref] = _asset_metadata(config, ref, role=role, kind=kind)
            except RuntimeError:
                pass

    for row in list_session_jobs(config, session_id):
        request_value = _json(row["request_json"])
        reference_ref = str(request_value.get("reference_image_ref") or "").strip()
        if reference_ref and reference_ref not in assets:
            try:
                assets[reference_ref] = _asset_metadata(config, reference_ref, role="reference_image", kind="image")
            except RuntimeError:
                pass
        result = _json(row["result_json"])
        for item in result.get("assets") or []:
            if not isinstance(item, dict):
                continue
            ref = str(item.get("relative_path") or "").strip()
            if not ref:
                continue
            try:
                metadata = _asset_metadata(
                    config,
                    ref,
                    role=str(item.get("role") or item.get("kind") or "artifact"),
                    kind=str(item.get("kind") or "") or None,
                )
            except RuntimeError:
                continue
            for field in ("duration_seconds", "width", "height", "fps", "profile", "source_video_ref"):
                if item.get(field) is not None:
                    metadata[field] = item[field]
            assets[ref] = metadata

    return list(assets.values())


def select_submission_asset(config: Config, session_id: str, *, selection: str, relative_path: str) -> dict[str, Any]:
    selection = selection.strip().lower()
    by_ref = {item["relative_path"]: item for item in session_assets(config, session_id)}
    asset = by_ref.get(relative_path)
    if not asset:
        raise RuntimeError("Selected artifact does not belong to this Creative Session.")

    state = load_submission_state(config, session_id)
    if selection == "work_image":
        if str(asset.get("role")) not in WORK_IMAGE_ROLES or not str(asset.get("mime_type") or "").startswith("image/"):
            raise RuntimeError("OOC Work Image must be a web image/thumbnail from this Creative Session.")
        state["work_image_ref"] = relative_path
    elif selection == "experience":
        if str(asset.get("role")) != EXPERIENCE_ROLE:
            raise RuntimeError("OOC Experience must be a mobile video with audio.")
        state["experience_ref"] = relative_path
    else:
        raise RuntimeError("Unknown OOC submission selection.")

    state["updated_at"] = _utc_now()
    _save_submission_state(config, session_id, state)
    return state


def set_proposed_artist(
    config: Config,
    session_id: str,
    *,
    artist_id: str,
    artist_name: str | None = None,
) -> dict[str, Any]:
    if not get_creative_session(config, session_id):
        raise RuntimeError("Creative session not found.")
    cleaned_id = artist_id.strip()
    if not cleaned_id:
        raise RuntimeError("Select an OOC Artist before presenting this Creative Session.")
    state = load_submission_state(config, session_id)
    state["proposed_artist_id"] = cleaned_id
    state["proposed_artist_name"] = (artist_name or "").strip() or None
    state["updated_at"] = _utc_now()
    _save_submission_state(config, session_id, state)
    return state


def _latest_asset_by_role(assets: list[dict[str, Any]], role: str) -> dict[str, Any] | None:
    for item in reversed(assets):
        if str(item.get("role")) == role:
            return item
    return None


def _public_asset(asset: dict[str, Any]) -> dict[str, Any]:
    return {
        "role": asset["role"],
        "filename": asset["filename"],
        "mime_type": asset["mime_type"],
        "size_bytes": asset["size_bytes"],
        "sha256": asset["sha256"],
    }


def _production_reference(asset: dict[str, Any] | None) -> dict[str, Any] | None:
    if not asset:
        return None
    return {
        "role": asset["role"],
        "forge_ref": asset["relative_path"],
        "sha256": asset["sha256"],
        "size_bytes": asset["size_bytes"],
        "mime_type": asset["mime_type"],
    }


def build_submission_manifest(
    config: Config,
    session_id: str,
    *,
    submission_id: str | None = None,
    presented_at: str | None = None,
) -> dict[str, Any]:
    session = get_creative_session(config, session_id)
    if not session:
        raise RuntimeError("Creative session not found.")
    state = load_submission_state(config, session_id)
    work_image_ref = str(state.get("work_image_ref") or "").strip()
    experience_ref = str(state.get("experience_ref") or "").strip()
    artist_id = str(state.get("proposed_artist_id") or "").strip()
    if not work_image_ref:
        raise RuntimeError("Select the OOC Work Image before presenting this Creative Session.")
    if not experience_ref:
        raise RuntimeError("Select the mobile video with audio for the OOC Experience before presenting.")
    if not artist_id:
        raise RuntimeError("Select an OOC Artist before presenting this Creative Session.")

    assets = session_assets(config, session_id)
    by_ref = {item["relative_path"]: item for item in assets}
    work_image = by_ref.get(work_image_ref)
    experience = by_ref.get(experience_ref)
    if not work_image or str(work_image.get("role")) not in WORK_IMAGE_ROLES:
        raise RuntimeError("Selected OOC Work Image is no longer available.")
    if not experience or str(experience.get("role")) != EXPERIENCE_ROLE:
        raise RuntimeError("Selected OOC Experience is no longer an available mobile video with audio.")

    identity = ensure_identity(config)
    snapshot_id = submission_id or str(uuid4())
    timestamp = presented_at or _utc_now()
    retained = {
        "seed_work": _production_reference(_latest_asset_by_role(assets, "seed_work")),
        "print_master": _production_reference(_latest_asset_by_role(assets, "print_master")),
        "print_plate": _production_reference(_latest_asset_by_role(assets, "print_plate")),
        "video_master": _production_reference(_latest_asset_by_role(assets, "video_master")),
        "audio_master": _production_reference(_latest_asset_by_role(assets, "audio_master")),
    }
    retained = {key: value for key, value in retained.items() if value is not None}

    return {
        "schema": SUBMISSION_SCHEMA,
        "submission_id": snapshot_id,
        "presented_at": timestamp,
        "source": {
            "kind": "FORGE_CREATIVE_SESSION",
            "forge_id": str(identity["forge_id"]),
            "creative_session_id": session_id,
            "creative_session_updated_at": str(session["updated_at"]),
        },
        "creative_agency": {"kind": "HUMAN_STUDIO"},
        "proposed_attribution": {
            "creator_kind": "ARTIST",
            "creator_id": artist_id,
            "display_name": state.get("proposed_artist_name"),
            "role_code": "CREATOR",
        },
        "work": {
            "title": str(session["title"]),
            "description": None,
            "work_image": _public_asset(work_image),
        },
        "experience": {
            "experience_type": "VIDEO",
            "media": _public_asset(experience),
        },
        "provenance": {
            "creative_prompt": str(session["prompt"]),
            "negative_prompt": str(session["negative_prompt"] or "") or None,
            "seed_work_sha256": str(session["seed_work_sha256"] or "") or None,
            "retained_production_assets": retained,
        },
        "publication": {
            "requested": False,
            "note": "Forge presents a Studio Submission only; OOC Admin admits and publishes explicitly.",
        },
    }


def _manifest_sha256(manifest: dict[str, Any]) -> str:
    payload = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def present_submission(config: Config, session_id: str) -> dict[str, Any]:
    secrets_value = ensure_secrets(config)
    origin = str(secrets_value.get("ooc_origin") or config.ooc_origin or "").rstrip("/")
    token = str(secrets_value.get("machine_token") or "")
    if not origin or not token:
        raise RuntimeError("Pair this Forge with OOC before presenting a Studio Submission.")

    manifest = build_submission_manifest(config, session_id)
    state = load_submission_state(config, session_id)
    work_image = _safe_path(config, str(state["work_image_ref"]))
    experience = _safe_path(config, str(state["experience_ref"]))
    with work_image.open("rb") as image_handle, experience.open("rb") as video_handle:
        response = requests.post(
            f"{origin}{SUBMISSION_ENDPOINT}",
            headers={
                "X-OOC-Machine-Token": token,
                "X-OOC-Submission-Id": str(manifest["submission_id"]),
            },
            data={"manifest": json.dumps(manifest, sort_keys=True, separators=(",", ":"))},
            files={
                "work_image": (
                    work_image.name,
                    image_handle,
                    str(manifest["work"]["work_image"]["mime_type"]),
                ),
                "experience": (
                    experience.name,
                    video_handle,
                    str(manifest["experience"]["media"]["mime_type"]),
                ),
            },
            timeout=300,
        )
    response.raise_for_status()
    value = response.json()
    if not isinstance(value, dict):
        raise RuntimeError("OOC Studio Submission endpoint returned non-object JSON.")

    state["last_submission_id"] = str(value.get("submission_id") or manifest["submission_id"])
    state["last_presented_at"] = manifest["presented_at"]
    state["last_presented_manifest_sha256"] = _manifest_sha256(manifest)
    state["updated_at"] = _utc_now()
    _save_submission_state(config, session_id, state)
    return value


def _remove_ref(value: Any, relative_path: str) -> Any:
    if isinstance(value, list):
        cleaned = []
        for item in value:
            if isinstance(item, dict) and str(item.get("relative_path") or "") == relative_path:
                continue
            cleaned.append(_remove_ref(item, relative_path))
        return cleaned
    if isinstance(value, dict):
        return {key: _remove_ref(item, relative_path) for key, item in value.items()}
    if isinstance(value, str) and value == relative_path:
        return None
    return value


def delete_session_artifact(config: Config, session_id: str, relative_path: str) -> None:
    session = get_creative_session(config, session_id)
    if not session:
        raise RuntimeError("Creative session not found.")
    relative_path = relative_path.strip()
    by_ref = {item["relative_path"]: item for item in session_assets(config, session_id)}
    if relative_path not in by_ref:
        raise RuntimeError("Artifact does not belong to this Creative Session.")

    state = load_submission_state(config, session_id)
    if relative_path in {str(state.get("work_image_ref") or ""), str(state.get("experience_ref") or "")}:
        raise RuntimeError("This artifact is selected for OOC submission. Select another artifact before deleting it.")

    path = _safe_path(config, relative_path)
    path.unlink(missing_ok=True)

    session_updates: list[str] = []
    session_values: list[Any] = []
    for ref_column, sha_column in (
        ("seed_work_ref", "seed_work_sha256"),
        ("thumbnail_ref", "thumbnail_sha256"),
        ("etching_plate_ref", "etching_plate_sha256"),
    ):
        if str(session[ref_column] or "") == relative_path:
            session_updates.extend([f"{ref_column}=NULL", f"{sha_column}=NULL"])
    if str(session["seed_source_ref"] or "") == relative_path:
        session_updates.append("seed_source_ref=NULL")

    jobs = list_session_jobs(config, session_id)
    with transaction(config) as connection:
        if session_updates:
            connection.execute(
                f"UPDATE creative_sessions SET {', '.join(session_updates)}, updated_at=? WHERE id=?",
                (_utc_now(), session_id),
            )
        for row in jobs:
            result = _json(row["result_json"])
            request_value = _json(row["request_json"])
            changed_result = _remove_ref(result, relative_path)
            changed_request = _remove_ref(request_value, relative_path)
            if str(request_value.get("reference_image_ref") or "") == relative_path:
                if isinstance(changed_request, dict):
                    changed_request["workflow_id"] = "manual-image"
                    changed_request["creation_mode"] = "prompt"
                    for key in (
                        "reference_image_ref",
                        "reference_image_sha256",
                        "reference_input_name",
                        "reference_denoise",
                    ):
                        changed_request.pop(key, None)
            if changed_result != result:
                connection.execute(
                    "UPDATE jobs SET result_json=? WHERE id=?",
                    (json.dumps(changed_result, sort_keys=True), str(row["id"])),
                )
            if changed_request != request_value:
                connection.execute(
                    "UPDATE jobs SET request_json=? WHERE id=?",
                    (json.dumps(changed_request, sort_keys=True), str(row["id"])),
                )
