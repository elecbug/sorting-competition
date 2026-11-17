"""Apply an instructor-prepared problem replacement before the new API starts."""
import hashlib
import json
from pathlib import Path
import sqlite3
import time
from judge.datasets import validate_pair
from judge.profile import case_digest


def install_pending(database, data_dir):
    manifest_path = data_dir / "pending_problem_set.json"
    if not manifest_path.exists():
        return
    manifest_bytes = manifest_path.read_bytes()
    signature = hashlib.sha256(manifest_bytes).hexdigest()
    if database.get_meta("installed_problem_set") == signature:
        return
    manifest = json.loads(manifest_bytes)
    family = manifest.get("family", manifest["set_id"])
    keys = [problem.get("key", problem["title"]) for problem in manifest["problems"]]
    if len(keys) != len(set(keys)):
        raise ValueError("Problem-set keys must be unique.")
    problems = []
    for problem in manifest["problems"]:
        cases = []
        for case in problem["cases"]:
            paths = [(data_dir / case[key]).resolve() for key in ("input_file", "answer_file")]
            if any(not path.is_relative_to(data_dir.resolve()) for path in paths):
                raise ValueError("Problem-set file must be inside the data directory.")
            original, answer = [path.read_bytes().decode("utf-8-sig") for path in paths]
            rows, columns = validate_pair(original, answer)
            cases.append((case,original,answer,rows,columns))
        problems.append((problem,cases))
    backup_dir = data_dir / "backups"
    backup_dir.mkdir(mode=0o700,exist_ok=True)
    backup_path = backup_dir / f"before-problem-set-{signature[:12]}.db"
    if not backup_path.exists():
        backup_path.touch(mode=0o600)
        with database.connect() as source, sqlite3.connect(backup_path) as destination:
            source.backup(destination)
    with database.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        installed = db.execute("SELECT value FROM metadata WHERE key='installed_problem_set'").fetchone()
        if installed and json.loads(installed[0]) == signature:
            return
        previous = db.execute("SELECT value FROM metadata WHERE key='installed_problem_set_members'").fetchone()
        previous = json.loads(previous[0]) if previous else {}
        members = previous.get("ids", {}) if previous.get("family") == family else {}
        # Adopt the first course release, which stored its manifest hash but
        # did not yet record problem IDs. Only its exact, unique titles qualify.
        if installed and not previous and family == "sorting-course":
            for key, (problem, cases) in zip(keys, problems):
                matches = db.execute("SELECT id FROM problems WHERE title=? AND scoring_mode=?", (problem["title"], problem["scoring_mode"])).fetchall()
                if len(matches) != 1:
                    raise ValueError("Cannot identify the earlier course assignment uniquely.")
                members[key] = matches[0]["id"]
        for old in manifest.get("replace",[]):
            if db.execute("SELECT 1 FROM problems WHERE id=? AND title=?", (old["id"],old["title"])).fetchone():
                db.execute("DELETE FROM submissions WHERE problem_id=?", (old["id"],))
                db.execute("DELETE FROM testcases WHERE problem_id=?", (old["id"],))
                db.execute("DELETE FROM problems WHERE id=?", (old["id"],))
        installed_members = {}
        for key, (problem,cases) in zip(keys, problems):
            problem_id = members.get(key)
            content = (problem["title"],problem["description"],json.dumps(problem["translations"],ensure_ascii=False),problem["scoring_mode"])
            if problem_id is not None and db.execute("SELECT 1 FROM problems WHERE id=?", (problem_id,)).fetchone():
                db.execute("UPDATE problems SET title=?,description=?,translations=?,scoring_mode=? WHERE id=?", (*content,problem_id))
            else:
                cursor = db.execute("INSERT INTO problems(title,description,translations,scoring_mode,is_open,is_published,created_at) VALUES (?,?,?,?,1,?,?)", (*content,problem.get("is_published",False),time.time()))
                problem_id = cursor.lastrowid
            installed_members[key] = problem_id
            existing = {case["title"]: case["id"] for case in db.execute("SELECT id,title FROM testcases WHERE problem_id=?", (problem_id,))}
            retained = set()
            for case,original,answer,rows,columns in cases:
                values = (case["title"],original,answer,case["is_public"],rows,columns,case_digest(original,answer))
                case_id = existing.get(case["title"])
                if case_id is not None:
                    db.execute("UPDATE testcases SET title=?,input_csv=?,answer_csv=?,is_public=?,row_count=?,column_count=?,digest=? WHERE id=?", (*values,case_id))
                else:
                    case_id = db.execute("INSERT INTO testcases(problem_id,title,input_csv,answer_csv,is_public,row_count,column_count,digest,created_at) VALUES (?,?,?,?,?,?,?,?,?)", (problem_id,*values,time.time())).lastrowid
                retained.add(case_id)
            for removed in set(existing.values()) - retained:
                db.execute("DELETE FROM testcases WHERE id=?", (removed,))
        db.execute("INSERT OR REPLACE INTO metadata VALUES ('installed_problem_set',?)", (json.dumps(signature),))
        db.execute("INSERT OR REPLACE INTO metadata VALUES ('installed_problem_set_members',?)", (json.dumps({"family":family,"ids":installed_members}),))
