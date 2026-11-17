#!/usr/bin/env python3
"""Generate local credentials without printing them or overwriting existing ones."""
from pathlib import Path
import os
import secrets

ROOT = Path(__file__).resolve().parent.parent
path = ROOT / ".env"
if path.exists():
    print(".env already exists. Keeping the current configuration.")
else:
    values = {
        "SORT_HOST": "0.0.0.0", "SORT_PORT": "8100", "SORT_DATA_DIR": str(ROOT / "data"),
        "SORT_ADMIN_PASSWORD": secrets.token_urlsafe(24), "SORT_ENROLLMENT_CODE": secrets.token_urlsafe(16),
        "SORT_COOKIE_SECURE": "false", "SORT_JUDGE_IMAGE": "sorting-judge:local", "SORT_CPU": "2",
        "SORT_REPETITIONS": "10", "SORT_TIME_LIMIT": "2.0", "SORT_WALL_LIMIT": "4.0", "SORT_MEMORY_MB": "128",
    }
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as out:
        out.writelines(f"{key}={value}\n" for key, value in values.items())
    print(".env created. Administrator ID: admin.")
    print("Read the administrator password and class code from .env (mode 600).")
