#!/usr/bin/env python3
"""Container readiness checks without printing secrets or creating a database."""
import json
import os
from pathlib import Path
import sqlite3
import sys
import time
from urllib.request import urlopen


def main():
    if sys.argv[1] == "web":
        with urlopen("http://127.0.0.1:8100/api/health", timeout=3) as response:
            return 0 if json.load(response).get("web") == "ok" else 1
    path = Path(os.getenv("SORT_DATA_DIR", "/data")) / "competition.db"
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=3) as db:
        row = db.execute("SELECT value FROM metadata WHERE key='worker'").fetchone()
    state = json.loads(row[0]) if row else {}
    return 0 if state.get("status") in ("ready", "running") and time.time() - state.get("updated_at", 0) < 20 else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        raise SystemExit(1)
