#!/usr/bin/env bash
set -euo pipefail
project_dir="$(cd "$(dirname "$0")/.." && pwd)"
cd "$project_dir"
if [[ $EUID -eq 0 && -n "${SUDO_USER:-}" && "${SUDO_USER}" != root ]]; then
  runuser -u "$SUDO_USER" -- python3 "$project_dir/scripts/setup.py"
else
  python3 scripts/setup.py
fi
docker compose config --quiet
docker info >/dev/null
# Build before touching the running legacy services.
docker compose build
legacy_user="${SUDO_USER:-$(id -un)}"
legacy_uid="$(id -u "$legacy_user")"
user_systemctl() {
  if [[ $EUID -eq 0 ]]; then
    runuser -u "$legacy_user" -- env XDG_RUNTIME_DIR="/run/user/$legacy_uid" systemctl --user "$@"
  else
    systemctl --user "$@"
  fi
}
user_units=()
system_units=()
for unit in sorting-arena-web.service sorting-arena-worker.service; do
  if user_systemctl is-active --quiet "$unit" 2>/dev/null; then
    user_units+=("$unit")
  fi
  if [[ $EUID -eq 0 ]] && systemctl is-active --quiet "$unit" 2>/dev/null; then
    system_units+=("$unit")
  fi
done
if ((${#user_units[@]})); then user_systemctl stop "${user_units[@]}"; fi
if ((${#system_units[@]})); then systemctl stop "${system_units[@]}"; fi
if ! docker compose up -d --wait --wait-timeout 120; then
  echo "Compose startup failed."
  if ((${#user_units[@]}+${#system_units[@]})); then
    docker compose down
    if ((${#system_units[@]})); then systemctl start "${system_units[@]}"; fi
    if ((${#user_units[@]})); then user_systemctl start "${user_units[@]}"; fi
    echo "Restored the previous services."
  fi
  exit 1
fi
for unit in "${system_units[@]}"; do systemctl disable "$unit"; done
for unit in "${user_units[@]}"; do user_systemctl disable "$unit" 2>/dev/null || true; done
docker compose ps
echo "Compose deployment is healthy. Web and worker restart automatically with Docker."
