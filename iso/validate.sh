#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)

bash -n \
  "$ROOT_DIR/iso/build.sh" \
  "$ROOT_DIR/iso/inspect-boot.sh" \
  "$ROOT_DIR/iso/qemu-uefi-smoke.sh" \
  "$ROOT_DIR/iso/auto/config" \
  "$ROOT_DIR/iso/auto/build" \
  "$ROOT_DIR/iso/auto/clean" \
  "$ROOT_DIR/iso/config/hooks/live/010-ooc-forge-runtime.hook.chroot"

grep -Fq -- '--distribution trixie' "$ROOT_DIR/iso/auto/config"
grep -Fq -- '--architectures amd64' "$ROOT_DIR/iso/auto/config"
grep -Fq -- '--bootloaders "syslinux,grub-efi"' "$ROOT_DIR/iso/auto/config"
grep -Fq -- '--uefi-secure-boot disable' "$ROOT_DIR/iso/auto/config"
grep -Fq 'lb build noauto' "$ROOT_DIR/iso/auto/build"
grep -Fq 'lb clean noauto' "$ROOT_DIR/iso/auto/clean"
grep -Fxq 'nvidia-driver' "$ROOT_DIR/iso/config/package-lists/forge.list.chroot"
grep -Fxq 'git' "$ROOT_DIR/iso/config/package-lists/forge.list.chroot"
grep -Fq 'grub-efi-amd64-signed' "$ROOT_DIR/.github/workflows/forge-iso.yml"
grep -Fq 'ooc-forge-git-update.service' "$ROOT_DIR/iso/config/hooks/live/010-ooc-forge-runtime.hook.chroot"
grep -Fq 'ooc-forge-maintenance.sudoers' "$ROOT_DIR/iso/config/hooks/live/010-ooc-forge-runtime.hook.chroot"
grep -Fq 'developer_git_update": True' "$ROOT_DIR/iso/build.sh"
grep -Fq 'models_bundled": False' "$ROOT_DIR/iso/build.sh"
grep -Fq '"$ISO_DIR/inspect-boot.sh" "$ISO_OUTPUT"' "$ROOT_DIR/iso/build.sh"
grep -Fq 'OOC_FORGE_UEFI_BOOT_OK' "$ROOT_DIR/iso/config/hooks/live/010-ooc-forge-runtime.hook.chroot"
grep -Fq 'usb-storage' "$ROOT_DIR/iso/qemu-uefi-smoke.sh"
grep -Fq 'sha256sum "${IMAGE_BASENAME}.iso" > "${IMAGE_BASENAME}.iso.sha256"' "$ROOT_DIR/iso/build.sh"
grep -Fq 'cd dist' "$ROOT_DIR/.github/workflows/forge-iso.yml"
grep -Fq 'sha256sum -c ooc-forge-*.iso.sha256' "$ROOT_DIR/.github/workflows/forge-iso.yml"

if grep -Fq 'sha256sum "$ISO_OUTPUT" > "$ISO_OUTPUT.sha256"' "$ROOT_DIR/iso/build.sh"; then
  echo "ISO checksum must not record an absolute container path." >&2
  exit 1
fi

echo "OOC Forge ISO configuration valid."
