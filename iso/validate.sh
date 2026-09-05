#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)

contains() {
  local needle=$1 file=$2
  if ! grep -Fq -- "$needle" "$file"; then
    echo "ISO validation failed: expected '$needle' in ${file#$ROOT_DIR/}" >&2
    exit 1
  fi
}

line_is() {
  local needle=$1 file=$2
  if ! grep -Fxq -- "$needle" "$file"; then
    echo "ISO validation failed: expected line '$needle' in ${file#$ROOT_DIR/}" >&2
    exit 1
  fi
}

bash -n \
  "$ROOT_DIR/iso/build.sh" \
  "$ROOT_DIR/iso/auto/config" \
  "$ROOT_DIR/iso/auto/build" \
  "$ROOT_DIR/iso/auto/clean" \
  "$ROOT_DIR/iso/config/hooks/live/010-ooc-forge-runtime.hook.chroot" \
  "$ROOT_DIR/scripts/install-comfyui-runtime" \
  "$ROOT_DIR/scripts/install-prompt-runtime" \
  "$ROOT_DIR/scripts/ooc-forge-appliance-policy" \
  "$ROOT_DIR/scripts/ooc-forge-prompt-model-install" \
  "$ROOT_DIR/scripts/ooc-forge-video-model-install" \
  "$ROOT_DIR/scripts/ooc-forge-audio-model-install"

contains '--distribution trixie' "$ROOT_DIR/iso/auto/config"
contains '--architectures amd64' "$ROOT_DIR/iso/auto/config"
contains '--bootloaders "syslinux,grub-efi"' "$ROOT_DIR/iso/auto/config"
contains '--uefi-secure-boot disable' "$ROOT_DIR/iso/auto/config"
contains 'lb build noauto' "$ROOT_DIR/iso/auto/build"
contains 'lb clean noauto' "$ROOT_DIR/iso/auto/clean"

for package in nvidia-driver linux-headers-amd64 dkms kmod openssh-server network-manager ffmpeg cmake; do
  line_is "$package" "$ROOT_DIR/iso/config/package-lists/forge.list.chroot"
done

HOOK="$ROOT_DIR/iso/config/hooks/live/010-ooc-forge-runtime.hook.chroot"
contains 'blacklist nouveau' "$HOOK"
contains 'update-initramfs -u -k all' "$HOOK"
contains 'install-comfyui-runtime' "$HOOK"
contains 'install-prompt-runtime' "$HOOK"
contains 'manual-image-reference' "$HOOK"
contains 'video-wan22-ti2v' "$HOOK"
contains 'audio-stable-audio3' "$HOOK"
contains 'ooc-forge-prompt-model-install' "$HOOK"
contains 'ooc-forge-video-model-install' "$HOOK"
contains 'ooc-forge-audio-model-install' "$HOOK"
contains 'ooc-forge-audio-model-install" /usr/local/sbin/ooc-forge-audio-model-install' "$HOOK"
contains 'systemctl enable comfyui.service' "$HOOK"
contains 'systemctl enable ssh.service' "$HOOK"
contains 'rm -f /etc/ssh/ssh_host_' "$HOOK"
contains 'ooc-forge-appliance-policy.service' "$HOOK"
contains 'ooc-forge-git-update' "$HOOK"
contains 'ooc-forge-maintenance.sudoers' "$HOOK"

contains '127.0.0.1 --port 8188' "$ROOT_DIR/systemd/comfyui.service"
contains 'XDG_CACHE_HOME=/forge-data/cache' "$ROOT_DIR/systemd/comfyui.service"

line_is 'COMFYUI_VERSION=v0.34.0' "$ROOT_DIR/scripts/comfyui-runtime.env"
line_is 'COMFYUI_COMMIT=12d5279438bfefc058a269eae805ceab6047777f' "$ROOT_DIR/scripts/comfyui-runtime.env"
line_is 'TORCH_VERSION=2.7.1' "$ROOT_DIR/scripts/comfyui-runtime.env"
line_is 'PYTORCH_CUDA_VERSION=12.6' "$ROOT_DIR/scripts/comfyui-runtime.env"
contains 'https://download.pytorch.org/whl/cu126' "$ROOT_DIR/scripts/comfyui-runtime.env"
line_is 'LLAMA_CPP_VERSION=v0.4.0' "$ROOT_DIR/scripts/prompt-runtime.env"
line_is 'LLAMA_CPP_COMMIT=427291b5b34cd914a31b3fd3b61a68f6184f4b9f' "$ROOT_DIR/scripts/prompt-runtime.env"
contains 'GGML_CUDA=OFF' "$ROOT_DIR/scripts/install-prompt-runtime"

contains 'stable_audio_3_medium_base.safetensors' "$ROOT_DIR/scripts/ooc-forge-audio-model-install"
contains 't5gemma_b_b_ul2.safetensors' "$ROOT_DIR/scripts/ooc-forge-audio-model-install"
contains 'c443fcc4d491475064cd0ff3eb92459b1e5f5060e86d96d016f048e528e24195' "$ROOT_DIR/scripts/ooc-forge-audio-model-install"
contains '1e1eba25be8872edb0d3c6335c6658fd6388e7b14b60da6e454e404cfcd8150e' "$ROOT_DIR/scripts/ooc-forge-audio-model-install"

contains 'ssh-keygen -A' "$ROOT_DIR/scripts/ooc-forge-appliance-policy"
contains 'deb https://deb.debian.org/debian trixie' "$ROOT_DIR/scripts/ooc-forge-appliance-policy"
contains 'iface lo inet loopback' "$ROOT_DIR/scripts/ooc-forge-appliance-policy"
contains 'managed=true' "$ROOT_DIR/scripts/ooc-forge-appliance-policy"
contains 'Before=NetworkManager.service ssh.service' "$ROOT_DIR/systemd/ooc-forge-appliance-policy.service"

contains 'grub-efi-amd64-signed' "$ROOT_DIR/.github/workflows/forge-iso.yml"
contains 'developer_git_update": True' "$ROOT_DIR/iso/build.sh"
contains 'models_bundled": False' "$ROOT_DIR/iso/build.sh"
contains '"bundled": True' "$ROOT_DIR/iso/build.sh"
contains '"ssh_server": True' "$ROOT_DIR/iso/build.sh"
contains 'sha256sum "${IMAGE_BASENAME}.iso" > "${IMAGE_BASENAME}.iso.sha256"' "$ROOT_DIR/iso/build.sh"
contains 'cd dist' "$ROOT_DIR/.github/workflows/forge-iso.yml"
contains 'sha256sum -c ooc-forge-*.iso.sha256' "$ROOT_DIR/.github/workflows/forge-iso.yml"
contains '/opt/ComfyUI/main.py' "$ROOT_DIR/.github/workflows/forge-iso.yml"
contains 'blacklist-nouveau.conf' "$ROOT_DIR/.github/workflows/forge-iso.yml"
contains 'ssh_host_' "$ROOT_DIR/.github/workflows/forge-iso.yml"

if grep -Fq 'sha256sum "$ISO_OUTPUT" > "$ISO_OUTPUT.sha256"' "$ROOT_DIR/iso/build.sh"; then
  echo "ISO checksum must not record an absolute container path." >&2
  exit 1
fi

for service in ooc-forge-prompt-model-install ooc-forge-video-model-install ooc-forge-audio-model-install; do
  if grep -Fq "systemctl enable $service.service" "$HOOK"; then
    echo "$service must remain on-demand, not boot-enabled." >&2
    exit 1
  fi
done

echo "OOC Forge ISO configuration valid."
