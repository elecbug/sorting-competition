#!/usr/bin/env python3
"""Functional smoke test for our OWN reference_sort.c, never for student uploads.

This does not create leaderboard results and is not the production sandbox path.
"""
from pathlib import Path
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.config import ROOT, Settings
from app.db import Database
from judge.profile import load_cases
from judge.cores import plan
from judge.supervisor import judge_job


def main():
    settings = Settings.from_env()
    database = Database(settings.data_dir)
    _, housekeeping = plan(settings)
    if housekeeping:
        os.sched_setaffinity(0, housekeeping)
    with database.connect() as db:
        problems = [dict(r) for r in db.execute("SELECT id,title FROM problems WHERE title LIKE '[Sample]%' OR title LIKE '[샘플]%' ORDER BY id")]
    if not problems:
        raise SystemExit("Run scripts/import_samples.py first.")
    for problem in problems:
        # This script knows only the default, first-column sample rule.
        if not problem["title"].endswith(("· 1열 정렬", "· Column 1")):
            continue
        with tempfile.TemporaryDirectory(prefix="sort-reference-") as directory:
            launcher = str(Path(directory) / "launch")
            subprocess.run(["gcc", "-O2", "-std=c11", "-Wall", "-Wextra", str(ROOT / "judge/launch.c"), "-o", launcher], check=True)
            cases = load_cases(database, problem["id"])
            result = judge_job({"cases": cases, "repetitions": 3, "time_limit": settings.time_limit, "wall_limit": settings.wall_limit, "memory_mb": settings.memory_mb},
                source_path=str(ROOT / "examples/reference_sort.c"), work_dir=directory,
                harness=str(ROOT / "judge/harness.c"), include=str(ROOT / "examples"), uid=None, launcher=launcher)
            print(f"{problem['title']}: {result['status']}, {result['passed']}/{result['total']}, CPU={result.get('time_ms')}ms, RSS={result.get('memory_kb')}KiB", flush=True)
            if result["status"] != "accepted":
                print(result)
                raise SystemExit(1)


if __name__ == "__main__":
    main()
