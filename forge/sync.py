from __future__ import annotations

import json
import time
from typing import Any

import requests

from forge import __version__
from forge.config import Config
from forge.db import (
    all_assets,
    init_db,
    mark_asset_uploaded,
    pending_assets,
    receive_remote_job,
    remote_jobs_to_sync,
    set_sync_status,
    studies_to_submit,
    update_remote_lease,
)
from forge.health import capabilities, report
from forge.storage import ensure_identity, ensure_layout, ensure_secrets, update_secrets


def _request(
    method: str, url: str, *, headers=None, payload=None, timeout: int = 30
) -> dict[str, Any]:
    response = requests.request(
        method, url, headers=headers, json=payload, timeout=timeout
    )
    response.raise_for_status()
    value = response.json()
    if not isinstance(value, dict):
        raise RuntimeError("OOC returned non-object JSON")
    return value


def _pair(config: Config, secrets_value: dict[str, Any]) -> dict[str, Any]:
    pairing_id = secrets_value.get("pairing_id")
    poll_secret = secrets_value.get("pairing_poll_secret")
    origin = secrets_value.get("ooc_origin") or config.ooc_origin
    if (
        not origin
        or not pairing_id
        or not poll_secret
        or secrets_value.get("machine_token")
    ):
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


def _machine_context(config: Config, secrets_value: dict[str, Any]):
    origin = str(secrets_value.get("ooc_origin") or config.ooc_origin or "").rstrip("/")
    token = str(secrets_value.get("machine_token") or "")
    if not origin or not token:
        return None
    return origin, {"X-OOC-Machine-Token": token}, ensure_identity(config)


def _heartbeat_and_claim(config: Config, secrets_value: dict[str, Any]) -> None:
    context = _machine_context(config, secrets_value)
    if not context:
        return
    origin, headers, identity = context
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
    required = set(claimed.get("required_capabilities") or [])
    available = {
        name for name, enabled in capabilities(config).items() if enabled is True
    }
    missing = required - available
    if missing:
        raise RuntimeError(
            "OOC dispatched unsupported Forge capabilities: "
            + ", ".join(sorted(missing))
        )
    receive_remote_job(
        config,
        remote_job_id=str(claimed["id"]),
        attempt_token=str(claimed["attempt_token"]),
        lease_until=str(claimed["lease_until"]),
        request=dict(claimed.get("request") or {}),
    )


def _renew_lease(
    config: Config, origin: str, headers: dict[str, str], row: Any
) -> None:
    value = _request(
        "POST",
        f"{origin}/api/forge/jobs/{row['remote_job_id']}/lease",
        headers=headers,
        payload={"attempt_token": str(row["attempt_token"])},
    )
    update_remote_lease(config, str(row["id"]), str(value["lease_until"]))


def _upload_assets(
    config: Config, origin: str, headers: dict[str, str], identity, row
) -> None:
    result = json.loads(str(row["result_json"] or "{}"))
    if not result:
        raise RuntimeError("Completed Forge job has no result manifest")
    for asset in pending_assets(config, str(row["id"])):
        local_path = config.data_root / str(asset["relative_path"])
        with local_path.open("rb") as handle:
            response = requests.post(
                f"{origin}/api/forge/assets",
                headers={**headers, "Idempotency-Key": str(asset["client_asset_id"])},
                data={
                    "forge_id": identity["forge_id"],
                    "job_id": str(row["remote_job_id"]),
                    "attempt_token": str(row["attempt_token"]),
                    "client_asset_id": str(asset["client_asset_id"]),
                    "kind": str(asset["kind"]),
                    "sha256": str(asset["sha256"]),
                },
                files={
                    "file": (
                        local_path.name,
                        handle,
                        str(asset["mime_type"] or "application/octet-stream"),
                    )
                },
                timeout=120,
            )
        response.raise_for_status()
        uploaded = response.json()
        mark_asset_uploaded(
            config,
            asset_id=str(asset["id"]),
            remote_media_asset_id=str(uploaded["media_asset_id"]),
        )
    if not pending_assets(config, str(row["id"])):
        set_sync_status(config, str(row["id"]), "READY_TO_COMPLETE")


def _complete(config: Config, origin: str, headers: dict[str, str], row) -> None:
    result = json.loads(str(row["result_json"] or "{}"))
    generation_evidence = result.pop("generation_evidence", None)
    result.pop("media_ref", None)
    assets = [
        {
            "client_asset_id": str(asset["client_asset_id"]),
            "media_asset_id": str(asset["remote_media_asset_id"]),
            "kind": str(asset["kind"]),
            "sha256": str(asset["sha256"]),
            "mime_type": str(asset["mime_type"] or "application/octet-stream"),
        }
        for asset in all_assets(config, str(row["id"]))
    ]
    result["assets"] = assets
    value = _request(
        "POST",
        f"{origin}/api/forge/jobs/{row['remote_job_id']}/complete",
        headers={
            **headers,
            "Idempotency-Key": f"complete:{row['remote_job_id']}:{row['attempt_token']}",
        },
        payload={
            "attempt_token": str(row["attempt_token"]),
            "result": result,
            "candidate_title": str(
                result.get("title") or f"Candidate {str(row['remote_job_id'])[:8]}"
            ),
            "candidate_description": result.get("description"),
            "generation_evidence": generation_evidence,
            "assets": assets,
        },
        timeout=120,
    )
    set_sync_status(
        config, str(row["id"]), "ACKNOWLEDGED", candidate_id=str(value["candidate_id"])
    )


def _report_failure(config: Config, origin: str, headers: dict[str, str], row) -> None:
    _request(
        "POST",
        f"{origin}/api/forge/jobs/{row['remote_job_id']}/fail",
        headers={
            **headers,
            "Idempotency-Key": f"fail:{row['remote_job_id']}:{row['attempt_token']}",
        },
        payload={
            "attempt_token": str(row["attempt_token"]),
            "reason": str(row["error"] or "unspecified"),
        },
    )
    set_sync_status(config, str(row["id"]), "ACKNOWLEDGED")


def _reconcile(config: Config, secrets_value: dict[str, Any]) -> None:
    context = _machine_context(config, secrets_value)
    if not context:
        return
    origin, headers, identity = context
    for row in remote_jobs_to_sync(config):
        if (
            row["status"] in {"QUEUED", "RUNNING"}
            or row["sync_status"] == "READY_TO_UPLOAD"
        ):
            _renew_lease(config, origin, headers, row)
        if row["sync_status"] == "READY_TO_UPLOAD":
            _upload_assets(config, origin, headers, identity, row)
        refreshed = next(
            (item for item in remote_jobs_to_sync(config) if item["id"] == row["id"]),
            None,
        )
        if refreshed and refreshed["sync_status"] == "READY_TO_COMPLETE":
            _complete(config, origin, headers, refreshed)
        elif refreshed and refreshed["sync_status"] == "READY_TO_REPORT_FAILURE":
            _report_failure(config, origin, headers, refreshed)

    for study in studies_to_submit(config):
        result = json.loads(str(study["result_json"] or "{}"))
        assets = all_assets(config, str(study["id"]))
        handles = []
        try:
            files = []
            manifest = []
            for asset in assets:
                local_path = config.data_root / str(asset["relative_path"])
                handle = local_path.open("rb")
                handles.append(handle)
                files.append(
                    (
                        "files",
                        (
                            local_path.name,
                            handle,
                            str(asset["mime_type"] or "application/octet-stream"),
                        ),
                    )
                )
                manifest.append(
                    {
                        "client_asset_id": str(asset["client_asset_id"]),
                        "kind": str(asset["kind"]),
                        "sha256": str(asset["sha256"]),
                        "mime_type": str(
                            asset["mime_type"] or "application/octet-stream"
                        ),
                    }
                )
            response = requests.post(
                f"{origin}/api/forge/studies",
                headers={
                    **headers,
                    "Idempotency-Key": f"study:{identity['forge_id']}:{study['id']}",
                },
                data={
                    "forge_id": identity["forge_id"],
                    "local_job_id": str(study["id"]),
                    "title": str(
                        result.get("title") or f"Study {str(study['id'])[:8]}"
                    ),
                    "description": str(result.get("description") or ""),
                    "request_json": str(study["request_json"]),
                    "generation_evidence_json": json.dumps(
                        result.get("generation_evidence")
                    ),
                    "assets_json": json.dumps(manifest, sort_keys=True),
                },
                files=files,
                timeout=300,
            )
            response.raise_for_status()
            value = response.json()
            set_sync_status(
                config,
                str(study["id"]),
                "ACKNOWLEDGED",
                candidate_id=str(value["candidate_id"]),
            )
        finally:
            for handle in handles:
                handle.close()


def run_once(config: Config) -> None:
    secrets_value = _pair(config, ensure_secrets(config))
    _reconcile(config, secrets_value)
    _heartbeat_and_claim(config, secrets_value)


def main() -> int:
    config = Config.load()
    ensure_layout(config)
    init_db(config)
    while True:
        try:
            run_once(config)
        except requests.RequestException:
            pass
        time.sleep(config.poll_interval)


if __name__ == "__main__":
    raise SystemExit(main())
