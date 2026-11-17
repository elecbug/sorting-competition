from contextlib import asynccontextmanager
import csv
import hmac
import io
import json
import secrets
import sqlite3
import time
from urllib.parse import urlsplit

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from jinja2 import Environment, FileSystemLoader, select_autoescape
from pydantic import BaseModel, ConfigDict, Field

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
                return await JSONResponse({"detail": "요청 파일이 너무 큽니다."}, status_code=413)(scope, receive, send)
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
    password: str = Field(min_length=10, max_length=128)


class Registration(Login):
    nickname: str = Field(min_length=2, max_length=30)
    enrollment_code: str = Field(min_length=1, max_length=128)


class Submission(Payload):
    problem_id: int
    source: str = Field(min_length=1, max_length=65536)


class ProblemPayload(Payload):
    title: str = Field(min_length=1, max_length=100)
    description: str = Field(min_length=1, max_length=10000)
    is_open: bool = True


class TestcasePayload(Payload):
    title: str = Field(min_length=1, max_length=80)
    input_csv: str = Field(max_length=32 * 1024**2)
    answer_csv: str = Field(max_length=32 * 1024**2)
    is_public: bool = True


class OpenPayload(Payload):
    is_open: bool


def current_user(request: Request):
    cookie = request.cookies.get("sorting_session", "")
    if not cookie:
        return None
    with request.app.state.db.connect() as db:
        user = db.execute("SELECT u.* FROM sessions s JOIN users u ON s.user_id=u.id WHERE s.token_hash=? AND s.expires_at>?", (token_hash(cookie), time.time())).fetchone()
    return dict(user) if user else None


def require_user(user=Depends(current_user)):
    if user is None:
        raise HTTPException(401, "로그인이 필요합니다.")
    return user


def require_admin(user=Depends(require_user)):
    if user["role"] != "admin":
        raise HTTPException(403, "관리자만 사용할 수 있습니다.")
    return user


def public_user(user):
    return {k: user[k] for k in ("id", "student_id", "nickname", "role")} if user else None


def worker_status(database):
    state = database.get_meta("worker", {"status": "offline", "message": "채점기가 아직 연결되지 않았습니다.", "updated_at": 0})
    if time.time() - state["updated_at"] > 20:
        state.update(status="offline", message="채점기가 연결되지 않았습니다. 제출물은 대기열에 보관됩니다.")
    return state


def create_app(settings=None):
    settings = settings or Settings.from_env()
    database = Database(settings.data_dir)
    with database.connect() as db:
        admin = db.execute("SELECT * FROM users WHERE student_id='admin'").fetchone()
        if not admin:
            db.execute("INSERT INTO users(student_id,nickname,password,role,created_at) VALUES ('admin','운영자',?,'admin',?)", (hash_password(settings.admin_password), time.time()))
        elif not verify_password(settings.admin_password, admin["password"]):
            db.execute("UPDATE users SET password=? WHERE id=?", (hash_password(settings.admin_password), admin["id"]))
            db.execute("DELETE FROM sessions WHERE user_id=?", (admin["id"],))
    dummy_password = hash_password(secrets.token_urlsafe(20))
    app = FastAPI(title="SORT / CSV Sorting Arena", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.db = database
    app.state.settings = settings
    app.add_middleware(BodyLimit)
    app.mount("/static", StaticFiles(directory=ROOT / "app/static"), name="static")
    templates = Environment(loader=FileSystemLoader(ROOT / "app/templates"), autoescape=select_autoescape())

    @app.middleware("http")
    async def security(request, call_next):
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            origin = request.headers.get("origin")
            if request.headers.get("x-requested-with") != "SortingLab" or (origin and urlsplit(origin).netloc != request.headers.get("host")):
                return JSONResponse({"detail": "요청 출처를 확인할 수 없습니다."}, status_code=403)
            if request.url.path.endswith("/testcases"):
                user = current_user(request)
                if not user or user["role"] != "admin":
                    return JSONResponse({"detail": "관리자만 사용할 수 있습니다."}, status_code=403)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    def problem_or_404(problem_id):
        with database.connect() as db:
            row = db.execute("SELECT * FROM problems WHERE id=?", (problem_id,)).fetchone()
        if not row:
            raise HTTPException(404, "문제를 찾을 수 없습니다.")
        return dict(row)

    def profile_for(problem_id):
        return current_profile(database, problem_id)

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

    @app.post("/api/register", status_code=201)
    def register(payload: Registration, request: Request):
        ip = request.client.host if request.client else "unknown"
        if not database.rate_limit(f"register:{ip}", 10, 3600):
            raise HTTPException(429, "가입 요청이 많습니다. 잠시 후 다시 시도하세요.")
        if not hmac.compare_digest(payload.enrollment_code.encode(), settings.enrollment_code.encode()):
            raise HTTPException(400, "수업 참여 코드가 올바르지 않습니다.")
        nickname = payload.nickname.strip()
        if len(nickname) < 2:
            raise HTTPException(400, "표시 이름을 2자 이상 입력하세요.")
        try:
            with database.connect() as db:
                db.execute("INSERT INTO users(student_id,nickname,password,created_at) VALUES (?,?,?,?)", (payload.student_id, nickname, hash_password(payload.password), time.time()))
        except sqlite3.IntegrityError:
            raise HTTPException(409, "이미 등록된 학번/아이디입니다.")
        return {"ok": True}

    @app.post("/api/login")
    def login(payload: Login, request: Request):
        ip = request.client.host if request.client else "unknown"
        if not database.rate_limit(f"login-ip:{ip}", 100, 300) or not database.rate_limit(f"login-user:{payload.student_id}", 15, 300):
            raise HTTPException(429, "로그인 시도가 많습니다. 5분 후 다시 시도하세요.")
        with database.connect() as db:
            user = db.execute("SELECT * FROM users WHERE student_id=?", (payload.student_id,)).fetchone()
        if not verify_password(payload.password, user["password"] if user else dummy_password) or not user:
            raise HTTPException(401, "학번/아이디 또는 비밀번호를 확인하세요.")
        token = secrets.token_urlsafe(32)
        lifetime = 43200 if user["role"] == "admin" else 604800
        with database.connect() as db:
            db.execute("DELETE FROM sessions WHERE expires_at<? OR token_hash=?", (time.time(), token_hash(request.cookies.get("sorting_session", ""))))
            db.execute("INSERT INTO sessions VALUES (?,?,?)", (token_hash(token), user["id"], time.time() + lifetime))
        response = JSONResponse({"user": public_user(user)})
        response.set_cookie("sorting_session", token, httponly=True, secure=settings.cookie_secure, samesite="strict", max_age=lifetime)
        return response

    @app.post("/api/logout")
    def logout(request: Request):
        with database.connect() as db:
            db.execute("DELETE FROM sessions WHERE token_hash=?", (token_hash(request.cookies.get("sorting_session", "")),))
        response = JSONResponse({"ok": True})
        response.delete_cookie("sorting_session", secure=settings.cookie_secure, httponly=True, samesite="strict")
        return response

    @app.get("/api/problems")
    def problems():
        with database.connect() as db:
            items = [dict(r) for r in db.execute("""SELECT p.*, (SELECT COUNT(*) FROM testcases t WHERE t.problem_id=p.id) test_count,
                (SELECT COUNT(DISTINCT s.user_id) FROM submissions s JOIN users u ON u.id=s.user_id WHERE s.problem_id=p.id AND u.role='student') participants
                FROM problems p ORDER BY p.id""")]
        return {"problems": items, "worker": worker_status(database), "submissions_open": database.get_meta("submissions_open", True),
                "limits": {"repetitions": settings.repetitions, "time_seconds": settings.time_limit, "memory_mb": settings.memory_mb}}

    @app.get("/api/problems/{problem_id}")
    def problem_detail(problem_id: int):
        problem = problem_or_404(problem_id)
        with database.connect() as db:
            cases = [dict(r) for r in db.execute("SELECT id,title,is_public,row_count,column_count FROM testcases WHERE problem_id=? ORDER BY id", (problem_id,))]
        problem["testcases"] = cases
        problem["profile"] = profile_for(problem_id)
        return problem

    @app.get("/api/problems/{problem_id}/leaderboard")
    def ranks(problem_id: int):
        problem_or_404(problem_id)
        profile = profile_for(problem_id)
        return {"entries": leaderboard(database, profile, problem_id), "profile": profile}

    @app.get("/api/problems/{problem_id}/leaderboard.csv")
    def ranks_csv(problem_id: int, user=Depends(require_admin)):
        problem_or_404(problem_id)
        output = io.StringIO(newline="")
        fields = ["rank", "nickname", "score", "correctness_score", "time_score", "memory_score", "time_ms", "memory_kb", "time_rank", "memory_rank", "status", "submission_id"]
        writer = csv.DictWriter(output, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for entry in leaderboard(database, profile_for(problem_id), problem_id):
            # Spreadsheet formula injection protection for student-controlled names.
            if entry["nickname"].lstrip().startswith(("=", "+", "-", "@")):
                entry["nickname"] = "'" + entry["nickname"]
            writer.writerow(entry)
        return Response("\ufeff" + output.getvalue(), media_type="text/csv; charset=utf-8", headers={"Content-Disposition": f'attachment; filename="leaderboard-{problem_id}.csv"'})

    @app.get("/api/problems/{problem_id}/testcases/{case_id}/input.csv")
    def download_input(problem_id: int, case_id: int, user=Depends(current_user)):
        with database.connect() as db:
            case = db.execute("SELECT * FROM testcases WHERE id=? AND problem_id=?", (case_id, problem_id)).fetchone()
        if not case or (not case["is_public"] and (not user or user["role"] != "admin")):
            raise HTTPException(404, "공개 원본 파일이 없습니다.")
        return Response(case["input_csv"], media_type="text/csv; charset=utf-8", headers={"Content-Disposition": f'attachment; filename="problem-{problem_id}-input-{case_id}.csv"'})

    @app.get("/api/download/{filename}")
    def download_template(filename: str):
        if filename not in ("student_sort.c", "csv_sort.h"):
            raise HTTPException(404)
        return FileResponse(ROOT / "examples" / filename, filename=filename, media_type="text/plain")

    @app.post("/api/submissions", status_code=201)
    def submit(payload: Submission, user=Depends(require_user)):
        if len(payload.source.encode()) > 65536:
            raise HTTPException(400, "소스 코드는 UTF-8 기준 64 KiB 이하여야 합니다.")
        with database.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            problem = db.execute("SELECT * FROM problems WHERE id=?", (payload.problem_id,)).fetchone()
            opened = json.loads(db.execute("SELECT value FROM metadata WHERE key='submissions_open'").fetchone()[0])
            if not problem or not problem["is_open"] or not opened:
                raise HTTPException(400, "현재 제출할 수 없는 문제입니다.")
            if not db.execute("SELECT 1 FROM testcases WHERE problem_id=?", (payload.problem_id,)).fetchone():
                raise HTTPException(400, "아직 채점 데이터가 등록되지 않았습니다.")
            pending = db.execute("SELECT COUNT(*) FROM submissions WHERE user_id=? AND status IN ('queued','running')", (user["id"],)).fetchone()[0]
            if pending >= 2:
                raise HTTPException(429, "동시에 대기할 수 있는 제출물은 2개입니다.")
            last = db.execute("SELECT MAX(created_at) FROM submissions WHERE user_id=?", (user["id"],)).fetchone()[0]
            if last and time.time() - last < 20:
                raise HTTPException(429, "제출 간격은 20초입니다. 잠시 후 다시 제출하세요.")
            if db.execute("SELECT COUNT(*) FROM submissions WHERE status IN ('queued','running')").fetchone()[0] >= 200:
                raise HTTPException(429, "채점 대기열이 가득 찼습니다. 잠시 후 다시 제출하세요.")
            cursor = db.execute("INSERT INTO submissions(user_id,problem_id,source,created_at) VALUES (?,?,?,?)", (user["id"], payload.problem_id, payload.source, time.time()))
            submission_id = cursor.lastrowid
        return {"id": submission_id, "status": "queued"}

    @app.get("/api/submissions")
    def submissions(problem_id: int | None = None, user=Depends(require_user)):
        conditions, params = [], []
        if user["role"] != "admin":
            conditions.append("s.user_id=?")
            params.append(user["id"])
        if problem_id is not None:
            conditions.append("s.problem_id=?")
            params.append(problem_id)
        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        with database.connect() as db:
            rows = db.execute("SELECT s.id,s.problem_id,s.status,s.created_at,s.finished_at,s.profile,s.result,u.nickname FROM submissions s JOIN users u ON u.id=s.user_id" + where + " ORDER BY s.id DESC LIMIT 100", params).fetchall()
        return {"submissions": [{**dict(r), "result": json.loads(r["result"]) if r["result"] else None} for r in rows]}

    @app.get("/api/submissions/{submission_id}")
    def submission_detail(submission_id: int, user=Depends(require_user)):
        with database.connect() as db:
            row = db.execute("SELECT * FROM submissions WHERE id=?", (submission_id,)).fetchone()
        if not row or (row["user_id"] != user["id"] and user["role"] != "admin"):
            raise HTTPException(404, "제출물을 찾을 수 없습니다.")
        return {**dict(row), "result": json.loads(row["result"]) if row["result"] else None}

    @app.get("/api/admin/settings")
    def admin_settings(user=Depends(require_admin)):
        with database.connect() as db:
            users = [dict(r) for r in db.execute("SELECT student_id,nickname,created_at FROM users WHERE role='student' ORDER BY created_at DESC LIMIT 500")]
        return {"enrollment_code": settings.enrollment_code, "submissions_open": database.get_meta("submissions_open", True), "users": users, "worker": worker_status(database)}

    @app.post("/api/admin/submissions-open")
    def toggle_open(payload: OpenPayload, user=Depends(require_admin)):
        database.set_meta("submissions_open", payload.is_open)
        return {"ok": True}

    @app.post("/api/admin/problems", status_code=201)
    def create_problem(payload: ProblemPayload, user=Depends(require_admin)):
        with database.connect() as db:
            cursor = db.execute("INSERT INTO problems(title,description,is_open,created_at) VALUES (?,?,?,?)", (payload.title, payload.description, payload.is_open, time.time()))
            problem_id = cursor.lastrowid
        return {"id": problem_id}

    @app.patch("/api/admin/problems/{problem_id}")
    def edit_problem(problem_id: int, payload: ProblemPayload, user=Depends(require_admin)):
        problem_or_404(problem_id)
        with database.connect() as db:
            db.execute("UPDATE problems SET title=?,description=?,is_open=? WHERE id=?", (payload.title, payload.description, payload.is_open, problem_id))
        return {"ok": True}

    @app.post("/api/admin/problems/{problem_id}/testcases", status_code=201)
    def create_case(problem_id: int, payload: TestcasePayload, user=Depends(require_admin)):
        problem_or_404(problem_id)
        try:
            rows, columns = validate_pair(payload.input_csv, payload.answer_csv)
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        with database.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT COUNT(*) FROM testcases WHERE problem_id=?", (problem_id,)).fetchone()[0] >= 10:
                raise HTTPException(400, "문제당 테스트 파일은 10쌍까지 등록할 수 있습니다.")
            current_bytes = db.execute("SELECT COALESCE(SUM(length(CAST(input_csv AS BLOB))+length(CAST(answer_csv AS BLOB))),0) FROM testcases WHERE problem_id=?", (problem_id,)).fetchone()[0]
            if current_bytes + len(payload.input_csv.encode()) + len(payload.answer_csv.encode()) > 128 * 1024**2:
                raise HTTPException(400, "문제당 원본·정답 파일의 합계는 128 MiB 이하여야 합니다.")
            cursor = db.execute("INSERT INTO testcases(problem_id,title,input_csv,answer_csv,is_public,row_count,column_count,digest,created_at) VALUES (?,?,?,?,?,?,?,?,?)", (problem_id, payload.title, payload.input_csv, payload.answer_csv, payload.is_public, rows, columns, case_digest(payload.input_csv, payload.answer_csv), time.time()))
            case_id = cursor.lastrowid
        return {"id": case_id, "rows": rows, "columns": columns}

    @app.delete("/api/admin/problems/{problem_id}/testcases/{case_id}")
    def delete_case(problem_id: int, case_id: int, user=Depends(require_admin)):
        with database.connect() as db:
            cursor = db.execute("DELETE FROM testcases WHERE id=? AND problem_id=?", (case_id, problem_id))
        if not cursor.rowcount:
            raise HTTPException(404, "테스트를 찾을 수 없습니다.")
        return {"ok": True}

    @app.post("/api/admin/submissions/{submission_id}/rejudge")
    def rejudge(submission_id: int, user=Depends(require_admin)):
        with database.connect() as db:
            row = db.execute("SELECT status FROM submissions WHERE id=?", (submission_id,)).fetchone()
            if not row:
                raise HTTPException(404, "제출물을 찾을 수 없습니다.")
            if row["status"] in ("queued", "running"):
                raise HTTPException(400, "이미 채점 대기 중입니다.")
            db.execute("UPDATE submissions SET status='queued',result=NULL,profile=NULL,started_at=NULL,finished_at=NULL WHERE id=?", (submission_id,))
        return {"ok": True}

    return app
