#!/usr/bin/env bash
set -euo pipefail

if [[ ${EUID:-$(id -u)} -ne 0 ]]; then
  echo "Run as root." >&2
  exit 1
fi

SOURCE_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
FORGE_USER=${FORGE_USER:-forge}
FORGE_DATA=${FORGE_DATA:-/forge-data}
COMFYUI_DIR=${COMFYUI_DIR:-/opt/ComfyUI}
FORGE_DEFAULT_CHECKPOINT=${FORGE_DEFAULT_CHECKPOINT:-}

apt-get update
apt-get install -y python3 python3-venv python3-pip nginx avahi-daemon network-manager curl rsync

if ! id "$FORGE_USER" >/dev/null 2>&1; then
  useradd --system --create-home --home-dir /var/lib/ooc-forge --shell /usr/sbin/nologin "$FORGE_USER"
fi

install -d -o "$FORGE_USER" -g "$FORGE_USER" -m 0750 "$FORGE_DATA"
install -d -m 0755 /opt/ooc-forge /etc/ooc-forge
rsync -a --delete --exclude '.venv' "$SOURCE_DIR/" /opt/ooc-forge/
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

install -o "$FORGE_USER" -g "$FORGE_USER" -d "$FORGE_DATA/workflows/manual-image"
install -o "$FORGE_USER" -g "$FORGE_USER" -m 0644 "$SOURCE_DIR/workflows/manual-image/manifest.json" "$FORGE_DATA/workflows/manual-image/manifest.json"
install -o "$FORGE_USER" -g "$FORGE_USER" -m 0644 "$SOURCE_DIR/workflows/manual-image/workflow.json" "$FORGE_DATA/workflows/manual-image/workflow.json"

for unit in ooc-forge-init ooc-forge-web ooc-forge-worker ooc-forge-sync; do
  install -m 0644 "$SOURCE_DIR/systemd/$unit.service" "/etc/systemd/system/$unit.service"
done
if [[ -f "$COMFYUI_DIR/main.py" && -x "$COMFYUI_DIR/.venv/bin/python" ]]; then
  install -m 0644 "$SOURCE_DIR/systemd/comfyui.service" /etc/systemd/system/comfyui.service
  systemctl enable comfyui.service
else
  echo "ComfyUI not found at $COMFYUI_DIR; install it before image generation." >&2
fi

rm -f /etc/nginx/sites-enabled/default
install -m 0644 "$SOURCE_DIR/nginx/ooc-forge.conf" /etc/nginx/sites-available/ooc-forge
ln -sfn /etc/nginx/sites-available/ooc-forge /etc/nginx/sites-enabled/ooc-forge
hostnamectl set-hostname forge
systemctl enable --now NetworkManager avahi-daemon nginx
systemctl daemon-reload
systemctl enable --now ooc-forge-init ooc-forge-web ooc-forge-worker ooc-forge-sync

echo
echo "OOC Forge local runtime installed."
echo "Open: http://forge.local/"
echo "If mDNS is unavailable, use this machine's LAN IP address."
