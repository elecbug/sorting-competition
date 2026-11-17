import json
import time
from app.auth import hash_password
from app.scoring import assign_ranks, leaderboard
from judge.profile import current_profile


def seed(app, problem_id):
    database = app.state.db
    database.set_meta("base_profile", "test-image-cpu")
    profile = current_profile(database, problem_id)
    with database.connect() as db:
        for i, (cpu, memory) in enumerate([(100,10240),(101,10752),(180,20480)]):
            uid = db.execute("INSERT INTO users(student_id,nickname,password,created_at) VALUES (?,?,?,?)", (f"student{i}",f"학생{i}","unused",time.time())).lastrowid
            result = {"passed":1,"total":1,"time_ms":cpu,"memory_kb":memory}
            db.execute("INSERT INTO submissions(user_id,problem_id,source,status,created_at,profile,result) VALUES (?,?,?,'accepted',?,?,?)",(uid,problem_id,"test",time.time(),profile,json.dumps(result)))
    return database, profile


def test_scores_and_profiles(app, problem):
    database, profile = seed(app, problem)
    results = leaderboard(database,profile,problem)
    assert results[0]["score"] == 100
    assert results[1]["time_rank"] == 1
    assert results[1]["memory_rank"] == 1
    assert results[2]["score"] < results[1]["score"]
    assert leaderboard(database,"changed-profile",problem) == []
    with database.connect() as db:
        db.execute("UPDATE testcases SET digest='changed' WHERE problem_id=?",(problem,))
    assert current_profile(database,problem) != profile


def test_latest_failed_submission_replaces_old_accepted(app, problem):
    database, profile = seed(app, problem)
    with database.connect() as db:
        uid = db.execute("SELECT id FROM users WHERE student_id='student0'").fetchone()[0]
        db.execute("INSERT INTO submissions(user_id,problem_id,source,status,created_at,profile,result) VALUES (?,?,?,'wrong_answer',?,?,?)",(uid,problem,"wrong",time.time(),profile,json.dumps({"passed":0,"total":1})))
    entries=leaderboard(database,profile,problem)
    failed=next(e for e in entries if e["nickname"]=="학생0")
    assert failed["rank"] is None and failed["score"] == 0
    assert next(e for e in entries if e["nickname"]=="학생1")["rank"] == 1


def test_ties_use_anchor_and_competition_ranks():
    entries = [{"time":n} for n in [100,101,102,120]]
    assign_ranks(entries,"time","rank",relative=.01)
    assert [e["rank"] for e in entries] == [1,1,3,4]
