from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_prompt_compiler_runtime_is_pinned_and_cpu_only():
    pins = read("scripts/prompt-runtime.env")
    assert "LLAMA_CPP_VERSION=v0.4.0" in pins
    assert "LLAMA_CPP_COMMIT=427291b5b34cd914a31b3fd3b61a68f6184f4b9f" in pins
    installer = read("scripts/install-prompt-runtime")
    assert "GGML_CUDA=OFF" in installer
    assert "--target llama-cli" in installer
    assert "/usr/local/bin/ooc-llama-cli" in installer


def test_prompt_and_video_models_are_verified_and_on_demand():
    prompt = read("scripts/ooc-forge-prompt-model-install")
    assert "Qwen3-1.7B-Q4_K_M.gguf" in prompt
    assert "d2387ca2dbfee2ffabce7120d3770dadca0b293052bc2f0e138fdc940d9bc7b5" in prompt
    assert "--continue-at -" in prompt
    assert "sha256sum" in prompt

    video = read("scripts/ooc-forge-video-model-install")
    for sha in (
        "456f901338bd9eadbded3828b819109a9b68e8a525ca5cf8d0049a69fcfeca1e",
        "e40321bd36b9709991dae2530eb4ac303dd168276980d3e9bc4b6e2b75fed156",
        "c3355d30191f1f066b26d93fba017ae9809dce6c627dda5f6a66eaa651204f68",
    ):
        assert sha in video
    assert "--continue-at -" in video
    assert "sha256sum" in video

    sudoers = read("systemd/ooc-forge-maintenance.sudoers")
    assert "systemctl --no-block start ooc-forge-prompt-model-install.service" in sudoers
    assert "systemctl --no-block start ooc-forge-video-model-install.service" in sudoers


def test_video_workflow_is_native_wan_seed_work_i2v():
    workflow = read("workflows/video-wan22-ti2v/workflow.json")
    manifest = read("workflows/video-wan22-ti2v/manifest.json")
    assert '"class_type": "Wan22ImageToVideoLatent"' in workflow
    assert '"class_type": "CreateVideo"' in workflow
    assert '"class_type": "SaveVideo"' in workflow
    assert "wan2.2_ti2v_5B_fp16.safetensors" in workflow
    assert "umt5_xxl_fp8_e4m3fn_scaled.safetensors" in workflow
    assert "wan2.2_vae.safetensors" in workflow
    assert '"input_image"' in manifest
    assert '"prompt"' in manifest
    assert '"frames"' in manifest
    assert '"fps"' in manifest


def test_local_install_owns_prompt_runtime_video_workflow_and_services():
    installer = read("scripts/install-local.sh")
    assert "cmake ffmpeg" in installer
    assert "manual-image print-upscale video-wan22-ti2v" in installer
    assert '"$SOURCE_DIR/scripts/install-prompt-runtime"' in installer
    assert "ooc-forge-prompt-model-install" in installer
    assert "ooc-forge-video-model-install" in installer


def test_iso_owns_prompt_runtime_and_video_contract_without_model_weights():
    packages = read("iso/config/package-lists/forge.list.chroot").splitlines()
    assert "cmake" in packages
    hook = read("iso/config/hooks/live/010-ooc-forge-runtime.hook.chroot")
    assert "manual-image print-upscale video-wan22-ti2v" in hook
    assert '"$SOURCE_DIR/scripts/install-prompt-runtime"' in hook
    assert "ooc-forge-prompt-model-install.service" in hook
    assert "ooc-forge-video-model-install.service" in hook
    assert "systemctl enable ooc-forge-prompt-model-install.service" not in hook
    assert "systemctl enable ooc-forge-video-model-install.service" not in hook
    # Model weights are managed after install; only verified installer metadata belongs in the ISO.
    assert "wan2.2_ti2v_5B_fp16.safetensors" not in read("iso/config/package-lists/forge.list.chroot")
