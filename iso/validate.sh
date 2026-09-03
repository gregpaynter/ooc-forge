#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
GRUB_TEMPLATE="$ROOT_DIR/iso/config/bootloaders/grub-pc/grub.cfg"

bash -n \
  "$ROOT_DIR/iso/build.sh" \
  "$ROOT_DIR/iso/inspect-boot.sh" \
  "$ROOT_DIR/iso/inspect-grub.sh" \
  "$ROOT_DIR/iso/qemu-uefi-smoke.sh" \
  "$ROOT_DIR/iso/auto/config" \
  "$ROOT_DIR/iso/auto/build" \
  "$ROOT_DIR/iso/auto/clean" \
  "$ROOT_DIR/iso/config/hooks/live/010-ooc-forge-runtime.hook.chroot"

grep -Fq -- '--distribution trixie' "$ROOT_DIR/iso/auto/config"
grep -Fq -- '--architectures amd64' "$ROOT_DIR/iso/auto/config"
grep -Fq -- '--bootloaders "syslinux,grub-efi"' "$ROOT_DIR/iso/auto/config"
grep -Fq -- '--uefi-secure-boot disable' "$ROOT_DIR/iso/auto/config"
grep -Fq 'console=ttyS0,115200n8' "$ROOT_DIR/iso/auto/config"
grep -Fq 'ooc-forge.boot-smoke=1' "$ROOT_DIR/iso/auto/config"
grep -Fq 'lb build noauto' "$ROOT_DIR/iso/auto/build"
grep -Fq 'lb clean noauto' "$ROOT_DIR/iso/auto/clean"
grep -Fxq 'nvidia-driver' "$ROOT_DIR/iso/config/package-lists/forge.list.chroot"
grep -Fxq 'git' "$ROOT_DIR/iso/config/package-lists/forge.list.chroot"
grep -Fq 'grub-common' "$ROOT_DIR/.github/workflows/forge-iso.yml"
grep -Fq 'grub-efi-amd64-signed' "$ROOT_DIR/.github/workflows/forge-iso.yml"
grep -Fq 'ooc-forge-git-update.service' "$ROOT_DIR/iso/config/hooks/live/010-ooc-forge-runtime.hook.chroot"
grep -Fq 'ooc-forge-maintenance.sudoers' "$ROOT_DIR/iso/config/hooks/live/010-ooc-forge-runtime.hook.chroot"
grep -Fq 'developer_git_update": True' "$ROOT_DIR/iso/build.sh"
grep -Fq 'models_bundled": False' "$ROOT_DIR/iso/build.sh"
grep -Fq 'GRUB_FONT_SOURCE=/usr/share/grub/unicode.pf2' "$ROOT_DIR/iso/build.sh"
grep -Fq 'config/includes.binary/boot/grub/fonts' "$ROOT_DIR/iso/build.sh"
grep -Fq 'unicode.pf2' "$ROOT_DIR/iso/inspect-boot.sh"
grep -Fq 'bash "$ISO_DIR/inspect-boot.sh" "$ISO_OUTPUT"' "$ROOT_DIR/iso/build.sh"
grep -Fq 'bash "$ISO_DIR/inspect-grub.sh" "$ISO_OUTPUT"' "$ROOT_DIR/iso/build.sh"
grep -Fq 'OOC_FORGE_UEFI_BOOT_OK' "$ROOT_DIR/iso/config/hooks/live/010-ooc-forge-runtime.hook.chroot"
grep -Fq 'usb-storage' "$ROOT_DIR/iso/qemu-uefi-smoke.sh"
grep -Fq 'OOC_FORGE_GRUB_READY' "$ROOT_DIR/iso/qemu-uefi-smoke.sh"
grep -Fq 'OOC_FORGE_GRUB_READY' "$GRUB_TEMPLATE"
grep -Fxq 'set default=0' "$GRUB_TEMPLATE"
grep -Eq '^set timeout=[1-9][0-9]*$' "$GRUB_TEMPLATE"
grep -Fq 'terminal_output console serial' "$GRUB_TEMPLATE"
grep -Fxq '@LINUX_LIVE@' "$GRUB_TEMPLATE"
PLACEHOLDER_COUNT=$(grep -Fo '@LINUX_LIVE@' "$GRUB_TEMPLATE" | wc -l | tr -d '[:space:]')
if [[ "$PLACEHOLDER_COUNT" != "1" ]]; then
  echo "GRUB template must contain exactly one live-build injection token; found $PLACEHOLDER_COUNT." >&2
  exit 1
fi
grep -Fq 'sha256sum "${IMAGE_BASENAME}.iso" > "${IMAGE_BASENAME}.iso.sha256"' "$ROOT_DIR/iso/build.sh"
grep -Fq 'cd dist' "$ROOT_DIR/.github/workflows/forge-iso.yml"
grep -Fq 'sha256sum -c ooc-forge-*.iso.sha256' "$ROOT_DIR/.github/workflows/forge-iso.yml"

if grep -Fq 'sha256sum "$ISO_OUTPUT" > "$ISO_OUTPUT.sha256"' "$ROOT_DIR/iso/build.sh"; then
  echo "ISO checksum must not record an absolute container path." >&2
  exit 1
fi

echo "OOC Forge ISO configuration valid."
