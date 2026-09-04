#!/usr/bin/env bash
set -euo pipefail

if [[ ${EUID:-$(id -u)} -ne 0 ]]; then
  echo "OOC Forge ISO builds require root. Run: sudo ./iso/build.sh" >&2
  exit 1
fi

ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
ISO_DIR="$ROOT_DIR/iso"
WORK_DIR=${OOC_FORGE_ISO_WORK:-"$ROOT_DIR/build/iso"}
DIST_DIR=${OOC_FORGE_DIST:-"$ROOT_DIR/dist"}
# shellcheck source=/dev/null
source "$ROOT_DIR/scripts/comfyui-runtime.env"

read_project_version() {
  python3 - "$ROOT_DIR/pyproject.toml" <<'PY'
import pathlib
import sys
import tomllib

project = tomllib.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
print(project["project"]["version"])
PY
}

VERSION=${OOC_FORGE_VERSION:-$(read_project_version)}
ARCH=${OOC_FORGE_ARCH:-amd64}
SOURCE_REF=${OOC_FORGE_SOURCE_REF:-$(git -C "$ROOT_DIR" rev-parse HEAD 2>/dev/null || echo unknown)}
SOURCE_DATE_EPOCH=${SOURCE_DATE_EPOCH:-$(git -C "$ROOT_DIR" show -s --format=%ct HEAD 2>/dev/null || date +%s)}
export SOURCE_DATE_EPOCH

IMAGE_BASENAME="ooc-forge-${VERSION}-${ARCH}"

rm -rf "$WORK_DIR"
mkdir -p "$WORK_DIR" "$DIST_DIR"
cp -a "$ISO_DIR/auto" "$WORK_DIR/auto"
cp -a "$ISO_DIR/config" "$WORK_DIR/config"

# Debian live-build's GRUB menu references this font from the binary medium.
GRUB_FONT_SOURCE=/usr/share/grub/unicode.pf2
if [[ ! -r "$GRUB_FONT_SOURCE" ]]; then
  echo "GRUB Unicode font not found at $GRUB_FONT_SOURCE; install grub-common." >&2
  exit 1
fi
mkdir -p "$WORK_DIR/config/includes.binary/boot/grub/fonts"
cp -L "$GRUB_FONT_SOURCE" \
  "$WORK_DIR/config/includes.binary/boot/grub/fonts/unicode.pf2"

mkdir -p "$WORK_DIR/config/includes.chroot/etc/ooc-forge"
mkdir -p "$WORK_DIR/config/includes.chroot/opt/ooc-forge"
rsync -a --delete \
  --exclude '.git/' \
  --exclude '.github/' \
  --exclude 'build/' \
  --exclude 'dist/' \
  --exclude 'iso/' \
  --exclude '.venv/' \
  "$ROOT_DIR/" "$WORK_DIR/config/includes.chroot/opt/ooc-forge/"

cat > "$WORK_DIR/config/includes.chroot/etc/ooc-forge/image-build.env" <<ENV
OOC_FORGE_VERSION=$VERSION
OOC_FORGE_SOURCE_REF=$SOURCE_REF
OOC_FORGE_SOURCE_DATE_EPOCH=$SOURCE_DATE_EPOCH
OOC_FORGE_BASE=debian-trixie
OOC_FORGE_ARCH=$ARCH
COMFYUI_VERSION=$COMFYUI_VERSION
COMFYUI_COMMIT=$COMFYUI_COMMIT
TORCH_VERSION=$TORCH_VERSION
PYTORCH_CUDA_VERSION=$PYTORCH_CUDA_VERSION
ENV

printf '%s\n' "$SOURCE_REF" > "$WORK_DIR/config/includes.chroot/opt/ooc-forge/.ooc-source-ref"

cd "$WORK_DIR"
./auto/config
./auto/build

ISO_SOURCE=$(find . -maxdepth 1 -type f \( -name 'live-image-*.hybrid.iso' -o -name 'live-image-*.iso' \) -print -quit)
if [[ -z "$ISO_SOURCE" ]]; then
  echo "live-build completed without producing an ISO" >&2
  exit 1
fi

ISO_OUTPUT="$DIST_DIR/${IMAGE_BASENAME}.iso"
cp "$ISO_SOURCE" "$ISO_OUTPUT"
bash "$ISO_DIR/inspect-boot.sh" "$ISO_OUTPUT" | tee "$ISO_OUTPUT.boot-report.txt"
bash "$ISO_DIR/inspect-grub.sh" "$ISO_OUTPUT" | tee "$ISO_OUTPUT.grub-report.txt"
(
  cd "$DIST_DIR"
  sha256sum "${IMAGE_BASENAME}.iso" > "${IMAGE_BASENAME}.iso.sha256"
)

python3 - \
  "$ISO_OUTPUT" "$VERSION" "$ARCH" "$SOURCE_REF" "$SOURCE_DATE_EPOCH" \
  "$COMFYUI_VERSION" "$COMFYUI_COMMIT" "$TORCH_VERSION" "$PYTORCH_CUDA_VERSION" <<'PY'
import hashlib
import json
import pathlib
import subprocess
import sys

(
    iso_arg,
    version,
    arch,
    source_ref,
    source_date_epoch,
    comfyui_version,
    comfyui_commit,
    torch_version,
    cuda_version,
) = sys.argv[1:]
iso_path = pathlib.Path(iso_arg)
digest = hashlib.sha256(iso_path.read_bytes()).hexdigest()
try:
    live_build_version = subprocess.check_output(["lb", "--version"], text=True).strip()
except Exception:
    live_build_version = "unknown"
manifest = {
    "schema": "ooc.forge.iso-build.v1",
    "artifact": iso_path.name,
    "version": version,
    "architecture": arch,
    "base": "debian-trixie",
    "source_ref": source_ref,
    "source_date_epoch": int(source_date_epoch),
    "sha256": digest,
    "bytes": iso_path.stat().st_size,
    "live_build": live_build_version,
    "secure_boot": False,
    "models_bundled": False,
    "developer_git_update": True,
    "ssh_server": True,
    "apt_sources_managed": True,
    "network_manager_owned": True,
    "nouveau_blacklisted": True,
    "execution_stack": {
        "bundled": True,
        "comfyui_version": comfyui_version,
        "comfyui_commit": comfyui_commit,
        "torch_version": torch_version,
        "cuda_runtime": cuda_version,
        "listen": "127.0.0.1:8188",
    },
    "usb_boot": {
        "hybrid_mbr": True,
        "gpt": True,
        "efi_system_partition": True,
        "uefi_el_torito": True,
        "qemu_ovmf_smoke_required_in_ci": True,
    },
}
iso_path.with_suffix(iso_path.suffix + ".manifest.json").write_text(
    json.dumps(manifest, indent=2, sort_keys=True) + "\n",
    encoding="utf-8",
)
PY

printf '\nBuilt OOC Forge ISO:\n  %s\n  %s\n  %s\n  %s\n  %s\n' \
  "$ISO_OUTPUT" "$ISO_OUTPUT.sha256" "$ISO_OUTPUT.manifest.json" \
  "$ISO_OUTPUT.boot-report.txt" "$ISO_OUTPUT.grub-report.txt"
