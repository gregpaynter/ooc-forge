from __future__ import annotations

import json
import time
from typing import Any

import requests

from forge import __version__
from forge.config import Config
from forge.dispatch import execute
from forge.health import capabilities, report
from forge.storage import ensure_identity, ensure_layout, ensure_secrets, update_secrets


def _request(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    payload: dict[str, Any] | None = None,
    timeout: int = 30,
) -> dict[str, Any]:
    response = requests.request(method, url, headers=headers, json=payload, timeout=timeout)
    response.raise_for_status()
    value = response.json()
    if not isinstance(value, dict):
        raise RuntimeError("OOC returned non-object JSON")
    return value


def _pair(config: Config, secrets_value: dict[str, Any]) -> dict[str, Any]:
    pairing_id = secrets_value.get("pairing_id")
    poll_secret = secrets_value.get("pairing_poll_secret")
    origin = secrets_value.get("ooc_origin") or config.ooc_origin
    if not origin or not pairing_id or not poll_secret or secrets_value.get("machine_token"):
        return secrets_value
    try:
        value = _request(
            "POST",
            f"{str(origin).rstrip('/')}/api/forge/pairing/{pairing_id}/status",
            headers={"X-OOC-Pairing-Secret": str(poll_secret)},
        )
        token = value.get("machine_token")
        if token:
            return update_secrets(
                config,
                machine_token=str(token),
                machine_principal_id=str(value.get("machine_principal_id") or ""),
                pairing_status=str(value.get("status") or "CREDENTIAL_DELIVERED"),
            )
        update_secrets(config, pairing_status=str(value.get("status") or "PENDING"))
    except requests.RequestException:
        pass
    return ensure_secrets(config)


def _upload_assets(
    config: Config,
    *,
    origin: str,
    headers: dict[str, str],
    forge_id: str,
    remote_id: str,
    assets: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    uploaded: list[dict[str, Any]] = []
    for asset in assets:
        local_path = config.data_root / str(asset["relative_path"])
        with local_path.open("rb") as handle:
            response = requests.post(
                f"{origin}/api/forge/assets",
                headers=headers,
                data={"forge_id": forge_id, "job_id": remote_id},
                files={
                    "file": (
                        local_path.name,
                        handle,
                        str(asset.get("mime_type") or "application/octet-stream"),
                    )
                },
                timeout=300,
            )
        response.raise_for_status()
        preview = response.json()
        if not isinstance(preview, dict):
            raise RuntimeError("OOC asset upload returned non-object JSON")
        preview["role"] = asset.get("role")
        preview["kind"] = asset.get("kind")
        if asset.get("print"):
            preview["print"] = asset["print"]
        if asset.get("duration_seconds") is not None:
            preview["duration_seconds"] = asset["duration_seconds"]
        if asset.get("profile"):
            preview["profile"] = asset["profile"]
        if asset.get("source_video_ref"):
            preview["source_video_ref"] = asset["source_video_ref"]
        uploaded.append(preview)
    return uploaded


def _copy_role_fields(result: dict[str, Any], uploaded: list[dict[str, Any]]) -> None:
    role_fields = {
        "print_master": ("print_media_asset_id", "print_storage_ref", "print_sha256"),
        "video_master": ("video_master_media_asset_id", "video_master_storage_ref", "video_master_sha256"),
        "video_mobile": ("video_mobile_media_asset_id", "video_mobile_storage_ref", "video_mobile_sha256"),
        "audio_master": ("audio_master_media_asset_id", "audio_master_storage_ref", "audio_master_sha256"),
        "audio_web": ("audio_web_media_asset_id", "audio_web_storage_ref", "audio_web_sha256"),
        "video_master_with_audio": (
            "video_master_with_audio_media_asset_id",
            "video_master_with_audio_storage_ref",
            "video_master_with_audio_sha256",
        ),
        "video_mobile_with_audio": (
            "video_mobile_with_audio_media_asset_id",
            "video_mobile_with_audio_storage_ref",
            "video_mobile_with_audio_sha256",
        ),
    }
    for role, fields in role_fields.items():
        asset = next((item for item in uploaded if item.get("role") == role), None)
        if not asset:
            continue
        result[fields[0]] = asset["media_asset_id"]
        result[fields[1]] = asset["storage_ref"]
        result[fields[2]] = asset["sha256"]


def _heartbeat_and_job(config: Config, secrets_value: dict[str, Any]) -> None:
    origin = str(secrets_value.get("ooc_origin") or config.ooc_origin or "").rstrip("/")
    token = str(secrets_value.get("machine_token") or "")
    if not origin or not token:
        return
    identity = ensure_identity(config)
    headers = {"X-OOC-Machine-Token": token}
    health = report(config)
    _request(
        "POST",
        f"{origin}/api/forge/heartbeat",
        headers=headers,
        payload={
            "forge_id": identity["forge_id"],
            "runtime_version": __version__,
            "hardware": {"gpu": health["gpu"], "storage": health["storage"]},
            "capabilities": capabilities(config),
            "health": health,
        },
    )
    claimed = _request(
        "POST",
        f"{origin}/api/forge/jobs/claim",
        headers=headers,
        payload={"forge_id": identity["forge_id"]},
    ).get("job")
    if not claimed:
        return
    if not isinstance(claimed, dict):
        raise RuntimeError("Claimed job is not an object")
    remote_id = str(claimed["id"])
    try:
        result = execute(dict(claimed.get("request") or {}))
        generation_evidence = result.pop("generation_evidence", None)
        assets = result.pop("assets", []) or []
        result.pop("media_ref", None)
        if assets:
            uploaded = _upload_assets(
                config,
                origin=origin,
                headers=headers,
                forge_id=identity["forge_id"],
                remote_id=remote_id,
                assets=assets,
            )
            preview = uploaded[0]
            result["preview_media_asset_id"] = preview["media_asset_id"]
            result["preview_ref"] = preview["storage_ref"]
            result["preview_sha256"] = preview["sha256"]
            result["assets"] = uploaded
            _copy_role_fields(result, uploaded)
        _request(
            "POST",
            f"{origin}/api/forge/jobs/{remote_id}/complete",
            headers=headers,
            payload={
                "result": result,
                "candidate_title": str(result.get("title") or f"Candidate {remote_id[:8]}"),
                "candidate_description": result.get("description"),
                "generation_evidence": generation_evidence,
            },
            timeout=300,
        )
    except Exception as error:
        try:
            _request(
                "POST",
                f"{origin}/api/forge/jobs/{remote_id}/fail",
                headers=headers,
                payload={"reason": str(error)},
            )
        except Exception:
            pass


def main() -> int:
    config = Config.load()
    ensure_layout(config)
    while True:
        secrets_value = _pair(config, ensure_secrets(config))
        try:
            _heartbeat_and_job(config, secrets_value)
        except requests.RequestException:
            pass
        time.sleep(config.poll_interval)


if __name__ == "__main__":
    raise SystemExit(main())
