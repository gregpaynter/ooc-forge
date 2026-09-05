#!/usr/bin/env bash
set -euo pipefail

if [[ ${EUID:-$(id -u)} -ne 0 ]]; then
  echo "Run as root." >&2
  exit 1
fi

SOURCE_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
FORGE_USER=${FORGE_USER:-forge}
FORGE_DATA=${FORGE_DATA:-/forge-data}

if [[ -z "${FORGE_DEFAULT_CHECKPOINT+x}" && -f /etc/ooc-forge/forge.env ]]; then
  FORGE_DEFAULT_CHECKPOINT=$(sed -n 's/^FORGE_DEFAULT_CHECKPOINT=//p' /etc/ooc-forge/forge.env | head -n 1)
else
  FORGE_DEFAULT_CHECKPOINT=${FORGE_DEFAULT_CHECKPOINT:-}
fi

GIT_SOURCE_REF=$(git -C "$SOURCE_DIR" rev-parse HEAD 2>/dev/null || true)
if [[ -n "$GIT_SOURCE_REF" ]]; then
  SOURCE_REF=$GIT_SOURCE_REF
  if [[ -n "${OOC_FORGE_SOURCE_REF:-}" && "$OOC_FORGE_SOURCE_REF" != "$GIT_SOURCE_REF" ]]; then
    echo "Ignoring stale OOC_FORGE_SOURCE_REF=$OOC_FORGE_SOURCE_REF; local checkout is $GIT_SOURCE_REF" >&2
  fi
else
  SOURCE_REF=${OOC_FORGE_SOURCE_REF:-local}
fi

apt-get update
apt-get install -y \
  python3 python3-venv python3-pip \
  nginx avahi-daemon network-manager \
  curl rsync git sudo openssh-server build-essential cmake ffmpeg

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

for workflow in manual-image manual-image-reference print-upscale video-wan22-ti2v audio-stable-audio3; do
  install -o "$FORGE_USER" -g "$FORGE_USER" -d "$FORGE_DATA/workflows/$workflow"
  install -o "$FORGE_USER" -g "$FORGE_USER" -m 0644 "$SOURCE_DIR/workflows/$workflow/manifest.json" "$FORGE_DATA/workflows/$workflow/manifest.json"
  install -o "$FORGE_USER" -g "$FORGE_USER" -m 0644 "$SOURCE_DIR/workflows/$workflow/workflow.json" "$FORGE_DATA/workflows/$workflow/workflow.json"
done

# Use the exact same pinned execution and prompt-compiler payloads as the ISO.
"$SOURCE_DIR/scripts/install-comfyui-runtime"
"$SOURCE_DIR/scripts/install-prompt-runtime"

for unit in ooc-forge-init ooc-forge-gpu-init ooc-forge-web ooc-forge-worker ooc-forge-sync comfyui; do
  install -m 0644 "$SOURCE_DIR/systemd/$unit.service" "/etc/systemd/system/$unit.service"
done
for unit in ooc-forge-git-update ooc-forge-model-install ooc-forge-upscale-model-install ooc-forge-prompt-model-install ooc-forge-video-model-install ooc-forge-audio-model-install; do
  install -m 0644 "$SOURCE_DIR/systemd/$unit.service" "/etc/systemd/system/$unit.service"
done
install -m 0755 "$SOURCE_DIR/scripts/ooc-forge-git-update" /usr/local/sbin/ooc-forge-git-update
install -m 0755 "$SOURCE_DIR/scripts/ooc-forge-model-install" /usr/local/sbin/ooc-forge-model-install
install -m 0755 "$SOURCE_DIR/scripts/ooc-forge-upscale-model-install" /usr/local/sbin/ooc-forge-upscale-model-install
install -m 0755 "$SOURCE_DIR/scripts/ooc-forge-prompt-model-install" /usr/local/sbin/ooc-forge-prompt-model-install
install -m 0755 "$SOURCE_DIR/scripts/ooc-forge-video-model-install" /usr/local/sbin/ooc-forge-video-model-install
install -m 0755 "$SOURCE_DIR/scripts/ooc-forge-audio-model-install" /usr/local/sbin/ooc-forge-audio-model-install
install -m 0755 "$SOURCE_DIR/scripts/ooc-forge-gpu-init" /usr/local/sbin/ooc-forge-gpu-init
install -m 0440 "$SOURCE_DIR/systemd/ooc-forge-maintenance.sudoers" /etc/sudoers.d/ooc-forge-maintenance
visudo -cf /etc/sudoers.d/ooc-forge-maintenance >/dev/null

rm -f /etc/nginx/sites-enabled/default
install -m 0644 "$SOURCE_DIR/nginx/ooc-forge.conf" /etc/nginx/sites-available/ooc-forge
ln -sfn /etc/nginx/sites-available/ooc-forge /etc/nginx/sites-enabled/ooc-forge
hostnamectl set-hostname forge
if grep -q '^127\.0\.1\.1[[:space:]]' /etc/hosts; then
  sed -i 's/^127\.0\.1\.1.*/127.0.1.1\tforge/' /etc/hosts
else
  printf '127.0.1.1\tforge\n' >> /etc/hosts
fi

systemctl daemon-reload
systemctl enable --now NetworkManager avahi-daemon nginx ssh
systemctl enable ooc-forge-init ooc-forge-gpu-init comfyui ooc-forge-web ooc-forge-worker ooc-forge-sync
systemctl restart ooc-forge-init
systemctl restart ooc-forge-gpu-init
systemctl restart comfyui ooc-forge-web ooc-forge-worker ooc-forge-sync

echo
echo "FORGE local runtime installed."
echo "Open: http://forge.local/"
echo "Install/manage SDXL and print models from Models; prompt/video/audio models from Creative Models."
echo "Developer/Maintenance Git updates are available under System."
echo "If mDNS is unavailable, use this machine's LAN IP address."
