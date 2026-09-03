# OOC Forge v1 local runtime

This is the first executable OOC Forge appliance slice. It is intended to be installed on the existing Debian/RTX 3090 Forge before the same known-good runtime is wrapped in the bootable ISO.

## What works in this slice

- OOC-branded local browser UI at `forge.local`
- persistent Forge identity and SQLite job state under `/forge-data`
- Manual Create image jobs
- ComfyUI execution over localhost only (`127.0.0.1:8188`)
- local Study asset retention with SHA-256 evidence
- browser-driven pairing with public OOC System
- outbound heartbeat and ProductionJob polling
- outbound preview upload into OOC storage before Candidate completion
- durable remote-job inbox with attempt-scoped leases and retryable reconciliation
- idempotent multi-asset upload and Candidate completion
- governed local Study submission to OOC as a Candidate
- systemd supervision for Forge web, worker, sync and ComfyUI
- nginx LAN entry point and Avahi/mDNS discovery
- `ooc-forge doctor` health output

## Reference host

- Debian / x86_64
- NVIDIA RTX 3090 24 GB
- ComfyUI installed at `/opt/ComfyUI`
- ComfyUI Python environment at `/opt/ComfyUI/.venv`

## Install on the current Forge

From this source directory:

```bash
sudo ./scripts/install-local.sh
```

If the installed checkpoint is not named `OOC_CORE_IMAGE.safetensors`, pass the ComfyUI checkpoint filename during installation:

```bash
sudo FORGE_DEFAULT_CHECKPOINT='your-model.safetensors' ./scripts/install-local.sh
```

The installer does not install or alter the NVIDIA driver. That remains a separate appliance/ISO concern until this runtime is proven on the reference 3090.

After installation open:

```text
http://forge.local/
```

On first access create the local admin password and name the Forge.

## First local proof

1. Confirm `System` reports NVIDIA and ComfyUI ready.
2. Open `Manual Create`.
3. Enter a prompt.
4. Generate.
5. Confirm the job moves `QUEUED → RUNNING → COMPLETED`.
6. Confirm the image is retained under `/forge-data/library/studies/<job-id>/`.

## OOC commissioning proof

After the OOC System commissioning PR is deployed:

1. Open `forge.local → OOC System`.
2. Start pairing with `https://ooc.melbourne`.
3. Approve the displayed code at `/admin/forges`.
4. The outbound sync service receives and stores its machine credential.
5. Queue the commissioning test from OOC Admin.
6. Forge claims the job, executes it locally through ComfyUI, uploads the generated preview to OOC storage, and completes the Candidate.
7. Complete commissioning in OOC Admin.
8. Power the Forge off and verify OOC continues normally with no dependency on `/forge-data`.

The physical network-loss and reboot release gate is defined in
[`docs/RTX3090-RELIABILITY-REHEARSAL.md`](docs/RTX3090-RELIABILITY-REHEARSAL.md).

## Service layout

```text
nginx :80
   ↓
OOC Forge web :8080
   ├── local SQLite /forge-data/database/forge.db
   ├── worker → ComfyUI :8188 → RTX 3090
   └── sync → outbound HTTPS → OOC System
```

ComfyUI is intentionally bound to localhost, not directly exposed on the LAN.

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
└── comfyui-output/
```

## Useful commands

```bash
systemctl status ooc-forge-web ooc-forge-worker ooc-forge-sync comfyui
journalctl -u ooc-forge-worker -f
/opt/ooc-forge/.venv/bin/ooc-forge doctor
```

## Deliberately deferred until the local proof passes

- bootable/installable ISO image assembly
- disk repartition/install wizard
- Recovery partition
- Wi-Fi setup UI/hotspot
- model pack downloader/archiver UI
- video + audio workflows
- backup/restore UI
- software update UI
- kiosk session

Those become packaging and capability increments around a runtime already shown to work.
