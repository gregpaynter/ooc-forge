#!/usr/bin/env bash
set -euo pipefail

usage() {
  echo "Usage: $0 PATH_TO_ISO" >&2
  exit 2
}

[[ $# -eq 1 ]] || usage
ISO_PATH=$(readlink -f "$1")
[[ -s "$ISO_PATH" ]] || { echo "ISO not found or empty: $ISO_PATH" >&2; exit 1; }
command -v xorriso >/dev/null || { echo "xorriso is required" >&2; exit 1; }

RUN_DIR=$(mktemp -d)
trap 'rm -rf "$RUN_DIR"' EXIT
GRUB_CFG="$RUN_DIR/grub.cfg"
LIVE_CFG="$RUN_DIR/live.cfg"

xorriso -osirrox on -indev "$ISO_PATH" \
  -extract /boot/grub/grub.cfg "$GRUB_CFG" >/dev/null 2>&1
xorriso -osirrox on -indev "$ISO_PATH" \
  -extract /boot/grub/live.cfg "$LIVE_CFG" >/dev/null 2>&1

[[ -s "$GRUB_CFG" ]] || { echo "Generated /boot/grub/grub.cfg is missing or empty" >&2; exit 1; }
[[ -s "$LIVE_CFG" ]] || { echo "Generated /boot/grub/live.cfg is missing or empty" >&2; exit 1; }

grep -Eq '^set[[:space:]]+default=0$' "$GRUB_CFG" || {
  echo "GRUB default entry is not explicitly index 0" >&2
  exit 1
}
grep -Eq '^set[[:space:]]+timeout=[1-9][0-9]*$' "$GRUB_CFG" || {
  echo "GRUB does not have a finite positive timeout" >&2
  exit 1
}
grep -Fq 'terminal_output console serial' "$GRUB_CFG" || {
  echo "GRUB serial output is not enabled" >&2
  exit 1
}
grep -Fq 'OOC_FORGE_GRUB_READY' "$GRUB_CFG" || {
  echo "GRUB serial readiness marker is missing" >&2
  exit 1
}
grep -Fq 'source /boot/grub/live.cfg' "$GRUB_CFG" || {
  echo "GRUB does not source the generated live menu" >&2
  exit 1
}

grep -Fq 'ooc-forge.boot-smoke=1' "$LIVE_CFG" || {
  echo "Generated live menu is missing ooc-forge.boot-smoke=1" >&2
  exit 1
}
grep -Fq 'console=ttyS0,115200n8' "$LIVE_CFG" || {
  echo "Generated live menu is missing the serial kernel console" >&2
  exit 1
}
grep -Eq '^[[:space:]]*linux[[:space:]]+/live/' "$LIVE_CFG" || {
  echo "Generated live menu does not load a /live kernel" >&2
  exit 1
}
grep -Eq '^[[:space:]]*initrd[[:space:]]+/live/' "$LIVE_CFG" || {
  echo "Generated live menu does not load a /live initrd" >&2
  exit 1
}

printf '%s\n' '=== /boot/grub/grub.cfg ==='
cat "$GRUB_CFG"
printf '%s\n' '=== /boot/grub/live.cfg ==='
cat "$LIVE_CFG"
printf '%s\n' '=== assertions ==='
printf '%s\n' \
  'default_entry=0' \
  'finite_timeout=true' \
  'grub_serial_output=true' \
  'grub_ready_marker=true' \
  'live_menu_sourced=true' \
  'kernel_serial_console=true' \
  'boot_smoke_flag=true'
