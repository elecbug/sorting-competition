"""Worker-owned, immutable binary CSV pairs. Publish complete pairs atomically."""
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
    return (settings.job_dir or settings.data_dir / "jobs") / CACHE / digest


def prepare_cases(database, settings, cases):
    for case in cases:
        target = directory(settings, case)
        if target.is_dir():
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        with database.connect() as db:
            row = db.execute("SELECT input_csv,answer_csv FROM testcases WHERE id=? AND digest=?",
                             (case["id"], case["digest"])).fetchone()
        if row is None:
            raise RuntimeError("Test data changed during preparation.")
        header, original = parse_csv(row[0])
        answer_header, answer = parse_csv(row[1])
        validate_rows(header, original, answer_header, answer)
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
            os.rename(pair, target)
