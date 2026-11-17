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
        for old in manifest.get("replace",[]):
            if db.execute("SELECT 1 FROM problems WHERE id=? AND title=?", (old["id"],old["title"])).fetchone():
                db.execute("DELETE FROM submissions WHERE problem_id=?", (old["id"],))
                db.execute("DELETE FROM testcases WHERE problem_id=?", (old["id"],))
                db.execute("DELETE FROM problems WHERE id=?", (old["id"],))
        for problem,cases in problems:
            cursor = db.execute("INSERT INTO problems(title,description,translations,is_open,is_published,scoring_mode,created_at) VALUES (?,?,?,1,?,?,?)", (problem["title"],problem["description"],json.dumps(problem["translations"],ensure_ascii=False),problem.get("is_published",False),problem["scoring_mode"],time.time()))
            for case,original,answer,rows,columns in cases:
                db.execute("INSERT INTO testcases(problem_id,title,input_csv,answer_csv,is_public,row_count,column_count,digest,created_at) VALUES (?,?,?,?,?,?,?,?,?)", (cursor.lastrowid,case["title"],original,answer,case["is_public"],rows,columns,case_digest(original,answer),time.time()))
        db.execute("INSERT OR REPLACE INTO metadata VALUES ('installed_problem_set',?)", (json.dumps(signature),))
