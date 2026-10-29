#!/usr/bin/env bash
# Deploy the bot code to the servers.
#
# Usage:
#   ./deploy-bot.sh                          -> both servers
#   ./deploy-bot.sh sge                      -> Cikarang only
#   ./deploy-bot.sh pialabs@100.122.247.95   -> home only
#
# Copies the code (never .env or venv), installs requirements,
# then restarts the bot service if it exists.

set -euo pipefail
cd "$(dirname "$0")"

if [ $# -eq 0 ]; then
  set -- sge pialabs@100.122.247.95
fi

DIR=/srv/apps/server-bot

for host in "$@"; do
  echo "===== Deploying to $host ====="

  rsync -az --delete \
    --exclude venv --exclude .env --exclude .git \
    --exclude __pycache__ --exclude '*.pyc' --exclude .DS_Store \
    ./ "$host:$DIR/"

  ssh "$host" "cd $DIR && { [ -d venv ] || python3 -m venv venv; } && venv/bin/pip install -q --upgrade -r requirements.txt"

  if ssh "$host" "sudo -n systemctl restart server-bot" 2>/dev/null; then
    sleep 3
    echo "Service status: $(ssh "$host" systemctl is-active server-bot)"
  else
    echo "Service not installed yet (or no sudo permission). Code is copied, that's fine on the first deploy."
  fi
done
