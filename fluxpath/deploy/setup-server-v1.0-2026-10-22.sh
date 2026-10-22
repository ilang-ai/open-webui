#!/usr/bin/env bash
# FluxPath app server setup, v1.0 (2026-10-22). Ubuntu 24.04, run as root.
#
# Installs Docker, lays out /opt/fluxpath, starts the app and the tunnel, waits for the app.
# Every step is safe to run again. Nothing here touches SSH, users, the firewall or other services.
#
#   scp -r deploy/ root@HOST:/opt/fluxpath-deploy && ssh root@HOST 'bash /opt/fluxpath-deploy/setup-server-v1.0-2026-10-22.sh'
#
# Before running: fluxpath.env and tunnel.env must exist next to this script (copied from the
# .example files and filled in). The judgment filter is installed afterwards with install_filter.py.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
APP=/opt/fluxpath
STATE=$APP/setup-state.log
mkdir -p "$APP"
mark() { echo "$(date -u +%FT%TZ) $1" >> "$STATE"; echo "== $1"; }

[ -f "$HERE/fluxpath.env" ] || { echo "fluxpath.env missing next to this script"; exit 1; }
[ -f "$HERE/tunnel.env" ] || { echo "tunnel.env missing next to this script"; exit 1; }

if ! command -v docker >/dev/null 2>&1; then
  apt-get update -qq
  apt-get install -y -qq ca-certificates curl gnupg
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
  chmod a+r /etc/apt/keyrings/docker.gpg
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" > /etc/apt/sources.list.d/docker.list
  apt-get update -qq
  apt-get install -y -qq docker-ce docker-ce-cli containerd.io docker-compose-plugin
  mark DOCKER_INSTALLED
fi
systemctl enable --now docker >/dev/null
docker --version && docker compose version

cp "$HERE/docker-compose.yml" "$APP/docker-compose.yml"
cp "$HERE/fluxpath.env" "$APP/fluxpath.env"
cp "$HERE/tunnel.env" "$APP/tunnel.env"
chmod 600 "$APP/fluxpath.env" "$APP/tunnel.env"
mark FILES_IN_PLACE

cd "$APP"
docker compose pull -q
docker compose up -d
mark COMPOSE_UP

for i in $(seq 1 60); do
  if curl -fsS -m 3 http://127.0.0.1:8080/health >/dev/null 2>&1; then
    mark APP_HEALTHY
    curl -s -m 5 http://127.0.0.1:8080/api/config | head -c 300; echo
    docker compose ps
    exit 0
  fi
  sleep 5
done
echo "app did not become healthy in 5 minutes"; docker compose logs --tail 50 fluxpath; exit 1
