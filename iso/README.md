# OOC Forge ISO

This directory builds the OOC Forge appliance image from the canonical repository source using Debian `live-build`.

## v1 image boundary

The first image target is deliberately narrow:

- Debian 13 (Trixie), amd64;
- UEFI boot via GRUB EFI;
- Secure Boot disabled;
- Debian live environment plus Debian Installer;
- OOC Forge runtime preinstalled under `/opt/ooc-forge`;
- local `forge.local` service stack enabled;
- NVIDIA Debian driver included for the RTX 3090 reference machine;
- no model library in the ISO;
- no guessed ComfyUI/PyTorch/CUDA bundle until the reference RTX 3090 execution stack has been proven and pinned.

This means this slice establishes a bootable appliance base and reproducible packaging boundary. The pinned generation payload and custom Forge disk installer/recovery partition recipe are the next ISO slices.

## Build host

Use Debian 13 or a clean CI runner with `live-build`, `debootstrap`, `rsync`, `xorriso`, `squashfs-tools` and the usual GRUB EFI image tools installed.

```bash
sudo apt-get update
sudo apt-get install -y live-build debootstrap rsync xorriso squashfs-tools grub-efi-amd64-bin
sudo ./iso/build.sh
```

Outputs are written to `dist/`:

```text
ooc-forge-0.1.0-amd64.iso
ooc-forge-0.1.0-amd64.iso.sha256
ooc-forge-0.1.0-amd64.iso.manifest.json
```

The version is read from `pyproject.toml`. `OOC_FORGE_VERSION`, `OOC_FORGE_SOURCE_REF`, `OOC_FORGE_ISO_WORK` and `OOC_FORGE_DIST` can override build metadata/paths when required by CI.

## Validate without building

```bash
./iso/validate.sh
```

## Principles

The ISO is packaging, not a second Forge implementation. The source under `forge/`, `systemd/`, `nginx/` and `workflows/` remains canonical; `iso/build.sh` stages that source into the image at build time.

Large models are never committed to this repository or baked into the base ISO. Forge Data remains a separate persistence concern from the system image.
