#!/usr/bin/env bash
set -euo pipefail

# Run this on the cloud server from the project root.
# Usually called by deployment/deploy_to_cloud.sh.

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_USER="${APP_USER:-$(id -un)}"
MODE="${MODE:-openclaw}" # openclaw | telegram-bot
APP_HOME="$(getent passwd "$APP_USER" | cut -d: -f6)"

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Please run with sudo so system packages and services can be installed."
  exit 1
fi

cd "$APP_DIR"

echo "Installing OS packages..."
if command -v apt-get >/dev/null 2>&1; then
  apt-get update
  DEBIAN_FRONTEND=noninteractive apt-get install -y \
    ca-certificates \
    curl \
    rsync \
    build-essential \
    python3 \
    python3-venv \
    python3-pip \
    libgl1 \
    libglib2.0-0 \
    libfontconfig1
elif command -v dnf >/dev/null 2>&1; then
  dnf install -y \
    ca-certificates \
    rsync \
    gcc \
    gcc-c++ \
    make \
    python3 \
    python3-pip \
    mesa-libGL \
    glib2 \
    fontconfig
elif command -v yum >/dev/null 2>&1; then
  yum install -y \
    ca-certificates \
    rsync \
    gcc \
    gcc-c++ \
    make \
    python3 \
    python3-pip \
    mesa-libGL \
    glib2 \
    fontconfig
else
  echo "Unsupported server OS: apt-get, dnf, or yum is required."
  exit 1
fi

if ! command -v node >/dev/null 2>&1 || ! node -e 'process.exit(Number(process.versions.node.split(".")[0]) >= 22 ? 0 : 1)' >/dev/null 2>&1; then
  echo "Installing Node.js 22..."
  if command -v apt-get >/dev/null 2>&1; then
    curl -fsSL https://deb.nodesource.com/setup_22.x | bash -
    DEBIAN_FRONTEND=noninteractive apt-get install -y nodejs
  elif command -v dnf >/dev/null 2>&1; then
    curl -fsSL https://rpm.nodesource.com/setup_22.x | bash -
    dnf install -y nodejs
  else
    curl -fsSL https://rpm.nodesource.com/setup_22.x | bash -
    yum install -y nodejs
  fi
fi

echo "Installing OpenClaw..."
npm install -g openclaw@latest

echo "Installing Python dependencies..."
python3 -m venv "$APP_DIR/venv"
"$APP_DIR/venv/bin/pip" install --upgrade pip
"$APP_DIR/venv/bin/pip" install -r "$APP_DIR/requirements.txt"

mkdir -p "$APP_DIR/uploads" "$APP_DIR/data/processed" "$APP_DIR/outputs/reports"
chown -R "$APP_USER:$APP_USER" "$APP_DIR"

if [[ ! -f "$APP_DIR/.env" ]]; then
  cat > "$APP_DIR/.env" <<'ENV'
# Required for Telegram/OpenClaw gateway.
TELEGRAM_BOT_TOKEN=

# Optional. Enables the LLM narrative in report_generator.py.
OPENROUTER_API_KEY=
LLM_MODEL=deepseek/deepseek-v4-flash
ENV
  chmod 600 "$APP_DIR/.env"
  chown "$APP_USER:$APP_USER" "$APP_DIR/.env"
fi

mkdir -p "$APP_HOME/.openclaw"
if [[ ! -f "$APP_HOME/.openclaw/openclaw.json" ]]; then
  cp "$APP_DIR/deployment/openclaw.json" "$APP_HOME/.openclaw/openclaw.json"
  chown -R "$APP_USER:$APP_USER" "$APP_HOME/.openclaw"
fi
if ! grep -q '"gateway"' "$APP_HOME/.openclaw/openclaw.json"; then
  python3 - "$APP_HOME/.openclaw/openclaw.json" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
config = json.loads(path.read_text())
config["gateway"] = {"mode": "local"}
path.write_text(json.dumps(config, indent=2) + "\n")
PY
  chown "$APP_USER:$APP_USER" "$APP_HOME/.openclaw/openclaw.json"
fi
python3 - "$APP_HOME/.openclaw/openclaw.json" <<'PY'
import json
import sys
from pathlib import Path

model = "openrouter/deepseek/deepseek-v4-flash"
path = Path(sys.argv[1])
config = json.loads(path.read_text())
agents = config.setdefault("agents", {})
defaults = agents.setdefault("defaults", {})
defaults.setdefault("model", {})["primary"] = model
defaults.setdefault("models", {}).setdefault(model, {})
path.write_text(json.dumps(config, indent=2) + "\n")
PY
chown "$APP_USER:$APP_USER" "$APP_HOME/.openclaw/openclaw.json"

OPENCLAW_BIN="$(command -v openclaw)"

echo "Installing Archive Photo Triage OpenClaw skill..."
if command -v runuser >/dev/null 2>&1; then
  runuser -u "$APP_USER" -- env HOME="$APP_HOME" "$OPENCLAW_BIN" skills install \
    "$APP_DIR/skills/archive-photo-triage" \
    --as archive-photo-triage \
    --agent main \
    --force
else
  sudo -u "$APP_USER" HOME="$APP_HOME" "$OPENCLAW_BIN" skills install \
    "$APP_DIR/skills/archive-photo-triage" \
    --as archive-photo-triage \
    --agent main \
    --force
fi

install_service() {
  local template="$1"
  local destination="$2"
  sed \
    -e "s#__APP_DIR__#$APP_DIR#g" \
    -e "s#__APP_USER__#$APP_USER#g" \
    -e "s#__OPENCLAW_BIN__#$OPENCLAW_BIN#g" \
    "$template" > "$destination"
}

install_service "$APP_DIR/deployment/openclaw-gateway.service" "/etc/systemd/system/archive-photo-openclaw.service"
install_service "$APP_DIR/deployment/telegram-bot.service" "/etc/systemd/system/archive-photo-telegram.service"

systemctl daemon-reload

if [[ "$MODE" == "telegram-bot" ]]; then
  systemctl disable --now archive-photo-openclaw.service >/dev/null 2>&1 || true
  systemctl enable --now archive-photo-telegram.service
  systemctl status archive-photo-telegram.service --no-pager || true
else
  systemctl disable --now archive-photo-telegram.service >/dev/null 2>&1 || true
  systemctl enable --now archive-photo-openclaw.service
  systemctl status archive-photo-openclaw.service --no-pager || true
fi

echo
echo "Setup complete."
echo "Important: if .env still has TELEGRAM_BOT_TOKEN= blank, edit it and restart:"
echo "  sudo nano $APP_DIR/.env"
echo "  sudo systemctl restart archive-photo-openclaw"
