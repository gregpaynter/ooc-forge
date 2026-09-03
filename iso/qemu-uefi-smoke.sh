#!/usr/bin/env bash
set -euo pipefail

usage() {
  echo "Usage: $0 PATH_TO_ISO" >&2
  exit 2
}

[[ $# -eq 1 ]] || usage
ISO_PATH=$(readlink -f "$1")
[[ -s "$ISO_PATH" ]] || { echo "ISO not found or empty: $ISO_PATH" >&2; exit 1; }
command -v qemu-system-x86_64 >/dev/null || { echo "qemu-system-x86_64 is required" >&2; exit 1; }

# OVMF is flash firmware, not a legacy PC BIOS. In particular, Ubuntu's
# OVMF_CODE_4M.fd is intended to be paired with a writable VARS image and
# attached as pflash. Loading the 4 MiB image through QEMU's -bios option can
# fail before firmware execution begins.
OVMF_CODE=
OVMF_VARS=
for pair in \
  "/usr/share/OVMF/OVMF_CODE_4M.fd|/usr/share/OVMF/OVMF_VARS_4M.fd" \
  "/usr/share/OVMF/OVMF_CODE.fd|/usr/share/OVMF/OVMF_VARS.fd" \
  "/usr/share/edk2/ovmf/OVMF_CODE.fd|/usr/share/edk2/ovmf/OVMF_VARS.fd" \
  "/usr/share/edk2/x64/OVMF_CODE.4m.fd|/usr/share/edk2/x64/OVMF_VARS.4m.fd"; do
  code=${pair%%|*}
  vars=${pair#*|}
  if [[ -r "$code" && -r "$vars" ]]; then
    OVMF_CODE=$code
    OVMF_VARS=$vars
    break
  fi
done
[[ -n "$OVMF_CODE" && -n "$OVMF_VARS" ]] || {
  echo "A matching OVMF CODE/VARS firmware pair was not found" >&2
  exit 1
}

RUN_DIR=$(mktemp -d)
SERIAL_LOG="$RUN_DIR/serial.log"
QEMU_LOG="$RUN_DIR/qemu.log"
OVMF_VARS_RW="$RUN_DIR/OVMF_VARS.fd"
cp "$OVMF_VARS" "$OVMF_VARS_RW"
chmod u+w "$OVMF_VARS_RW"

QEMU_PID=
cleanup() {
  if [[ -n "$QEMU_PID" ]] && kill -0 "$QEMU_PID" 2>/dev/null; then
    kill "$QEMU_PID" 2>/dev/null || true
    wait "$QEMU_PID" 2>/dev/null || true
  fi
  rm -rf "$RUN_DIR"
}
trap cleanup EXIT

qemu-system-x86_64 \
  -machine q35,accel=tcg \
  -cpu max \
  -m 2048 \
  -display none \
  -monitor none \
  -serial "file:$SERIAL_LOG" \
  -drive "if=pflash,format=raw,unit=0,readonly=on,file=$OVMF_CODE" \
  -drive "if=pflash,format=raw,unit=1,file=$OVMF_VARS_RW" \
  -drive "if=none,id=forge_usb,file=$ISO_PATH,format=raw,readonly=on" \
  -device qemu-xhci,id=xhci \
  -device usb-storage,bus=xhci.0,drive=forge_usb,bootindex=1 \
  -no-reboot \
  >"$QEMU_LOG" 2>&1 &
QEMU_PID=$!

for _ in $(seq 1 240); do
  if grep -Fq 'OOC_FORGE_UEFI_BOOT_OK' "$SERIAL_LOG" 2>/dev/null; then
    echo "OOC Forge UEFI USB boot smoke test passed."
    exit 0
  fi
  if ! kill -0 "$QEMU_PID" 2>/dev/null; then
    echo "QEMU exited before the boot marker was emitted." >&2
    sed -n '1,240p' "$QEMU_LOG" >&2
    sed -n '1,240p' "$SERIAL_LOG" >&2
    exit 1
  fi
  sleep 1
done

echo "Timed out waiting for OOC Forge UEFI boot marker." >&2
sed -n '1,240p' "$QEMU_LOG" >&2
sed -n '1,240p' "$SERIAL_LOG" >&2
exit 1
