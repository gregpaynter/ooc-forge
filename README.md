# OOC Forge v1 local runtime

OOC Forge is the local creative execution appliance for OOC. The canonical v1 install path is the versioned Debian ISO; the runtime remains independently installable from source for development and maintenance.

## What works

- OOC-branded local browser UI at `forge.local`
- persistent Forge identity and SQLite job state under `/forge-data`
- Manual Create image jobs
- ComfyUI execution over localhost only (`127.0.0.1:8188`)
- local Study asset retention with SHA-256 evidence
- browser-driven pairing with public OOC System
- outbound heartbeat and ProductionJob polling
- outbound preview upload into OOC storage before Candidate completion
- systemd supervision for Forge web, worker, sync and ComfyUI
- nginx LAN entry point and Avahi/mDNS discovery
- `ooc-forge doctor` health output
- Developer/Maintenance Git update path, explicitly gated and never automatic

## Reference appliance

- Debian 13 / x86_64
- NVIDIA RTX 3090 24 GB
- NVIDIA Debian driver with Nouveau disabled
- ComfyUI v0.34.0 pinned at commit `12d5279438bfefc058a269eae805ceab6047777f`
- PyTorch 2.7.1 + CUDA 12.6 runtime
- ComfyUI installed at `/opt/ComfyUI`
- ComfyUI Python environment at `/opt/ComfyUI/.venv`
- mutable models and ComfyUI state under `/forge-data`

The ISO carries the execution software but deliberately does not carry the model library. Models are persistent creative assets installed and managed separately.

## Install the appliance

Build or download the versioned OOC Forge ISO, flash it to USB, boot the reference machine and install Debian/OOC Forge to disk. The installed appliance is responsible for the complete base runtime: NVIDIA kernel module, ComfyUI/PyTorch, SSH maintenance service, APT sources, NetworkManager ownership, nginx and Forge systemd services.

A clean RTX 3090 installation is expected to boot to:

```text
Health      Healthy
GPU         NVIDIA GeForce RTX 3090 · 24 GB
ComfyUI     Ready
Storage     Persistent Forge Data
OOC         Standalone
```

No post-install shell repair is part of the appliance contract.

See `iso/README.md` for the ISO build, USB boot and validation gates.

## Development install

From this source directory:

```bash
sudo ./scripts/install-local.sh
```

This remains a developer/maintenance path rather than the production appliance installation mechanism.

After installation open:

```text
http://forge.local/
```

On first access create the local admin password and name the Forge.

## First local proof

1. Confirm `System` reports NVIDIA and ComfyUI ready.
2. Install/select the OOC image model in persistent Forge Data.
3. Open `Manual Create`.
4. Enter a prompt and generate.
5. Confirm the job moves `QUEUED → RUNNING → COMPLETED`.
6. Confirm the image is retained under `/forge-data/library/studies/<job-id>/`.

## OOC commissioning proof

1. Open `forge.local → OOC System`.
2. Start pairing with the OOC System origin.
3. Approve the displayed code at `/admin/forges`.
4. The outbound sync service receives and stores its machine credential.
5. Queue the commissioning test from OOC Admin.
6. Forge claims the job, executes it locally through ComfyUI, uploads the generated preview and completes the Candidate.
7. Complete commissioning in OOC Admin.

## Service layout

```text
nginx :80
   ↓
OOC Forge web :8080
   ├── local SQLite /forge-data/database/forge.db
   ├── worker → ComfyUI 127.0.0.1:8188 → RTX 3090
   └── sync → outbound HTTPS → OOC System
```

ComfyUI is intentionally bound to localhost, not exposed directly on the LAN.

## Persistent paths

```text
/forge-data/
├── config/
├── identity/
├── database/
├── models/
├── workflows/
├── library/studies/
├── library/works/
├── library/experiences/
├── provenance/
├── jobs/
├── sync/
├── cache/
├── comfyui-input/
├── comfyui-output/
├── comfyui-temp/
└── comfyui-user/
```

## Useful commands

```bash
systemctl status ooc-forge-web ooc-forge-worker ooc-forge-sync comfyui
journalctl -u ooc-forge-worker -f
/opt/ooc-forge/.venv/bin/ooc-forge doctor
```

## Remaining v1 capability work

- model pack downloader/archiver UI
- video + audio workflows
- backup/restore UI
- signed/versioned production software update UI
- kiosk session

These are capability and lifecycle increments around a self-contained appliance base.
