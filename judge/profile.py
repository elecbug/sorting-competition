import hashlib
import json


def problem_profile(base, cases):
    if not base or not cases:
        return None
    content = [{"id": c["id"], "digest": c["digest"]} for c in cases]
    payload = json.dumps({"base": base, "cases": content}, sort_keys=True, ensure_ascii=False).encode()
    return hashlib.sha256(payload).hexdigest()[:20]


def load_cases(database, problem_id):
    with database.connect() as db:
        return [dict(r) for r in db.execute("SELECT * FROM testcases WHERE problem_id=? ORDER BY id", (problem_id,))]


def current_profile(database, problem_id):
    with database.connect() as db:
        cases = [dict(r) for r in db.execute("SELECT id,digest FROM testcases WHERE problem_id=? ORDER BY id", (problem_id,))]
    return problem_profile(database.get_meta("base_profile"), cases)


def case_digest(original, answer):
    return hashlib.sha256(json.dumps([original, answer], ensure_ascii=False).encode()).hexdigest()
