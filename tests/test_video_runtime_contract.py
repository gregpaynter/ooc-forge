from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def assert_looped_service_installed(text: str, service: str) -> None:
    assert service in text
    assert '"$SOURCE_DIR/systemd/$unit.service"' in text
    assert '"/etc/systemd/system/$unit.service"' in text


def test_prompt_compiler_runtime_is_pinned_and_cpu_only():
    pins = read("scripts/prompt-runtime.env")
    assert "LLAMA_CPP_VERSION=v0.4.0" in pins
    assert "LLAMA_CPP_COMMIT=427291b5b34cd914a31b3fd3b61a68f6184f4b9f" in pins
    installer_path = ROOT / "scripts/install-prompt-runtime"
    assert installer_path.stat().st_mode & 0o111
    installer = installer_path.read_text(encoding="utf-8")
    assert "GGML_CUDA=OFF" in installer
    # At the pinned upstream commit tools/cli is only added when this option is ON.
    assert "LLAMA_BUILD_SERVER=ON" in installer
    assert "--target llama-cli" in installer
    assert "/usr/local/bin/ooc-llama-cli" in installer


def test_prompt_video_and_audio_models_are_verified_and_on_demand():
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
    assert "Comfy-Org/Wan_2.2_ComfyUI_Repackaged/resolve/main/split_files/text_encoders/umt5_xxl_fp8_e4m3fn_scaled.safetensors" in video
    assert "--continue-at -" in video
    assert "sha256sum" in video
    assert "Checksum mismatch for $filename; retrying one clean download from byte zero." in video
    assert "did not match after a clean retry" in video

    audio = read("scripts/ooc-forge-audio-model-install")
    assert "stable_audio_3_medium_base.safetensors" in audio
    assert "t5gemma_b_b_ul2.safetensors" in audio
    assert "c443fcc4d491475064cd0ff3eb92459b1e5f5060e86d96d016f048e528e24195" in audio
    assert "1e1eba25be8872edb0d3c6335c6658fd6388e7b14b60da6e454e404cfcd8150e" in audio
    assert "--continue-at -" in audio
    assert "sha256sum" in audio
    assert "Checksum mismatch for $filename; retrying one clean download from byte zero." in audio
    assert "did not match after a clean retry" in audio

    sudoers = read("systemd/ooc-forge-maintenance.sudoers")
    assert "systemctl --no-block start ooc-forge-prompt-model-install.service" in sudoers
    assert "systemctl --no-block start ooc-forge-video-model-install.service" in sudoers
    assert "systemctl --no-block start ooc-forge-audio-model-install.service" in sudoers


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


def test_audio_workflow_is_native_stable_audio3():
    workflow = read("workflows/audio-stable-audio3/workflow.json")
    manifest = read("workflows/audio-stable-audio3/manifest.json")
    assert '"class_type": "CheckpointLoaderSimple"' in workflow
    assert '"class_type": "CLIPLoader"' in workflow
    assert '"type": "stable_audio"' in workflow
    assert '"class_type": "EmptyLatentAudio"' in workflow
    assert '"class_type": "VAEDecodeAudio"' in workflow
    assert '"class_type": "SaveAudio"' in workflow
    assert "stable_audio_3_medium_base.safetensors" in workflow
    assert "t5gemma_b_b_ul2.safetensors" in workflow
    assert '"duration_seconds"' in manifest
    assert '"prompt"' in manifest
    assert '"seed"' in manifest


def test_local_install_owns_prompt_video_audio_and_reference_workflows():
    installer = read("scripts/install-local.sh")
    assert "cmake ffmpeg" in installer
    for workflow in (
        "manual-image",
        "manual-image-reference",
        "print-upscale",
        "video-wan22-ti2v",
        "audio-stable-audio3",
    ):
        assert workflow in installer
    assert '"$SOURCE_DIR/scripts/install-prompt-runtime"' in installer
    assert_looped_service_installed(installer, "ooc-forge-prompt-model-install")
    assert_looped_service_installed(installer, "ooc-forge-video-model-install")
    assert_looped_service_installed(installer, "ooc-forge-audio-model-install")
    assert 'ooc-forge-audio-model-install" /usr/local/sbin/ooc-forge-audio-model-install' in installer


def test_iso_owns_prompt_reference_video_and_audio_contract_without_model_weights():
    packages = read("iso/config/package-lists/forge.list.chroot").splitlines()
    assert "cmake" in packages
    hook = read("iso/config/hooks/live/010-ooc-forge-runtime.hook.chroot")
    for workflow in (
        "manual-image",
        "manual-image-reference",
        "print-upscale",
        "video-wan22-ti2v",
        "audio-stable-audio3",
    ):
        assert workflow in hook
    assert '"$SOURCE_DIR/scripts/install-prompt-runtime"' in hook
    assert_looped_service_installed(hook, "ooc-forge-prompt-model-install")
    assert_looped_service_installed(hook, "ooc-forge-video-model-install")
    assert_looped_service_installed(hook, "ooc-forge-audio-model-install")
    assert "systemctl enable ooc-forge-prompt-model-install.service" not in hook
    assert "systemctl enable ooc-forge-video-model-install.service" not in hook
    assert "systemctl enable ooc-forge-audio-model-install.service" not in hook
    # Model weights are managed after install; only verified installer metadata belongs in the ISO.
    package_list = read("iso/config/package-lists/forge.list.chroot")
    assert "wan2.2_ti2v_5B_fp16.safetensors" not in package_list
    assert "stable_audio_3_medium_base.safetensors" not in package_list


def test_auto_refresh_pauses_while_operator_edits_forms():
    base = read("forge/templates/base.html")
    assert "autoRefreshMeta.remove()" in base
    assert "input:not([type=\"hidden\"]), textarea, select" in base
    assert "if(!editing&&!dirty){window.location.reload();return;}" in base
    assert "document.addEventListener('input'" in base
    assert "document.addEventListener('focusin'" in base
