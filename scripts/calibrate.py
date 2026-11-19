#!/usr/bin/env python3
"""Measure representative submissions in Docker without writing competition results."""
import argparse
from contextlib import closing, contextmanager
import fcntl
import json
import os
from pathlib import Path
import sqlite3
import statistics
import sys
import threading
import time
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.config import Settings
from app.scoring import rank_entries
from judge.cores import plan
from judge.prepared import prepare_cases
from judge.profile import case_metadata
from judge.worker import Submission, compile_submission, execute, measure, ready_profile


class ReadOnly:
    def __init__(self, path):
        self.uri = path.resolve().as_uri() + "?mode=ro"

    @contextmanager
    def connect(self):
        with closing(sqlite3.connect(self.uri, uri=True)) as db:
            db.row_factory = sqlite3.Row
            yield db


def calibrate(args, settings):
    database = ReadOnly(settings.data_dir / "competition.db")
    cores, housekeeping = plan(settings, auto_limit=max(args.cores))
    counts = [count for count in args.cores if count <= len(cores)]
    if not counts:
        raise ValueError("No requested core count fits the available benchmark cores.")
    if housekeeping:
        os.sched_setaffinity(0, housekeeping)
    check_cpu = (housekeeping or cores)[0]
    _, image = ready_profile(settings, cores, check_cpu)
    owner, stop = "calibrate-" + uuid.uuid4().hex, threading.Event()
    items = []
    mode = None
    for sid in args.submission:
        with database.connect() as db:
            row = db.execute("""SELECT s.problem_id,s.source,p.scoring_mode FROM submissions s
                                JOIN problems p ON p.id=s.problem_id WHERE s.id=?""", (sid,)).fetchone()
        if not row:
            raise ValueError(f"Unknown submission #{sid}.")
        if items and items[0].problem_id != row["problem_id"]:
            raise ValueError("Use submissions from the same problem to compare scores.")
        mode = row["scoring_mode"]
        cases = case_metadata(database, row["problem_id"])
        if not cases:
            raise ValueError(f"No test cases for submission #{sid}.")
        item = Submission(sid, row["problem_id"], row["source"], "", stop, 0, "", image, "", cases)
        prepare_cases(database, settings, cases)
        failure = compile_submission(settings, item, check_cpu, owner)
        if failure:
            raise ValueError(f"Submission #{sid}: {failure['status']}")
        checked = execute(settings, None, cases, image, stop, owner, phase="check", cpu=check_cpu,
                          program=item.program, binary_hash=item.binary_hash)
        if checked["status"] != "accepted":
            raise ValueError(f"Submission #{sid}: {checked['status']}")
        items.append(item)

    for count in counts:
        samples = {item.id: {"cpu_ms": [], "peak_kib": [], "scores": [], "ranks": []} for item in items}
        started = time.monotonic()
        for _ in range(args.rounds):
            entries = []
            for item in items:
                result = measure(settings, item, cores[:count], owner)
                if result["status"] != "accepted":
                    raise ValueError(f"Submission #{item.id}: {result['status']}")
                samples[item.id]["cpu_ms"].append(result["time_ms"])
                samples[item.id]["peak_kib"].append(result["memory_kb"])
                entries.append({"submission_id":item.id, "created_at":item.id, "status":result["status"],
                                "time_ms":result["time_ms"], "memory_kb":result["memory_kb"],
                                "passed":result["passed"], "total":result["total"]})
            for entry in rank_entries(entries, mode):
                sample = samples[entry["submission_id"]]
                sample["scores"].append(entry["score"])
                sample["ranks"].append(entry["rank"])
        elapsed = time.monotonic() - started
        for sample in samples.values():
            times = sample["cpu_ms"]
            sample["cpu_cv_percent"] = round(100 * statistics.pstdev(times) / max(statistics.mean(times), .001), 3)
            sample["score_range"] = round(max(sample["scores"]) - min(sample["scores"]), 2)
        print(json.dumps({"cpus": cores[:count], "rounds": args.rounds, "repetitions": settings.repetitions,
                          "benchmarks_per_minute": round(60*len(items)*args.rounds/elapsed, 2),
                          "scoring_mode": mode, "submissions": samples}), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--submission", type=int, nargs="+", required=True, help="Representative submission IDs")
    parser.add_argument("--cores", type=int, nargs="+", default=[1, 2, 5, 10])
    parser.add_argument("--rounds", type=int, default=3)
    args = parser.parse_args()
    if len(set(args.submission)) != len(args.submission):
        parser.error("Each submission ID must appear once.")
    if min(args.cores) < 1 or args.rounds < 2:
        parser.error("Use positive core counts and at least two rounds.")
    settings = Settings.from_env()
    with (settings.data_dir / "worker.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            parser.exit(1, "The judge worker is active. Calibrate during a maintenance window.\n")
        calibrate(args, settings)


if __name__ == "__main__":
    main()
