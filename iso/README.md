# OOC Forge ISO

This directory builds the OOC Forge appliance image from the canonical repository source using Debian `live-build`.

## v1 image boundary

The first image target is deliberately narrow:

- Debian 13 (Trixie), amd64;
- hybrid GPT/MBR layout for raw USB flashing;
- an EFI System Partition and UEFI boot via GRUB EFI;
- Secure Boot disabled;
- Debian live environment plus Debian Installer;
- OOC Forge runtime preinstalled under `/opt/ooc-forge`;
- local `forge.local` service stack enabled;
- NVIDIA Debian driver included for the RTX 3090 reference machine;
- Developer/Maintenance Git update capability installed but never automatic;
- no model library in the ISO;
- no guessed ComfyUI/PyTorch/CUDA bundle until the reference RTX 3090 execution stack has been proven and pinned.

This slice establishes a bootable appliance base and reproducible packaging boundary. The pinned generation payload and custom Forge disk installer/recovery partition recipe are subsequent v1 slices.

## Build host

Use Debian 13 with `live-build`, `debootstrap`, `rsync`, `xorriso`, `squashfs-tools`, Syslinux/ISOLINUX, and the GRUB common, BIOS and EFI builder packages installed. `grub-efi-amd64-signed` is required by Debian live-build's EFI image assembly even though OOC Forge v1 deliberately leaves Secure Boot disabled. `grub-common` supplies the canonical `unicode.pf2` font, which the build stages into `/boot/grub/fonts/unicode.pf2` on the finished ISO because the GRUB boot menu references that path.

```bash
sudo apt-get update
sudo apt-get install -y live-build debootstrap rsync xorriso squashfs-tools grub-common grub-efi-amd64-bin grub-efi-amd64-signed grub-pc-bin isolinux syslinux-common syslinux-utils fdisk
sudo ./iso/build.sh
```

Outputs are written to `dist/`:

```text
ooc-forge-0.1.0-amd64.iso
ooc-forge-0.1.0-amd64.iso.sha256
ooc-forge-0.1.0-amd64.iso.manifest.json
ooc-forge-0.1.0-amd64.iso.boot-report.txt
```

The manifest records the exact source ref installed in the image and whether Developer/Maintenance Git updating is present. The build fails unless xorriso confirms hybrid MBR and GPT metadata, an EFI System Partition, a UEFI El Torito entry and the GRUB Unicode font required by the boot menu. CI also presents the image to QEMU as raw USB mass storage under OVMF and requires an in-guest boot marker; an `.iso` or `.hybrid.iso` filename is never treated as evidence of USB bootability.

## Validate without building

```bash
./iso/validate.sh
```

## Principles

The ISO is packaging, not a second Forge implementation. Source under `forge/`, `systemd/`, `nginx/` and `workflows/` remains canonical and is staged into the image at build time.

Large models are never committed to this repository or baked into the base ISO. Forge Data remains separate from the system image. Normal production updates remain signed/versioned Forge releases; Git updates are supervised Developer/Maintenance operations only.
