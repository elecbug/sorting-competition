"""Prepare private, immutable input/answer pairs before admitting work."""
from contextlib import contextmanager
import errno
from pathlib import Path
import os
import re
import tempfile

from judge.datasets import encode_table, parse_csv, validate_rows

CACHE = "prepared-v1"


def directory(settings, case):
    digest = case["digest"]
    if not re.fullmatch("[a-f0-9]{64}", digest):
        raise ValueError("Invalid testcase digest.")
    root = settings.prepared_dir or settings.job_dir or settings.data_dir / "jobs"
    return root / CACHE / digest


@contextmanager
def stage_pair(target, input_csv, answer_csv):
    header, original = parse_csv(input_csv)
    answer_header, answer = parse_csv(answer_csv)
    validate_rows(header, original, answer_header, answer)
    dimensions = (len(original), len(header))
    if target.is_dir():
        yield dimensions, None
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=target.parent) as tmp:
        pair = Path(tmp) / "pair"
        pair.mkdir(mode=0o750)
        os.chown(pair, -1, os.getgid())
        pair.chmod(0o750)
        for name, rows in (("input", original), ("answer", answer)):
            path = pair / name
            path.write_bytes(encode_table(header, rows))
            os.chown(path, -1, os.getgid())
            path.chmod(0o440)
        yield dimensions, pair


def publish_pair(pair, target):
    if pair is not None:
        try:
            pair.rename(target)
        except OSError as exc:
            # Another publisher may have won the race for the same digest.
            if exc.errno not in (errno.EEXIST, errno.ENOTEMPTY) or not target.is_dir():
                raise


def prepare_cases(database, settings, cases):
    for case in cases:
        target = directory(settings, case)
        if target.is_dir():
            continue
        with database.connect() as db:
            row = db.execute("SELECT input_csv,answer_csv FROM testcases WHERE id=? AND digest=?",
                             (case["id"], case["digest"])).fetchone()
        if row is None:
            raise RuntimeError("Test data changed during preparation.")
        with stage_pair(target, row[0], row[1]) as (_, pair):
            publish_pair(pair, target)
