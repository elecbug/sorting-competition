#!/usr/bin/env bash
set -euo pipefail
if [[ $EUID -ne 0 ]]; then
  echo "sudo bash deploy/install.sh 로 실행하세요."
  exit 1
fi
project_dir="$(cd "$(dirname "$0")/.." && pwd)"
service_user="${SUDO_USER:-}"
if [[ -z "$service_user" || "$service_user" == root ]]; then
  echo "서비스를 운영할 일반 계정에서 sudo bash deploy/install.sh 를 실행하세요."
  exit 1
fi
if [[ ! -x "$project_dir/.venv/bin/python" || ! -f "$project_dir/.env" ]]; then
  echo "먼저 uv sync 및 .venv/bin/python scripts/setup.py 를 실행하세요."
  exit 1
fi
if ! getent group docker >/dev/null; then
  echo "Docker Engine 및 docker 그룹이 필요합니다."
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
echo "설치 완료. systemctl status sorting-arena-web sorting-arena-worker 로 확인하세요."
