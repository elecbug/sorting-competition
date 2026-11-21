"""Report time from student submission to first verdict in the production database."""
import argparse
from contextlib import closing
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import sqlite3
import statistics
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.config import Settings


def report(db, now, since=None):
    row = db.execute("SELECT value FROM metadata WHERE key='latency_tracking_since'").fetchone()
    if row is None:
        raise ValueError("First-verdict tracking starts when the updated service opens this database.")
    start = max(float(row[0]), since) if since is not None else float(row[0])
    rows = db.execute("""SELECT s.created_at, s.first_verdict_at FROM submissions s
                         JOIN users u ON u.id=s.user_id WHERE u.role='student' AND s.created_at>=?""", (start,))
    completed = []
    awaiting = overdue = 0
    for created, verdict in rows:
        if verdict is None:
            awaiting += 1
            overdue += now - created >= 10
        else:
            completed.append(verdict - created)
    completed.sort()
    under = sum(seconds < 10 for seconds in completed)
    return {
        "since_utc": datetime.fromtimestamp(start, timezone.utc).isoformat(),
        "student_submissions": len(completed) + awaiting,
        "verdict_under_10s": under,
        "verdict_at_or_over_10s": len(completed) - under,
        "awaiting_first_verdict": awaiting,
        "awaiting_at_or_over_10s": overdue,
        "median_seconds": statistics.median(completed) if completed else None,
        "p90_seconds": completed[math.ceil(len(completed) * .9) - 1] if completed else None,
        "max_seconds": max(completed, default=None),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, help="Defaults to competition.db in SORT_DATA_DIR")
    parser.add_argument("--since", type=datetime.fromisoformat, help="Start time in ISO 8601 with UTC offset")
    args = parser.parse_args()
    if args.since and args.since.tzinfo is None:
        parser.error("--since requires a UTC offset")
    path = args.db or Settings.from_env().data_dir / "competition.db"
    try:
        with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as db:
            print(json.dumps(report(db, time.time(), args.since.timestamp() if args.since else None), indent=2))
    except (ValueError, sqlite3.Error) as exc:
        parser.exit(1, f"{exc}\n")


if __name__ == "__main__":
    main()
