#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)

bash -n \
  "$ROOT_DIR/iso/build.sh" \
  "$ROOT_DIR/iso/auto/config" \
  "$ROOT_DIR/iso/auto/build" \
  "$ROOT_DIR/iso/auto/clean" \
  "$ROOT_DIR/iso/config/hooks/live/010-ooc-forge-runtime.hook.chroot" \
  "$ROOT_DIR/scripts/install-comfyui-runtime" \
  "$ROOT_DIR/scripts/ooc-forge-appliance-policy"

grep -Fq -- '--distribution trixie' "$ROOT_DIR/iso/auto/config"
grep -Fq -- '--architectures amd64' "$ROOT_DIR/iso/auto/config"
grep -Fq -- '--bootloaders "syslinux,grub-efi"' "$ROOT_DIR/iso/auto/config"
grep -Fq -- '--uefi-secure-boot disable' "$ROOT_DIR/iso/auto/config"
grep -Fq 'lb build noauto' "$ROOT_DIR/iso/auto/build"
grep -Fq 'lb clean noauto' "$ROOT_DIR/iso/auto/clean"

for package in nvidia-driver linux-headers-amd64 dkms kmod openssh-server network-manager ffmpeg; do
  grep -Fxq "$package" "$ROOT_DIR/iso/config/package-lists/forge.list.chroot"
done

grep -Fq 'blacklist nouveau' "$ROOT_DIR/iso/config/hooks/live/010-ooc-forge-runtime.hook.chroot"
grep -Fq 'update-initramfs -u -k all' "$ROOT_DIR/iso/config/hooks/live/010-ooc-forge-runtime.hook.chroot"
grep -Fq 'install-comfyui-runtime' "$ROOT_DIR/iso/config/hooks/live/010-ooc-forge-runtime.hook.chroot"
grep -Fq 'systemctl enable comfyui.service' "$ROOT_DIR/iso/config/hooks/live/010-ooc-forge-runtime.hook.chroot"
grep -Fq 'systemctl enable ssh.service' "$ROOT_DIR/iso/config/hooks/live/010-ooc-forge-runtime.hook.chroot"
grep -Fq 'rm -f /etc/ssh/ssh_host_' "$ROOT_DIR/iso/config/hooks/live/010-ooc-forge-runtime.hook.chroot"
grep -Fq 'ooc-forge-appliance-policy.service' "$ROOT_DIR/iso/config/hooks/live/010-ooc-forge-runtime.hook.chroot"
grep -Fq '127.0.0.1 --port 8188' "$ROOT_DIR/systemd/comfyui.service"
grep -Fq 'XDG_CACHE_HOME=/forge-data/cache' "$ROOT_DIR/systemd/comfyui.service"

grep -Fxq 'COMFYUI_VERSION=v0.34.0' "$ROOT_DIR/scripts/comfyui-runtime.env"
grep -Fxq 'COMFYUI_COMMIT=12d5279438bfefc058a269eae805ceab6047777f' "$ROOT_DIR/scripts/comfyui-runtime.env"
grep -Fxq 'TORCH_VERSION=2.7.1' "$ROOT_DIR/scripts/comfyui-runtime.env"
grep -Fxq 'PYTORCH_CUDA_VERSION=12.6' "$ROOT_DIR/scripts/comfyui-runtime.env"
grep -Fq 'https://download.pytorch.org/whl/cu126' "$ROOT_DIR/scripts/comfyui-runtime.env"

grep -Fq 'ssh-keygen -A' "$ROOT_DIR/scripts/ooc-forge-appliance-policy"
grep -Fq 'deb https://deb.debian.org/debian trixie' "$ROOT_DIR/scripts/ooc-forge-appliance-policy"
grep -Fq 'iface lo inet loopback' "$ROOT_DIR/scripts/ooc-forge-appliance-policy"
grep -Fq 'managed=true' "$ROOT_DIR/scripts/ooc-forge-appliance-policy"
grep -Fq 'Before=NetworkManager.service ssh.service' "$ROOT_DIR/systemd/ooc-forge-appliance-policy.service"

grep -Fq 'grub-efi-amd64-signed' "$ROOT_DIR/.github/workflows/forge-iso.yml"
grep -Fq 'ooc-forge-git-update.service' "$ROOT_DIR/iso/config/hooks/live/010-ooc-forge-runtime.hook.chroot"
grep -Fq 'ooc-forge-maintenance.sudoers' "$ROOT_DIR/iso/config/hooks/live/010-ooc-forge-runtime.hook.chroot"
grep -Fq 'developer_git_update": True' "$ROOT_DIR/iso/build.sh"
grep -Fq 'models_bundled": False' "$ROOT_DIR/iso/build.sh"
grep -Fq '"bundled": True' "$ROOT_DIR/iso/build.sh"
grep -Fq '"ssh_server": True' "$ROOT_DIR/iso/build.sh"
grep -Fq 'sha256sum "${IMAGE_BASENAME}.iso" > "${IMAGE_BASENAME}.iso.sha256"' "$ROOT_DIR/iso/build.sh"
grep -Fq 'cd dist' "$ROOT_DIR/.github/workflows/forge-iso.yml"
grep -Fq 'sha256sum -c ooc-forge-*.iso.sha256' "$ROOT_DIR/.github/workflows/forge-iso.yml"
grep -Fq '/opt/ComfyUI/main.py' "$ROOT_DIR/.github/workflows/forge-iso.yml"
grep -Fq 'blacklist-nouveau.conf' "$ROOT_DIR/.github/workflows/forge-iso.yml"
grep -Fq 'ssh_host_' "$ROOT_DIR/.github/workflows/forge-iso.yml"

if grep -Fq 'sha256sum "$ISO_OUTPUT" > "$ISO_OUTPUT.sha256"' "$ROOT_DIR/iso/build.sh"; then
  echo "ISO checksum must not record an absolute container path." >&2
  exit 1
fi

echo "OOC Forge ISO configuration valid."
