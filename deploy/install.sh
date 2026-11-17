#!/usr/bin/env bash
set -euo pipefail
if [[ $EUID -ne 0 ]]; then
  echo "Run sudo bash deploy/install.sh."
  exit 1
fi
project_dir="$(cd "$(dirname "$0")/.." && pwd)"
service_user="${SUDO_USER:-}"
if [[ -z "$service_user" || "$service_user" == root ]]; then
  echo "Run sudo bash deploy/install.sh from the intended non-root service account."
  exit 1
fi
if [[ ! -x "$project_dir/.venv/bin/python" || ! -f "$project_dir/.env" ]]; then
  echo "Run uv sync and .venv/bin/python scripts/setup.py first."
  exit 1
fi
if ! getent group docker >/dev/null; then
  echo "Docker Engine and the docker group are required."
  exit 1
fi
cd "$project_dir"
docker build -f judge/Dockerfile -t sorting-judge:local .
# If temporary user services were used for initial preview, retire only our units.
service_uid="$(id -u "$service_user")"
runuser -u "$service_user" -- env XDG_RUNTIME_DIR="/run/user/$service_uid" systemctl --user stop sorting-arena-web.service sorting-arena-worker.service 2>/dev/null || true
for kind in web worker; do
  if [[ "$kind" == web ]]; then
    entry="scripts/serve.py"
    extra=""
  else
    entry="-m judge.worker"
    extra="SupplementaryGroups=docker"
  fi
  cat > "/etc/systemd/system/sorting-arena-$kind.service" <<EOF
[Unit]
Description=Sorting Arena $kind
After=network.target docker.service

[Service]
Type=simple
User=$service_user
WorkingDirectory=$project_dir
ExecStart=$project_dir/.venv/bin/python $entry
Restart=on-failure
RestartSec=3
UMask=0077
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=read-only
ReadWritePaths=$project_dir/data
PrivateTmp=true
Environment=PYTHONDONTWRITEBYTECODE=1
$extra

[Install]
WantedBy=multi-user.target
EOF
done
systemctl daemon-reload
systemctl enable --now sorting-arena-web.service sorting-arena-worker.service
echo "Installed. Check systemctl status sorting-arena-web sorting-arena-worker."
