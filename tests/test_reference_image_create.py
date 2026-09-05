from __future__ import annotations

import io
from pathlib import Path
from types import SimpleNamespace

import pytest

from forge.config import Config
from forge.reference_image import (
    prepare_reference_image,
    remove_staged_reference_image,
    store_reference_image,
)


ROOT = Path(__file__).resolve().parents[1]


def read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def make_config(tmp_path: Path) -> Config:
    return Config(
        data_root=tmp_path,
        comfy_url="http://127.0.0.1:8188",
        ooc_origin=None,
        poll_interval=5,
        default_checkpoint=None,
    )


def upload(name: str, data: bytes):
    return SimpleNamespace(filename=name, stream=io.BytesIO(data))


def test_reference_image_is_verified_persisted_and_staged(tmp_path):
    config = make_config(tmp_path)
    prepared = prepare_reference_image(upload("camera.jpg", b"\xff\xd8\xff" + b"photo"))
    assert prepared is not None
    assert prepared.suffix == ".jpg"
    assert prepared.mime_type == "image/jpeg"

    stored = store_reference_image(config, session_id="session-1", prepared=prepared)
    library = config.data_root / stored["reference_image_ref"]
    staged = config.data_root / "comfyui-input" / stored["input_image"]
    assert library.read_bytes() == prepared.data
    assert staged.read_bytes() == prepared.data
    assert stored["reference_image_sha256"] == prepared.sha256

    remove_staged_reference_image(config, "session-1")
    assert not staged.exists()
    assert library.exists()


def test_reference_image_rejects_non_image_payload():
    with pytest.raises(RuntimeError, match="PNG, JPEG or WebP"):
        prepare_reference_image(upload("not-image.txt", b"not an image"))


def test_reference_workflow_is_native_img2img_and_bound_to_prompt_and_source():
    workflow = read("workflows/manual-image-reference/workflow.json")
    manifest = read("workflows/manual-image-reference/manifest.json")
    assert '"class_type": "LoadImage"' in workflow
    assert '"class_type": "ImageScale"' in workflow
    assert '"class_type": "VAEEncode"' in workflow
    assert '"latent_image": ["11", 0]' in workflow
    assert '"source": "input_image"' in manifest
    assert '"source": "reference_denoise"' in manifest
    assert '"source": "prompt"' in manifest


def test_create_ui_offers_prompt_or_reference_with_upload_and_camera():
    template = read("forge/templates/create.html")
    assert "Prompt only" in template
    assert "Prompt + Reference Image" in template
    assert 'enctype="multipart/form-data"' in template
    assert 'name="reference_image"' in template
    assert 'name="camera_image"' in template
    assert 'capture="environment"' in template


def test_header_brand_is_forge_and_mobile_navigation_uses_hamburger():
    base = read("forge/templates/base.html")
    css = read("forge/static/forge.css")
    assert "<strong>FORGE</strong>" in base
    assert "<strong>OOC</strong>" not in base
    assert 'class="mobile-nav"' in base
    assert 'class="hamburger"' in base
    assert ".desktop-nav{display:none}" in css
    assert ".mobile-nav{display:block}" in css
