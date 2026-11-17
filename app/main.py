import csv
import io
import json
import secrets
import time
from urllib.parse import urlsplit
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from jinja2 import Environment, FileSystemLoader, select_autoescape
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.auth import hash_password, token_hash, verify_password
from app.config import ROOT, Settings
from app.db import Database
from app.scoring import leaderboard
from judge.datasets import validate_pair
from judge.profile import case_digest, current_profile


class BodyLimit:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] not in ("POST", "PATCH", "PUT"):
            return await self.app(scope, receive, send)
        maximum = 80 * 1024**2 if scope["path"].endswith("/testcases") else 256 * 1024
        body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            body.extend(message.get("body", b""))
            if len(body) > maximum:
                return await JSONResponse({"detail": "Request body is too large."}, status_code=413)(scope, receive, send)
            if not message.get("more_body", False):
                break
        sent = False
        async def replay():
            nonlocal sent
            if not sent:
                sent = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return await receive()
        await self.app(scope, replay, send)


class Payload(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Login(Payload):
    student_id: str = Field(pattern=r"^[A-Za-z0-9_-]{3,30}$")
    password: str = Field(min_length=1, max_length=128)

    @field_validator("student_id", mode="before")
    @classmethod
    def strip_student_id(cls, value):
        return value.strip() if isinstance(value, str) else value


class StudentCredentials(Login):
    password: str = Field(min_length=4, max_length=128)


class IdentityCorrection(StudentCredentials):
    pass


class Submission(Payload):
    problem_id: int
    source: str = Field(min_length=1, max_length=65536)
    student_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{3,30}$")
    password: str | None = Field(default=None, min_length=4, max_length=128)

    @field_validator("student_id", mode="before")
    @classmethod
    def strip_student_id(cls, value):
        return value.strip() if isinstance(value, str) else value


class ProblemTranslation(Payload):
    title: str = Field(default="", max_length=100)
    description: str = Field(default="", max_length=10000)


class ProblemPayload(Payload):
    title: str = Field(min_length=1, max_length=100)
    description: str = Field(min_length=1, max_length=10000)
    is_open: bool = True
    is_published: bool = False
    scoring_mode: Literal["balanced", "memory", "time"] = "balanced"
    translations: dict[Literal["ko", "zh", "vi", "ru"], ProblemTranslation] = Field(default_factory=dict)


class TestcasePayload(Payload):
    title: str = Field(min_length=1, max_length=80)
    input_csv: str = Field(max_length=32 * 1024**2)
    answer_csv: str = Field(max_length=32 * 1024**2)
    is_public: bool = True


class OpenPayload(Payload):
    is_open: bool


class PublicationPayload(Payload):
    is_published: bool


class ClearSubmissions(Payload):
    confirmation: Literal["CLEAR"]


def current_user(request: Request):
    cookie = request.cookies.get("sorting_session", "")
    if not cookie:
        return None
    with request.app.state.db.connect() as db:
        user = db.execute("SELECT u.* FROM sessions s JOIN users u ON s.user_id=u.id WHERE s.token_hash=? AND s.expires_at>?", (token_hash(cookie), time.time())).fetchone()
    return dict(user) if user else None


def require_user(user=Depends(current_user)):
    if user is None:
        raise HTTPException(401, "Sign in to continue.")
    return user


def require_admin(user=Depends(require_user)):
    if user["role"] != "admin":
        raise HTTPException(403, "Administrator access required.")
    return user


def public_user(user):
    return {k: user[k] for k in ("id", "student_id", "nickname", "role")} if user else None


def worker_status(database):
    state = database.get_meta("worker", {"status": "offline", "message": "Judge worker is not connected.", "updated_at": 0})
    if time.time() - state["updated_at"] > 20:
        state.update(status="offline", message="Judge worker is offline. Submissions remain queued.")
    return state


def create_app(settings=None):
    settings = settings or Settings.from_env()
    if not settings.admin_password:
        raise RuntimeError("Set SORT_ADMIN_PASSWORD in .env or run scripts/setup.py.")
    database = Database(settings.data_dir)
    from app.problem_sets import install_pending
    install_pending(database, settings.data_dir)
    with database.connect() as db:
        admin = db.execute("SELECT * FROM users WHERE student_id='admin'").fetchone()
        if not admin:
            db.execute("INSERT INTO users(student_id,nickname,password,role,created_at) VALUES ('admin','Administrator',?,'admin',?)", (hash_password(settings.admin_password), time.time()))
        elif not verify_password(settings.admin_password, admin["password"]):
            db.execute("UPDATE users SET password=? WHERE id=?", (hash_password(settings.admin_password), admin["id"]))
            db.execute("DELETE FROM sessions WHERE user_id=?", (admin["id"],))
        db.execute("UPDATE users SET nickname='Administrator' WHERE student_id='admin'")
    dummy_password = hash_password(secrets.token_urlsafe(20))
    app = FastAPI(title="Sorting Competition", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.db = database
    app.state.settings = settings
    app.add_middleware(BodyLimit)
    app.mount("/static", StaticFiles(directory=ROOT / "app/static"), name="static")
    templates = Environment(loader=FileSystemLoader(ROOT / "app/templates"), autoescape=select_autoescape())

    @app.exception_handler(RequestValidationError)
    def validation_error(request, error):
        # Pydantic's default error body echoes input, including personal passwords.
        details = [{key:item[key] for key in ("type", "loc", "msg")} for item in error.errors()]
        return JSONResponse({"detail": details}, status_code=422)

    @app.middleware("http")
    async def security(request, call_next):
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            origin = request.headers.get("origin")
            if request.headers.get("x-requested-with") != "SortingLab" or (origin and urlsplit(origin).netloc != request.headers.get("host")):
                return JSONResponse({"detail": "Invalid request origin."}, status_code=403)
            if request.url.path.endswith("/testcases"):
                user = current_user(request)
                if not user or user["role"] != "admin":
                    return JSONResponse({"detail": "Administrator access required."}, status_code=403)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    def problem_or_404(problem_id, user=None):
        with database.connect() as db:
            row = db.execute("SELECT * FROM problems WHERE id=?", (problem_id,)).fetchone()
        if not row or (not row["is_published"] and (not user or user["role"] != "admin")):
            raise HTTPException(404, "Problem not found.")
        return dict(row)

    def profile_for(problem_id):
        return current_profile(database, problem_id)

    def limit_student_attempts(request, student_id):
        ip = request.client.host if request.client else "unknown"
        if not database.rate_limit(f"student-ip:{ip}", 300, 60) or not database.rate_limit(f"student-id:{student_id}", 30, 300):
            raise HTTPException(429, "Too many verification attempts. Try again in 5 minutes.")

    def session_response(user, request, content, status_code=200):
        token = secrets.token_urlsafe(32)
        with database.connect() as db:
            db.execute("DELETE FROM sessions WHERE expires_at<? OR token_hash=?", (time.time(), token_hash(request.cookies.get("sorting_session", ""))))
            db.execute("INSERT INTO sessions VALUES (?,?,?)", (token_hash(token), user["id"], time.time() + 43200))
        response = JSONResponse({**content, "user": public_user(user)}, status_code=status_code)
        response.set_cookie("sorting_session", token, httponly=True, secure=settings.cookie_secure, samesite="strict", max_age=43200)
        return response

    @app.get("/", response_class=HTMLResponse)
    def home():
        return templates.get_template("index.html").render()

    @app.get("/api/health")
    def health():
        worker = worker_status(database)
        return {"web": "ok", "worker": worker, "ready": worker["status"] in ("ready", "running")}

    @app.get("/api/me")
    def me(user=Depends(current_user)):
        return {"user": public_user(user)}

    @app.post("/api/login")
    def login(payload: Login, request: Request):
        ip = request.client.host if request.client else "unknown"
        if not database.rate_limit(f"login-ip:{ip}", 100, 300) or not database.rate_limit(f"login-user:{payload.student_id}", 15, 300):
            raise HTTPException(429, "Too many sign-in attempts. Try again in 5 minutes.")
        with database.connect() as db:
            user = db.execute("SELECT * FROM users WHERE student_id=?", (payload.student_id,)).fetchone()
        if not verify_password(payload.password, user["password"] if user else dummy_password) or not user or user["role"] != "admin":
            raise HTTPException(401, "Incorrect student ID or password.")
        return session_response(user, request, {})

    @app.post("/api/student/session")
    def student_session(payload: StudentCredentials, request: Request):
        limit_student_attempts(request, payload.student_id)
        with database.connect() as db:
            user = db.execute("SELECT * FROM users WHERE student_id=?", (payload.student_id,)).fetchone()
        if not verify_password(payload.password, user["password"] if user else dummy_password) or not user or user["role"] != "student":
            raise HTTPException(401, "Student ID or personal password is incorrect.")
        return session_response(user, request, {})

    @app.post("/api/logout")
    def logout(request: Request):
        with database.connect() as db:
            db.execute("DELETE FROM sessions WHERE token_hash=?", (token_hash(request.cookies.get("sorting_session", "")),))
        response = JSONResponse({"ok": True})
        response.delete_cookie("sorting_session", secure=settings.cookie_secure, httponly=True, samesite="strict")
        return response

    @app.get("/api/problems")
    def problems(user=Depends(current_user)):
        with database.connect() as db:
            items = [dict(r) for r in db.execute("""SELECT p.*, (SELECT COUNT(*) FROM testcases t WHERE t.problem_id=p.id) test_count,
                (SELECT COUNT(DISTINCT s.user_id) FROM submissions s JOIN users u ON u.id=s.user_id WHERE s.problem_id=p.id AND u.role='student') participants
                FROM problems p WHERE p.is_published=1 OR ? ORDER BY p.id""", (bool(user and user["role"] == "admin"),))]
        for item in items:
            item["translations"] = json.loads(item["translations"])
        return {"problems": items, "worker": worker_status(database), "submissions_open": database.get_meta("submissions_open", True),
                "limits": {"repetitions": settings.repetitions, "time_seconds": settings.time_limit, "memory_mb": settings.memory_mb}}

    @app.get("/api/problems/{problem_id}")
    def problem_detail(problem_id: int, user=Depends(current_user)):
        problem = problem_or_404(problem_id, user)
        problem["translations"] = json.loads(problem["translations"])
        with database.connect() as db:
            cases = [dict(r) for r in db.execute("SELECT id,title,is_public,row_count,column_count FROM testcases WHERE problem_id=? ORDER BY id", (problem_id,))]
        problem["testcases"] = cases
        problem["profile"] = profile_for(problem_id)
        return problem

    @app.get("/api/problems/{problem_id}/leaderboard")
    def ranks(problem_id: int, user=Depends(current_user)):
        problem = problem_or_404(problem_id, user)
        profile = profile_for(problem_id)
        return {"entries": leaderboard(database, profile, problem_id), "profile": profile, "scoring_mode": problem["scoring_mode"]}

    @app.get("/api/problems/{problem_id}/leaderboard.csv")
    def ranks_csv(problem_id: int, user=Depends(require_admin)):
        problem_or_404(problem_id, user)
        output = io.StringIO(newline="")
        fields = ["rank", "student_id", "score", "correctness_score", "time_score", "memory_score", "time_ms", "memory_kb", "time_rank", "memory_rank", "status", "submission_id"]
        writer = csv.DictWriter(output, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for entry in leaderboard(database, profile_for(problem_id), problem_id):
            if entry["student_id"].startswith(("=", "+", "-", "@")):
                entry["student_id"] = "'" + entry["student_id"]
            writer.writerow(entry)
        return Response("\ufeff" + output.getvalue(), media_type="text/csv; charset=utf-8", headers={"Content-Disposition": f'attachment; filename="leaderboard-{problem_id}.csv"'})

    @app.get("/api/problems/{problem_id}/testcases/{case_id}/input.csv")
    def download_input(problem_id: int, case_id: int, user=Depends(current_user)):
        problem_or_404(problem_id, user)
        with database.connect() as db:
            case = db.execute("SELECT * FROM testcases WHERE id=? AND problem_id=?", (case_id, problem_id)).fetchone()
        if not case or (not case["is_public"] and (not user or user["role"] != "admin")):
            raise HTTPException(404, "Public input file not found.")
        return Response(case["input_csv"], media_type="text/csv; charset=utf-8", headers={"Content-Disposition": f'attachment; filename="problem-{problem_id}-input-{case_id}.csv"'})

    @app.get("/api/download/{filename}")
    def download_template(filename: str):
        if filename not in ("student_sort.c", "csv_sort.h"):
            raise HTTPException(404)
        return FileResponse(ROOT / "examples" / filename, filename=filename, media_type="text/plain")

    @app.post("/api/submissions", status_code=201)
    def submit(payload: Submission, request: Request, user=Depends(current_user)):
        if len(payload.source.encode()) > 65536:
            raise HTTPException(400, "Source code must not exceed 64 KiB in UTF-8.")
        as_admin = user and user["role"] == "admin" and payload.student_id is None and payload.password is None
        if not as_admin:
            if payload.student_id is None or payload.password is None:
                raise HTTPException(400, "Enter your student ID and personal password.")
            limit_student_attempts(request, payload.student_id)
        with database.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if not as_admin:
                existing = db.execute("SELECT * FROM users WHERE student_id=?", (payload.student_id,)).fetchone()
                if existing:
                    if not verify_password(payload.password, existing["password"]) or existing["role"] != "student":
                        raise HTTPException(401, "This student ID uses a different personal password. Check your ID or contact the instructor.")
                    user = dict(existing)
                else:
                    cursor = db.execute("INSERT INTO users(student_id,nickname,password,created_at) VALUES (?,?,?,?)", (payload.student_id, payload.student_id, hash_password(payload.password), time.time()))
                    user = dict(db.execute("SELECT * FROM users WHERE id=?", (cursor.lastrowid,)).fetchone())
            problem = db.execute("SELECT * FROM problems WHERE id=?", (payload.problem_id,)).fetchone()
            opened = json.loads(db.execute("SELECT value FROM metadata WHERE key='submissions_open'").fetchone()[0])
            if not problem or (not problem["is_published"] and not as_admin) or not problem["is_open"] or not opened:
                raise HTTPException(400, "Submissions are closed for this problem.")
            if not db.execute("SELECT 1 FROM testcases WHERE problem_id=?", (payload.problem_id,)).fetchone():
                raise HTTPException(400, "No test data has been registered.")
            pending = db.execute("SELECT COUNT(*) FROM submissions WHERE user_id=? AND status IN ('queued','running')", (user["id"],)).fetchone()[0]
            if pending >= 2:
                raise HTTPException(429, "You can have at most 2 pending submissions.")
            last = db.execute("SELECT MAX(created_at) FROM submissions WHERE user_id=?", (user["id"],)).fetchone()[0]
            if last and time.time() - last < 20:
                raise HTTPException(429, "Wait 20 seconds between submissions.")
            if db.execute("SELECT COUNT(*) FROM submissions WHERE status IN ('queued','running')").fetchone()[0] >= 200:
                raise HTTPException(429, "The judge queue is full. Try again later.")
            highest = int(db.execute("SELECT value FROM metadata WHERE key='submission_high_watermark'").fetchone()[0])
            current_max = db.execute("SELECT COALESCE(MAX(id),0) FROM submissions").fetchone()[0]
            submission_id = max(highest, current_max) + 1
            db.execute("UPDATE metadata SET value=? WHERE key='submission_high_watermark'", (str(submission_id),))
            db.execute("INSERT INTO submissions(id,user_id,problem_id,source,created_at) VALUES (?,?,?,?,?)", (submission_id, user["id"], payload.problem_id, payload.source, time.time()))
        return session_response(user, request, {"id": submission_id, "status": "queued"}, status_code=201)

    @app.get("/api/submissions")
    def submissions(problem_id: int | None = None, user=Depends(require_user)):
        conditions, params = [], []
        if user["role"] != "admin":
            conditions.append("s.user_id=?")
            params.append(user["id"])
            conditions.append("s.problem_id IN (SELECT id FROM problems WHERE is_published=1)")
        if problem_id is not None:
            conditions.append("s.problem_id=?")
            params.append(problem_id)
        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        with database.connect() as db:
            rows = db.execute("SELECT s.id,s.problem_id,s.status,s.created_at,s.finished_at,s.profile,s.result,u.student_id FROM submissions s JOIN users u ON u.id=s.user_id" + where + " ORDER BY s.id DESC LIMIT 100", params).fetchall()
        return {"submissions": [{**dict(r), "result": json.loads(r["result"]) if r["result"] else None} for r in rows]}

    @app.get("/api/submissions/{submission_id}")
    def submission_detail(submission_id: int, user=Depends(require_user)):
        with database.connect() as db:
            row = db.execute("SELECT * FROM submissions WHERE id=?", (submission_id,)).fetchone()
        if not row or (row["user_id"] != user["id"] and user["role"] != "admin"):
            raise HTTPException(404, "Submission not found.")
        problem_or_404(row["problem_id"], user)
        return {**dict(row), "result": json.loads(row["result"]) if row["result"] else None}

    @app.get("/api/admin/settings")
    def admin_settings(user=Depends(require_admin)):
        with database.connect() as db:
            users = [dict(r) for r in db.execute("SELECT id,student_id,created_at FROM users WHERE role='student' ORDER BY created_at DESC LIMIT 500")]
            summary = dict(db.execute("SELECT COUNT(*) total, COALESCE(SUM(s.status='running'),0) running, COALESCE(SUM(s.status='queued'),0) queued FROM submissions s JOIN users u ON u.id=s.user_id WHERE u.role='student'").fetchone())
        return {"submissions_open": database.get_meta("submissions_open", True), "users": users, "submission_summary": summary, "worker": worker_status(database)}

    @app.post("/api/admin/submissions/clear")
    def clear_student_submissions(payload: ClearSubmissions, admin=Depends(require_admin)):
        with database.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            deleted = db.execute("DELETE FROM submissions WHERE user_id IN (SELECT id FROM users WHERE role='student')").rowcount
            db.execute("DELETE FROM benchmark_sources")
            db.execute("DELETE FROM benchmark_cache")
        return {"ok": True, "deleted": deleted}

    @app.patch("/api/admin/students/{student_user_id}")
    def correct_student_id(student_user_id: int, payload: IdentityCorrection, admin=Depends(require_admin)):
        if not database.rate_limit(f"identity-check:{admin['id']}:{student_user_id}", 15, 300):
            raise HTTPException(429, "Too many verification attempts. Try again in 5 minutes.")
        with database.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            student = db.execute("SELECT * FROM users WHERE id=? AND role='student'", (student_user_id,)).fetchone()
            if not student:
                raise HTTPException(404, "Student not found.")
            if not verify_password(payload.password, student["password"]):
                raise HTTPException(401, "Personal password did not match. Student ID was not changed.")
            if db.execute("SELECT 1 FROM users WHERE student_id=? AND id!=?", (payload.student_id, student_user_id)).fetchone():
                raise HTTPException(409, "The corrected student ID is already in use. No records were merged.")
            if student["student_id"] != payload.student_id:
                db.execute("UPDATE users SET student_id=?,nickname=? WHERE id=?", (payload.student_id, payload.student_id, student_user_id))
                db.execute("DELETE FROM sessions WHERE user_id=?", (student_user_id,))
                db.execute("INSERT INTO identity_events(user_id,admin_id,old_student_id,new_student_id,created_at) VALUES (?,?,?,?,?)", (student_user_id, admin["id"], student["student_id"], payload.student_id, time.time()))
        return {"ok": True, "student_id": payload.student_id}

    @app.post("/api/admin/submissions-open")
    def toggle_open(payload: OpenPayload, user=Depends(require_admin)):
        database.set_meta("submissions_open", payload.is_open)
        return {"ok": True}

    @app.post("/api/admin/problems", status_code=201)
    def create_problem(payload: ProblemPayload, user=Depends(require_admin)):
        with database.connect() as db:
            cursor = db.execute("INSERT INTO problems(title,description,is_open,is_published,scoring_mode,translations,created_at) VALUES (?,?,?,?,?,?,?)", (payload.title, payload.description, payload.is_open, payload.is_published, payload.scoring_mode, json.dumps({k:v.model_dump() for k,v in payload.translations.items()}, ensure_ascii=False), time.time()))
            problem_id = cursor.lastrowid
        return {"id": problem_id}

    @app.patch("/api/admin/problems/{problem_id}")
    def edit_problem(problem_id: int, payload: ProblemPayload, user=Depends(require_admin)):
        problem_or_404(problem_id, user)
        with database.connect() as db:
            db.execute("UPDATE problems SET title=?,description=?,is_open=?,is_published=?,scoring_mode=?,translations=? WHERE id=?", (payload.title, payload.description, payload.is_open, payload.is_published, payload.scoring_mode, json.dumps({k:v.model_dump() for k,v in payload.translations.items()}, ensure_ascii=False), problem_id))
        return {"ok": True}

    @app.patch("/api/admin/problems/{problem_id}/publication")
    def publish_problem(problem_id: int, payload: PublicationPayload, admin=Depends(require_admin)):
        problem_or_404(problem_id, admin)
        with database.connect() as db:
            db.execute("UPDATE problems SET is_published=? WHERE id=?", (payload.is_published, problem_id))
        return {"ok": True, "is_published": payload.is_published}

    @app.post("/api/admin/problems/{problem_id}/testcases", status_code=201)
    def create_case(problem_id: int, payload: TestcasePayload, user=Depends(require_admin)):
        problem_or_404(problem_id, user)
        try:
            rows, columns = validate_pair(payload.input_csv, payload.answer_csv)
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        with database.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT COUNT(*) FROM testcases WHERE problem_id=?", (problem_id,)).fetchone()[0] >= 10:
                raise HTTPException(400, "Each problem supports at most 10 test pairs.")
            current_bytes = db.execute("SELECT COALESCE(SUM(length(CAST(input_csv AS BLOB))+length(CAST(answer_csv AS BLOB))),0) FROM testcases WHERE problem_id=?", (problem_id,)).fetchone()[0]
            if current_bytes + len(payload.input_csv.encode()) + len(payload.answer_csv.encode()) > 128 * 1024**2:
                raise HTTPException(400, "Total input and answer data per problem must not exceed 128 MiB.")
            cursor = db.execute("INSERT INTO testcases(problem_id,title,input_csv,answer_csv,is_public,row_count,column_count,digest,created_at) VALUES (?,?,?,?,?,?,?,?,?)", (problem_id, payload.title, payload.input_csv, payload.answer_csv, payload.is_public, rows, columns, case_digest(payload.input_csv, payload.answer_csv), time.time()))
            case_id = cursor.lastrowid
        return {"id": case_id, "rows": rows, "columns": columns}

    @app.delete("/api/admin/problems/{problem_id}/testcases/{case_id}")
    def delete_case(problem_id: int, case_id: int, user=Depends(require_admin)):
        with database.connect() as db:
            cursor = db.execute("DELETE FROM testcases WHERE id=? AND problem_id=?", (case_id, problem_id))
        if not cursor.rowcount:
            raise HTTPException(404, "Test case not found.")
        return {"ok": True}

    @app.post("/api/admin/submissions/{submission_id}/rejudge")
    def rejudge(submission_id: int, user=Depends(require_admin)):
        with database.connect() as db:
            row = db.execute("SELECT status FROM submissions WHERE id=?", (submission_id,)).fetchone()
            if not row:
                raise HTTPException(404, "Submission not found.")
            if row["status"] in ("queued", "running"):
                raise HTTPException(400, "This submission is already queued or running.")
            db.execute("UPDATE submissions SET status='queued',result=NULL,profile=NULL,started_at=NULL,finished_at=NULL WHERE id=?", (submission_id,))
        return {"ok": True}

    return app
