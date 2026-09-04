#!/usr/bin/env bash
set -euo pipefail

if [[ ${EUID:-$(id -u)} -ne 0 ]]; then
  echo "Run as root." >&2
  exit 1
fi

SOURCE_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
FORGE_USER=${FORGE_USER:-forge}
FORGE_DATA=${FORGE_DATA:-/forge-data}
FORGE_DEFAULT_CHECKPOINT=${FORGE_DEFAULT_CHECKPOINT:-}
SOURCE_REF=${OOC_FORGE_SOURCE_REF:-}
if [[ -z "$SOURCE_REF" ]]; then
  SOURCE_REF=$(git -C "$SOURCE_DIR" rev-parse HEAD 2>/dev/null || printf 'local\n')
fi

apt-get update
apt-get install -y \
  python3 python3-venv python3-pip \
  nginx avahi-daemon network-manager \
  curl rsync git sudo openssh-server build-essential ffmpeg

if ! id "$FORGE_USER" >/dev/null 2>&1; then
  useradd --system --create-home --home-dir /var/lib/ooc-forge --shell /usr/sbin/nologin "$FORGE_USER"
fi

install -d -o "$FORGE_USER" -g "$FORGE_USER" -m 0750 "$FORGE_DATA"
install -d -m 0755 /opt/ooc-forge /etc/ooc-forge
rsync -a --delete --exclude '.venv' "$SOURCE_DIR/" /opt/ooc-forge/
printf '%s\n' "$SOURCE_REF" > /opt/ooc-forge/.ooc-source-ref
python3 -m venv /opt/ooc-forge/.venv
/opt/ooc-forge/.venv/bin/pip install --upgrade pip
/opt/ooc-forge/.venv/bin/pip install /opt/ooc-forge

cat > /etc/ooc-forge/forge.env <<ENV
FORGE_DATA_ROOT=$FORGE_DATA
COMFYUI_URL=http://127.0.0.1:8188
FORGE_POLL_INTERVAL=5
FORGE_DEFAULT_CHECKPOINT=$FORGE_DEFAULT_CHECKPOINT
ENV
chmod 0640 /etc/ooc-forge/forge.env
chown root:"$FORGE_USER" /etc/ooc-forge/forge.env

cat > /etc/ooc-forge/comfyui-model-paths.yaml <<YAML
ooc_forge:
  base_path: $FORGE_DATA
  is_default: true
  checkpoints: models/checkpoints
  loras: models/loras
  vae: models/vae
  text_encoders: models/text_encoders
  diffusion_models: models/diffusion_models
  clip_vision: models/clip_vision
  controlnet: models/controlnet
  upscale_models: models/upscale_models
  embeddings: models/embeddings
YAML

install -o "$FORGE_USER" -g "$FORGE_USER" -d "$FORGE_DATA/workflows/manual-image"
install -o "$FORGE_USER" -g "$FORGE_USER" -m 0644 "$SOURCE_DIR/workflows/manual-image/manifest.json" "$FORGE_DATA/workflows/manual-image/manifest.json"
install -o "$FORGE_USER" -g "$FORGE_USER" -m 0644 "$SOURCE_DIR/workflows/manual-image/workflow.json" "$FORGE_DATA/workflows/manual-image/workflow.json"

# Use the exact same pinned execution payload as the appliance ISO.
"$SOURCE_DIR/scripts/install-comfyui-runtime"

for unit in ooc-forge-init ooc-forge-web ooc-forge-worker ooc-forge-sync comfyui; do
  install -m 0644 "$SOURCE_DIR/systemd/$unit.service" "/etc/systemd/system/$unit.service"
done
install -m 0644 "$SOURCE_DIR/systemd/ooc-forge-git-update.service" /etc/systemd/system/ooc-forge-git-update.service
install -m 0755 "$SOURCE_DIR/scripts/ooc-forge-git-update" /usr/local/sbin/ooc-forge-git-update
install -m 0440 "$SOURCE_DIR/systemd/ooc-forge-maintenance.sudoers" /etc/sudoers.d/ooc-forge-maintenance
visudo -cf /etc/sudoers.d/ooc-forge-maintenance >/dev/null

rm -f /etc/nginx/sites-enabled/default
install -m 0644 "$SOURCE_DIR/nginx/ooc-forge.conf" /etc/nginx/sites-available/ooc-forge
ln -sfn /etc/nginx/sites-available/ooc-forge /etc/nginx/sites-enabled/ooc-forge
hostnamectl set-hostname forge
systemctl daemon-reload
systemctl enable --now NetworkManager avahi-daemon nginx ssh
systemctl enable --now ooc-forge-init comfyui ooc-forge-web ooc-forge-worker ooc-forge-sync

echo
echo "OOC Forge local runtime installed."
echo "Open: http://forge.local/"
echo "Developer/Maintenance Git updates are available under System."
echo "If mDNS is unavailable, use this machine's LAN IP address."
