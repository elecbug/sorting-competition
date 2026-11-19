#!/usr/bin/env python3
from pathlib import Path
import os
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.config import Settings
from judge.cores import plan
import uvicorn

try:
    _, housekeeping = plan(Settings.from_env())
except (ValueError, OSError) as exc:
    print(f"CPU reservation unavailable: {exc}", file=sys.stderr)
else:
    if housekeeping:
        os.sched_setaffinity(0, housekeeping)
uvicorn.run("app.main:create_app", factory=True, host=os.getenv("SORT_HOST", "0.0.0.0"), port=int(os.getenv("SORT_PORT", "8100")), proxy_headers=False)
