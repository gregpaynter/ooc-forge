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
GRUB_FONT_REPORT="$REPORT_DIR/grub-font.txt"

xorriso -indev "$ISO_PATH" -report_system_area plain 2>&1 | tee "$SYSTEM_REPORT"
xorriso -indev "$ISO_PATH" -report_el_torito plain 2>&1 | tee "$EL_TORITO_REPORT"
fdisk -l "$ISO_PATH" 2>&1 | tee "$FDISK_REPORT"
xorriso -indev "$ISO_PATH" \
  -find /boot/grub/fonts/unicode.pf2 -type f -print \
  2>&1 | tee "$GRUB_FONT_REPORT"

require_match() {
  local pattern=$1
  local file=$2
  local description=$3
  if ! grep -Eiq -- "$pattern" "$file"; then
    echo "USB boot validation failed: missing $description" >&2
    exit 1
  fi
}

# Debian/xorriso hybrid images deliberately overlap ISO and EFI data. Depending
# on xorriso's representation, the FAT EFI image may be published as MBR type
# 0xef while its GPT entry is Basic Data. Accept the concrete firmware-visible
# evidence instead of requiring a particular human-readable GPT label.
require_match 'System area summary:.*MBR|MBR partition table|isohybrid' "$SYSTEM_REPORT" 'hybrid MBR metadata'
require_match 'System area summary:.*GPT|GPT disk GUID|GPT partition' "$SYSTEM_REPORT" 'GPT metadata'
require_match 'MBR partition[[:space:]]*:.*0xef|GPT partition path[[:space:]]*:.*(/boot/grub/)?efi\.img|GPT partname local[[:space:]]*:.*EFI' "$SYSTEM_REPORT" 'firmware-visible EFI boot partition/image'
require_match 'El Torito boot img[[:space:]]*:.*UEFI|UEFI|Platform Id[[:space:]]+0xef|platform_id=0xef' "$EL_TORITO_REPORT" 'UEFI El Torito boot entry'
require_match 'Disklabel type:[[:space:]]+(gpt|dos)' "$FDISK_REPORT" 'readable partition table'
require_match '/boot/grub/fonts/unicode\.pf2' "$GRUB_FONT_REPORT" 'GRUB Unicode font'

echo "OOC Forge hybrid USB layout valid: MBR + GPT + EFI boot partition/image + UEFI boot entry + GRUB runtime font."
