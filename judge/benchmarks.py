"""Canonical measurements for the same executable in the same test profile."""
import json


def source_result(database, profile, source_hash):
    with database.connect() as db:
        row = db.execute("SELECT c.result FROM benchmark_sources s JOIN benchmark_cache c ON c.profile=s.profile AND c.binary_hash=s.binary_hash WHERE s.profile=? AND s.source_hash=?", (profile,source_hash)).fetchone()
    if not row:
        return None
    result = json.loads(row[0])
    result["measurement_reused"] = True
    return result


def binary_result(database, profile, binary_hash):
    with database.connect() as db:
        row = db.execute("SELECT result FROM benchmark_cache WHERE profile=? AND binary_hash=?",
                         (profile, binary_hash)).fetchone()
    if row:
        return {**json.loads(row[0]), "measurement_reused": True}


def finish(database, submission_id, token, profile, source_hash, result, timestamp, *, check_timing=None):
    with database.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        if not db.execute("SELECT 1 FROM submissions WHERE id=? AND judge_token=? AND status='running'", (submission_id,token)).fetchone():
            return False
        if result["status"] == "accepted" and result.get("binary_hash"):
            binary_hash = result["binary_hash"]
            previous = db.execute("SELECT result FROM benchmark_cache WHERE profile=? AND binary_hash=?", (profile,binary_hash)).fetchone()
            if previous:
                result = {**json.loads(previous[0]), "measurement_reused": True}
            else:
                db.execute("INSERT INTO benchmark_cache VALUES (?,?,?)", (profile,binary_hash,json.dumps(result)))
            db.execute("INSERT OR REPLACE INTO benchmark_sources VALUES (?,?,?)", (profile,source_hash,binary_hash))
        # This check belongs to the submission, never to the canonical benchmark cache.
        if check_timing is not None:
            result = {**result, "check_timing": check_timing}
        verdict_at = None if result["status"] == "system_error" else timestamp
        db.execute("""UPDATE submissions SET status=?,result=?,profile=?,finished_at=?,
                      first_verdict_at=COALESCE(first_verdict_at, ?),stage=NULL,recalibrating=0,judge_token=NULL
                      WHERE id=? AND judge_token=?""",
                   (result["status"], json.dumps(result), profile, timestamp, verdict_at, submission_id, token))
    return True
