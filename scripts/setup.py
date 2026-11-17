#!/usr/bin/env python3
"""Generate configuration; preserve existing credentials and add Compose defaults."""
from pathlib import Path
import os
import secrets

ROOT = Path(__file__).resolve().parent.parent
path = ROOT / ".env"
text = path.read_text() if path.exists() else ""
existing = dict(line.split("=", 1) for line in text.splitlines() if line.strip() and not line.lstrip().startswith("#") and "=" in line)
values = {
    "SORT_HOST": "0.0.0.0", "SORT_PORT": "8100", "SORT_DATA_DIR": str(ROOT / "data"),
    "SORT_ADMIN_PASSWORD": secrets.token_urlsafe(24), "SORT_COOKIE_SECURE": "false",
    "SORT_JUDGE_IMAGE": "sorting-judge:local", "SORT_CPU": "2", "SORT_REPETITIONS": "10",
    "SORT_TIME_LIMIT": "2.0", "SORT_WALL_LIMIT": "4.0", "SORT_MEMORY_MB": "128",
    "SORT_UID": str(os.getuid()), "SORT_GID": str(os.getgid()),
    "SORT_JOB_VOLUME": "sorting-competition-jobs",
}
socket = Path("/var/run/docker.sock")
if socket.exists():
    values["SORT_DOCKER_GID"] = str(socket.stat().st_gid)
missing = {key:value for key,value in values.items() if key not in existing}
fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
os.fchmod(fd, 0o600)
with os.fdopen(fd, "w") as out:
    if text and not text.endswith("\n"):
        out.write("\n")
    out.writelines(f"{key}={value}\n" for key,value in missing.items())
data_dir = Path(existing.get("SORT_DATA_DIR", values["SORT_DATA_DIR"]))
if not data_dir.is_absolute():
    data_dir = ROOT / data_dir
data_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
print("Configuration ready. Existing credentials preserved. Administrator ID: admin.")
print("Read SORT_ADMIN_PASSWORD from .env (mode 600).")
if not socket.exists():
    print("Docker socket not found. Install Docker and rerun setup before starting Compose.")
