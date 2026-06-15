#!/usr/bin/env bash
set -euo pipefail

# Deploy this project to a cloud VM using SSH.
#
# Usage:
#   deployment/deploy_to_cloud.sh ubuntu@1.2.3.4
#   deployment/deploy_to_cloud.sh ubuntu@1.2.3.4 /path/to/key.pem --include-env
#
# Defaults:
#   PEM: /Users/aidilkhairi/Downloads/aidilkey.pem
#   REMOTE_DIR: /opt/archive_photo_assistant
#   MODE: openclaw

if [[ $# -lt 1 ]]; then
  echo "Usage: $0 <user@host> [pem_path] [--include-env]"
  exit 2
fi

SSH_TARGET="$1"
PEM_PATH="${2:-/Users/aidilkhairi/Downloads/aidilkey.pem}"
INCLUDE_ENV="false"

for arg in "$@"; do
  if [[ "$arg" == "--include-env" ]]; then
    INCLUDE_ENV="true"
  fi
done

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REMOTE_DIR="${REMOTE_DIR:-/opt/archive_photo_assistant}"
MODE="${MODE:-openclaw}"
REMOTE_STAGE="/tmp/archive_photo_assistant_upload"
SSH_OPTS=(-i "$PEM_PATH" -o StrictHostKeyChecking=accept-new)

if [[ ! -f "$PEM_PATH" ]]; then
  echo "PEM key not found: $PEM_PATH"
  exit 1
fi

chmod 400 "$PEM_PATH"

EXCLUDES=(
  "--exclude=.git/"
  "--exclude=.idea/"
  "--exclude=venv/"
  "--exclude=__pycache__/"
  "--exclude=*.pyc"
  "--exclude=.DS_Store"
  "--exclude=*.pem"
  "--exclude=outputs/"
  "--exclude=data/processed/"
)

if [[ "$INCLUDE_ENV" != "true" ]]; then
  EXCLUDES+=("--exclude=.env")
fi

echo "Uploading project to $SSH_TARGET ..."
ssh "${SSH_OPTS[@]}" "$SSH_TARGET" "rm -rf $REMOTE_STAGE && mkdir -p $REMOTE_STAGE"
rsync -az --delete -e "ssh -i $PEM_PATH -o StrictHostKeyChecking=accept-new" "${EXCLUDES[@]}" "$PROJECT_ROOT/" "$SSH_TARGET:$REMOTE_STAGE/"

echo "Installing project at $REMOTE_DIR ..."
ssh "${SSH_OPTS[@]}" "$SSH_TARGET" "
  set -e
  sudo mkdir -p '$REMOTE_DIR'
  sudo rsync -a --delete '$REMOTE_STAGE/' '$REMOTE_DIR/'
  sudo chown -R \$(id -un):\$(id -gn) '$REMOTE_DIR'
  cd '$REMOTE_DIR'
  sudo APP_USER=\$(id -un) MODE='$MODE' bash deployment/setup_cloud_server.sh
"

echo
echo "Deploy complete."
echo "Check service:"
echo "  ssh -i $PEM_PATH $SSH_TARGET 'sudo systemctl status archive-photo-openclaw --no-pager'"
echo
echo "Follow logs:"
echo "  ssh -i $PEM_PATH $SSH_TARGET 'sudo journalctl -u archive-photo-openclaw -f'"
