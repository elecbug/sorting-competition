import json

FINISHED = ("accepted", "wrong_answer", "compile_error", "time_limit", "memory_limit", "runtime_error", "output_limit")


def leaderboard(database, profile, problem_id):
    if not profile:
        return []
    with database.connect() as db:
        problem = db.execute("SELECT scoring_mode FROM problems WHERE id=?", (problem_id,)).fetchone()
        mode = problem["scoring_mode"] if problem else "balanced"
        rows = db.execute("""
        SELECT s.id, s.user_id, s.status, s.result, s.created_at, u.nickname, u.student_id
        FROM submissions s JOIN users u ON u.id=s.user_id
        WHERE s.profile=? AND s.problem_id=? AND s.status='accepted'
        AND u.role='student'
        AND s.id=(SELECT MAX(s2.id) FROM submissions s2 WHERE s2.user_id=s.user_id AND s2.problem_id=s.problem_id AND s2.profile=s.profile
          AND s2.status IN ('accepted','wrong_answer','compile_error','time_limit','memory_limit','runtime_error','output_limit'))
        """, (profile, problem_id)).fetchall()
    entries = []
    for row in rows:
        result = json.loads(row["result"] or "{}")
        entries.append({
            "submission_id": row["id"], "nickname": row["nickname"], "student_id": row["student_id"], "status": row["status"],
            "created_at": row["created_at"], "time_ms": result.get("time_ms"),
            "memory_kb": result.get("memory_kb"), "passed": result.get("passed", 0),
            "total": result.get("total", 12), "rank": None, "time_rank": None, "memory_rank": None,
        })
    accepted = [e for e in entries if e["status"] == "accepted"]
    best_time = min((e["time_ms"] for e in accepted), default=1)
    best_memory = min((e["memory_kb"] for e in accepted), default=1)
    for e in entries:
        e["correctness_score"] = round(60 * e["passed"] / e["total"], 2)
        e["time_score"] = round((0 if mode == "memory" else 40 if mode == "time" else 30) * best_time / max(e["time_ms"], 0.001), 2) if e["status"] == "accepted" else 0
        e["memory_score"] = round((40 if mode == "memory" else 0 if mode == "time" else 10) * best_memory / max(e["memory_kb"], 1), 2) if e["status"] == "accepted" else 0
        e["score"] = round(e["correctness_score"] + e["time_score"] + e["memory_score"], 2)
    assign_ranks(accepted, "score", "rank", descending=True, absolute=0.1)
    assign_ranks(accepted, "time_ms", "time_rank", relative=0.01)
    assign_ranks(accepted, "memory_kb", "memory_rank", absolute=64 if mode == "memory" else 1024)
    # Measurements within the published tolerance receive equal performance
    # points, rather than only sharing a rank with slightly different scores.
    times = {e["time_rank"]: min(x["time_ms"] for x in accepted if x["time_rank"] == e["time_rank"]) for e in accepted}
    memories = {e["memory_rank"]: min(x["memory_kb"] for x in accepted if x["memory_rank"] == e["memory_rank"]) for e in accepted}
    for e in accepted:
        e["time_score"] = round((0 if mode == "memory" else 40 if mode == "time" else 30)*best_time/max(times[e["time_rank"]],.001),2)
        e["memory_score"] = round((40 if mode == "memory" else 0 if mode == "time" else 10)*best_memory/max(memories[e["memory_rank"]],1),2)
        e["score"] = round(e["correctness_score"]+e["time_score"]+e["memory_score"],2)
    assign_ranks(accepted, "score", "rank", descending=True, absolute=.1)
    if mode != "balanced":
        for entry in accepted:
            entry["rank"] = entry["memory_rank" if mode == "memory" else "time_rank"]
    return sorted(entries, key=lambda e: (e["rank"] is None, e["rank"] or 0, -e["score"], e["created_at"]))


def assign_ranks(entries, key, rank_key, *, descending=False, absolute=0, relative=0):
    ordered = sorted(entries, key=lambda e: e[key], reverse=descending)
    anchor, rank = None, 0
    for position, entry in enumerate(ordered, 1):
        value = entry[key]
        if anchor is None or abs(value - anchor) > max(absolute, abs(anchor) * relative) + 1e-9:
            anchor, rank = value, position
        entry[rank_key] = rank
