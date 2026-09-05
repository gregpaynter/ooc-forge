from __future__ import annotations

import json
from pathlib import Path

import pytest

from forge.config import Config
from forge.db import create_creative_session, create_job, finish_job, init_db, transaction
from forge.storage import ensure_identity, ensure_layout
from forge.submission import (
    EXPERIENCE_ROLE,
    SUBMISSION_SCHEMA,
    build_submission_manifest,
    delete_session_artifact,
    load_submission_state,
    select_submission_asset,
    set_proposed_artist,
)


def make_config(tmp_path: Path) -> Config:
    return Config(
        data_root=tmp_path,
        comfy_url="http://127.0.0.1:8188",
        ooc_origin=None,
        poll_interval=5,
        default_checkpoint=None,
    )


def write_asset(config: Config, relative_path: str, data: bytes = b"asset") -> Path:
    path = config.data_root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def make_curated_session(tmp_path: Path):
    config = make_config(tmp_path)
    ensure_layout(config)
    init_db(config)
    identity = ensure_identity(config)
    session_id = create_creative_session(
        config,
        title="Ball in the Forest",
        prompt="ball in the forest, woodblock print",
        negative_prompt="photographic",
    )

    work_root = config.library_root / "works" / session_id
    thumbnail = work_root / "thumbnail.webp"
    seed = work_root / "seed-work.png"
    plate = work_root / "etching-plate-inverse.png"
    thumbnail.parent.mkdir(parents=True, exist_ok=True)
    thumbnail.write_bytes(b"web-image")
    seed.write_bytes(b"seed-image")
    plate.write_bytes(b"plate")
    thumbnail_ref = thumbnail.relative_to(config.data_root).as_posix()
    seed_ref = seed.relative_to(config.data_root).as_posix()
    plate_ref = plate.relative_to(config.data_root).as_posix()
    with transaction(config) as connection:
        connection.execute(
            """
            UPDATE creative_sessions
            SET status='SEED_READY', seed_work_ref=?, seed_work_sha256=?,
                thumbnail_ref=?, thumbnail_sha256=?, etching_plate_ref=?, etching_plate_sha256=?
            WHERE id=?
            """,
            (seed_ref, "seed-sha", thumbnail_ref, "thumb-sha", plate_ref, "plate-sha", session_id),
        )

    job_id = create_job(
        config,
        source="LOCAL",
        job_type="AUDIO_EXPERIENCE",
        request={"kind": "audio_from_seed"},
        creative_session_id=session_id,
        derivative_type="audio",
    )
    experience_ref = f"library/audio/{job_id}/video-mobile-with-audio.mp4"
    silent_mobile_ref = f"library/experiences/{job_id}/video-mobile.mp4"
    video_master_ref = f"library/experiences/{job_id}/video-master.mp4"
    audio_master_ref = f"library/audio/{job_id}/audio-master.flac"
    print_master_ref = f"library/print/{job_id}/print-master.png"
    for ref, data in (
        (experience_ref, b"mobile-av"),
        (silent_mobile_ref, b"silent-mobile"),
        (video_master_ref, b"video-master"),
        (audio_master_ref, b"audio-master"),
        (print_master_ref, b"print-master"),
    ):
        write_asset(config, ref, data)
    finish_job(
        config,
        job_id,
        {
            "assets": [
                {
                    "kind": "video",
                    "role": EXPERIENCE_ROLE,
                    "relative_path": experience_ref,
                    "mime_type": "video/mp4",
                    "size_bytes": 9,
                    "sha256": "experience",
                },
                {
                    "kind": "video",
                    "role": "video_mobile",
                    "relative_path": silent_mobile_ref,
                    "mime_type": "video/mp4",
                    "size_bytes": 13,
                    "sha256": "silent",
                },
                {
                    "kind": "video",
                    "role": "video_master",
                    "relative_path": video_master_ref,
                    "mime_type": "video/mp4",
                    "size_bytes": 12,
                    "sha256": "video-master",
                },
                {
                    "kind": "audio",
                    "role": "audio_master",
                    "relative_path": audio_master_ref,
                    "mime_type": "audio/flac",
                    "size_bytes": 12,
                    "sha256": "audio-master",
                },
                {
                    "kind": "image",
                    "role": "print_master",
                    "relative_path": print_master_ref,
                    "mime_type": "image/png",
                    "size_bytes": 12,
                    "sha256": "print-master",
                },
            ],
            "video_mobile_with_audio_ref": experience_ref,
            "video_mobile_ref": silent_mobile_ref,
            "video_master_ref": video_master_ref,
        },
    )
    return config, identity, session_id, thumbnail_ref, experience_ref, silent_mobile_ref, print_master_ref


def test_submission_manifest_uploads_only_work_image_and_mobile_video_with_audio(tmp_path):
    config, identity, session_id, thumbnail_ref, experience_ref, _silent, _print = make_curated_session(tmp_path)
    select_submission_asset(config, session_id, selection="work_image", relative_path=thumbnail_ref)
    select_submission_asset(config, session_id, selection="experience", relative_path=experience_ref)
    set_proposed_artist(config, session_id, artist_id="artist-123", artist_name="Greg Paynter")

    manifest = build_submission_manifest(
        config,
        session_id,
        submission_id="submission-1",
        presented_at="2026-09-05T05:00:00.000Z",
    )

    assert manifest["schema"] == SUBMISSION_SCHEMA
    assert manifest["submission_id"] == "submission-1"
    assert manifest["source"]["forge_id"] == identity["forge_id"]
    assert manifest["source"]["creative_session_id"] == session_id
    assert manifest["creative_agency"] == {"kind": "HUMAN_STUDIO"}
    assert manifest["proposed_attribution"] == {
        "creator_kind": "ARTIST",
        "creator_id": "artist-123",
        "display_name": "Greg Paynter",
        "role_code": "CREATOR",
    }
    assert manifest["work"]["work_image"]["filename"] == "thumbnail.webp"
    assert manifest["experience"]["media"]["role"] == EXPERIENCE_ROLE
    assert manifest["experience"]["media"]["filename"] == "video-mobile-with-audio.mp4"
    assert manifest["publication"]["requested"] is False
    assert "place" not in json.dumps(manifest).lower()

    retained = manifest["provenance"]["retained_production_assets"]
    assert "print_master" in retained
    assert "video_master" in retained
    assert "audio_master" in retained
    assert retained["print_master"]["forge_ref"].endswith("print-master.png")
    assert retained["video_master"]["forge_ref"].endswith("video-master.mp4")
    assert retained["audio_master"]["forge_ref"].endswith("audio-master.flac")
    assert retained["print_master"]["forge_ref"] not in json.dumps(manifest["work"])
    assert retained["video_master"]["forge_ref"] not in json.dumps(manifest["experience"])


def test_silent_mobile_video_cannot_be_selected_as_ooc_experience(tmp_path):
    config, _identity, session_id, _thumbnail, _experience, silent_mobile_ref, _print = make_curated_session(tmp_path)
    with pytest.raises(RuntimeError, match="mobile video with audio"):
        select_submission_asset(
            config,
            session_id,
            selection="experience",
            relative_path=silent_mobile_ref,
        )


def test_selected_artifact_must_be_replaced_before_individual_deletion(tmp_path):
    config, _identity, session_id, thumbnail_ref, experience_ref, _silent, print_master_ref = make_curated_session(tmp_path)
    select_submission_asset(config, session_id, selection="work_image", relative_path=thumbnail_ref)
    select_submission_asset(config, session_id, selection="experience", relative_path=experience_ref)

    with pytest.raises(RuntimeError, match="selected for OOC submission"):
        delete_session_artifact(config, session_id, thumbnail_ref)
    with pytest.raises(RuntimeError, match="selected for OOC submission"):
        delete_session_artifact(config, session_id, experience_ref)

    delete_session_artifact(config, session_id, print_master_ref)
    assert not (config.data_root / print_master_ref).exists()
    assert load_submission_state(config, session_id)["work_image_ref"] == thumbnail_ref
