#!/usr/bin/env bash
set -euo pipefail

usage() {
  echo "Usage: $0 PATH_TO_ISO" >&2
  exit 2
}

[[ $# -eq 1 ]] || usage
ISO_PATH=$1
[[ -s "$ISO_PATH" ]] || { echo "ISO not found or empty: $ISO_PATH" >&2; exit 1; }

for command in xorriso fdisk; do
  command -v "$command" >/dev/null || { echo "Required command not found: $command" >&2; exit 1; }
done

REPORT_DIR=$(mktemp -d)
trap 'rm -rf "$REPORT_DIR"' EXIT
SYSTEM_REPORT="$REPORT_DIR/system-area.txt"
EL_TORITO_REPORT="$REPORT_DIR/el-torito.txt"
FDISK_REPORT="$REPORT_DIR/fdisk.txt"

xorriso -indev "$ISO_PATH" -report_system_area plain 2>&1 | tee "$SYSTEM_REPORT"
xorriso -indev "$ISO_PATH" -report_el_torito plain 2>&1 | tee "$EL_TORITO_REPORT"
fdisk -l "$ISO_PATH" 2>&1 | tee "$FDISK_REPORT"

require_match() {
  local pattern=$1
  local file=$2
  local description=$3
  if ! grep -Eiq -- "$pattern" "$file"; then
    echo "USB boot validation failed: missing $description" >&2
    exit 1
  fi
}

require_match 'MBR partition table|isohybrid-mbr|MBR:' "$SYSTEM_REPORT" 'hybrid MBR metadata'
require_match 'GPT partition|GPT:' "$SYSTEM_REPORT" 'GPT metadata'
require_match 'EFI.*(boot|system)|C12A7328-F81F-11D2-BA4B-00A0C93EC93B' "$SYSTEM_REPORT" 'EFI System Partition'
require_match 'UEFI|Platform Id[[:space:]]+0xef|platform_id=0xef' "$EL_TORITO_REPORT" 'UEFI El Torito boot entry'
require_match 'Disklabel type:[[:space:]]+(gpt|dos)' "$FDISK_REPORT" 'readable partition table'

echo "OOC Forge hybrid USB layout valid: MBR + GPT + ESP + UEFI boot entry."
