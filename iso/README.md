# OOC Forge ISO

This directory builds the OOC Forge appliance image from the canonical repository source using Debian `live-build`.

## v1 image boundary

The OOC Forge v1 image is now a complete RTX 3090 appliance base rather than a shell-repair starting point:

- Debian 13 (Trixie), amd64;
- hybrid GPT/MBR layout for raw USB flashing;
- an EFI System Partition and UEFI boot via GRUB EFI;
- Secure Boot disabled;
- Debian live environment plus Debian Installer;
- OOC Forge runtime preinstalled under `/opt/ooc-forge`;
- local `forge.local` service stack enabled;
- NVIDIA Debian driver, DKMS and matching kernel headers for the RTX 3090 reference machine;
- Nouveau blacklisted in the image and initramfs rebuilt so the proprietary NVIDIA module owns the GPU at boot;
- pinned ComfyUI v0.34.0 at commit `12d5279438bfefc058a269eae805ceab6047777f`;
- pinned PyTorch 2.7.1 / torchvision 0.22.1 / torchaudio 2.7.1 with the CUDA 12.6 runtime;
- ComfyUI supervised by systemd and bound only to `127.0.0.1:8188`;
- persistent ComfyUI input/output/user/temp/model paths under `/forge-data`;
- OpenSSH server installed and enabled for maintenance access;
- canonical Debian 13 APT sources restored on the installed appliance;
- NetworkManager made authoritative on first installed boot so stale Debian Installer Ethernet routes cannot outrank Wi-Fi;
- Developer/Maintenance Git update capability installed but never automatic;
- no model library in the ISO.

Models remain separate because they are large, mutable creative assets. A newly installed reference Forge can nevertheless reach `Health: Healthy` with GPU and ComfyUI ready before any model is installed; model availability is a capability concern, not base appliance health.

## Build host

Use Debian 13 with `live-build`, `debootstrap`, `rsync`, `xorriso`, `squashfs-tools`, Syslinux/ISOLINUX, and the GRUB common, BIOS and EFI builder packages installed. `grub-efi-amd64-signed` is required by Debian live-build's EFI image assembly even though OOC Forge v1 deliberately leaves Secure Boot disabled. `grub-common` supplies the canonical `unicode.pf2` font staged into the finished ISO.

The build downloads the pinned ComfyUI source and PyTorch CUDA wheels. Internet access is therefore required on the build host, but the resulting ISO carries that execution stack and does not require a post-install ComfyUI/PyTorch download.

```bash
sudo apt-get update
sudo apt-get install -y live-build debootstrap rsync xorriso squashfs-tools grub-common grub-efi-amd64-bin grub-efi-amd64-signed grub-pc-bin isolinux syslinux-common syslinux-utils fdisk
sudo ./iso/build.sh
```

Outputs are written to `dist/`:

```text
ooc-forge-0.1.1-amd64.iso
ooc-forge-0.1.1-amd64.iso.sha256
ooc-forge-0.1.1-amd64.iso.manifest.json
ooc-forge-0.1.1-amd64.iso.boot-report.txt
ooc-forge-0.1.1-amd64.iso.grub-report.txt
```

The manifest records the exact OOC Forge source ref and execution-stack pins. The build fails unless xorriso confirms hybrid MBR and GPT metadata, an EFI System Partition, a UEFI El Torito entry and the GRUB Unicode font required by the boot menu. CI also inspects the SquashFS payload for ComfyUI, SSH, the NVIDIA DKMS module and the Nouveau policy, then presents the ISO to QEMU as raw USB mass storage under OVMF and requires an in-guest boot marker.

## Validate without building

```bash
./iso/validate.sh
```

## Installed-appliance contract

A clean install onto the RTX 3090 reference Forge is expected to require no shell repair. After installation and reboot:

```text
GPU        NVIDIA GeForce RTX 3090 · 24 GB
ComfyUI    Ready
Storage    Persistent Forge Data
OOC        Standalone until commissioned
Health     Healthy
```

The first installed boot applies a one-time appliance policy. It restores canonical Debian Trixie APT repositories and reduces `/etc/network/interfaces` to loopback so NetworkManager owns Ethernet and Wi-Fi. The previous installer files are retained with a `.pre-ooc-forge` suffix for diagnostics.

## Principles

The ISO is packaging, not a second Forge implementation. Source under `forge/`, `systemd/`, `nginx/`, `scripts/` and `workflows/` remains canonical and is staged into the image at build time.

Large models are never committed to this repository or baked into the base ISO. Forge Data remains separate from the system image. Normal production updates remain signed/versioned Forge releases; Git updates are supervised Developer/Maintenance operations only.
