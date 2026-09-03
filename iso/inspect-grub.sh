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

REPORT_PATH="${ISO_PATH}.grub-report.txt"
RUN_DIR=$(mktemp -d)
trap 'rm -rf "$RUN_DIR"' EXIT
GRUB_TREE="$RUN_DIR/boot/grub"
mkdir -p "$(dirname "$GRUB_TREE")"

# Keep every xorriso diagnostic and every assertion in the uploaded report while
# also showing it directly in the Actions log.
exec > >(tee "$REPORT_PATH") 2>&1

printf '%s\n' '=== OOC Forge generated GRUB inspection ==='
printf 'ISO: %s\n' "$ISO_PATH"
printf '%s\n' '=== extracting /boot/grub recursively ==='
if ! xorriso -osirrox on -indev "$ISO_PATH" -extract /boot/grub "$GRUB_TREE"; then
  status=$?
  printf '%s\n' 'ERROR: xorriso could not extract /boot/grub.'
  printf '%s\n' '=== /boot directory reported by xorriso ==='
  xorriso -indev "$ISO_PATH" -ls /boot || true
  printf '%s\n' '=== /boot/grub directory reported by xorriso ==='
  xorriso -indev "$ISO_PATH" -ls /boot/grub || true
  printf '%s\n' '=== any partially extracted /boot/grub content ==='
  if [[ -d "$GRUB_TREE" ]]; then
    find "$GRUB_TREE" -maxdepth 4 -printf '%y %P\n' | sort
  else
    printf '%s\n' '(none)'
  fi
  exit "$status"
fi

printf '%s\n' '=== actual /boot/grub tree in the generated ISO ==='
find "$GRUB_TREE" -maxdepth 4 -printf '%y %P\n' | sort

mapfile -t CFG_FILES < <(find "$GRUB_TREE" -type f -name '*.cfg' -print | sort)
if [[ ${#CFG_FILES[@]} -eq 0 ]]; then
  echo 'Generated /boot/grub contains no .cfg files' >&2
  exit 1
fi

printf '%s\n' '=== generated GRUB configuration files ==='
for cfg in "${CFG_FILES[@]}"; do
  rel="${cfg#${RUN_DIR}}"
  printf '%s\n' "--- ${rel} ---"
  cat "$cfg"
done

GRUB_CFG="$GRUB_TREE/grub.cfg"
[[ -s "$GRUB_CFG" ]] || {
  echo 'Generated /boot/grub/grub.cfg is missing or empty' >&2
  exit 1
}

# The top-level entry point must remain deterministic and observable over COM1.
grep -Eq '^set[[:space:]]+default=0$' "$GRUB_CFG" || {
  echo 'GRUB default entry is not explicitly index 0' >&2
  exit 1
}
grep -Eq '^set[[:space:]]+timeout=[1-9][0-9]*$' "$GRUB_CFG" || {
  echo 'GRUB does not have a finite positive timeout' >&2
  exit 1
}
grep -Eq '^serial .*--unit=0 .*--speed=115200' "$GRUB_CFG" || {
  echo 'GRUB COM1 serial diagnostics are not configured at 115200 baud' >&2
  exit 1
}
grep -Fq 'terminal_output console serial' "$GRUB_CFG" || {
  echo 'GRUB serial output is not enabled' >&2
  exit 1
}
grep -Fq 'OOC_FORGE_GRUB_READY' "$GRUB_CFG" || {
  echo 'GRUB serial readiness marker is missing' >&2
  exit 1
}

# Do not assume live-build emits a particular filename such as live.cfg. Locate
# the actual generated menu configuration in this ISO, then validate the entry
# that index 0 will execute.
BOOT_CFG=''
for cfg in "${CFG_FILES[@]}"; do
  if grep -Eq '^[[:space:]]*menuentry[[:space:]]' "$cfg" && \
     grep -Fq 'ooc-forge.boot-smoke=1' "$cfg"; then
    BOOT_CFG="$cfg"
    break
  fi
done

if [[ -z "$BOOT_CFG" ]]; then
  echo 'No generated GRUB menu configuration contains ooc-forge.boot-smoke=1.' >&2
  echo 'The actual generated GRUB structure is shown above; refusing to run QEMU.' >&2
  printf '%s\n' '=== relevant generated GRUB lines ==='
  grep -RInE 'menuentry|linux|initrd|boot-smoke|console=ttyS0|source|configfile' "$GRUB_TREE" || true
  exit 1
fi

BOOT_CFG_REL="${BOOT_CFG#${RUN_DIR}}"
printf 'Detected generated live menu configuration: %s\n' "$BOOT_CFG_REL"

if [[ "$BOOT_CFG" != "$GRUB_CFG" ]]; then
  boot_cfg_name=$(basename "$BOOT_CFG")
  grep -Fq "$boot_cfg_name" "$GRUB_CFG" || {
    echo "Top-level grub.cfg does not reference detected live menu ${boot_cfg_name}" >&2
    exit 1
  }
fi

FIRST_ENTRY="$RUN_DIR/first-menuentry.cfg"
awk '
  /^[[:space:]]*menuentry[[:space:]]/ {
    if (found) exit
    found=1
  }
  found { print }
' "$BOOT_CFG" > "$FIRST_ENTRY"

[[ -s "$FIRST_ENTRY" ]] || {
  echo "Detected live menu ${BOOT_CFG_REL} has no menuentry" >&2
  exit 1
}

printf '%s\n' '=== default/index-0 generated live menu entry ==='
cat "$FIRST_ENTRY"

grep -Fq 'ooc-forge.boot-smoke=1' "$FIRST_ENTRY" || {
  echo 'Default/index-0 live entry is missing ooc-forge.boot-smoke=1' >&2
  exit 1
}
grep -Fq 'console=ttyS0,115200n8' "$FIRST_ENTRY" || {
  echo 'Default/index-0 live entry is missing the serial kernel console' >&2
  exit 1
}
grep -Eq '^[[:space:]]*linux[[:space:]]+/live/' "$FIRST_ENTRY" || {
  echo 'Default/index-0 live entry does not load a /live kernel' >&2
  exit 1
}
grep -Eq '^[[:space:]]*initrd[[:space:]]+/live/' "$FIRST_ENTRY" || {
  echo 'Default/index-0 live entry does not load a /live initrd' >&2
  exit 1
}

printf '%s\n' '=== assertions ==='
printf '%s\n' \
  'generated_grub_tree_discovered=true' \
  'default_entry=0' \
  'finite_timeout=true' \
  'grub_serial_115200=true' \
  'grub_serial_output=true' \
  'grub_ready_marker=true' \
  "live_menu=${BOOT_CFG_REL}" \
  'default_live_entry_verified=true' \
  'kernel_serial_console=true' \
  'boot_smoke_flag=true'
