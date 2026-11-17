"""Host worker. Sole process holding Docker access; the web app needs none."""
import fcntl
import hashlib
import json
import logging
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import threading
import time
import uuid

from app.config import ROOT, Settings
from app.db import Database
from judge.profile import load_cases, problem_profile

log = logging.getLogger("judge")


def docker(args, **kwargs):
    return subprocess.run(["docker", *args], capture_output=True, text=True, **kwargs)


def sandbox_args(settings, name, source, image_id):
    return [
        "run", "--rm", "--name", name, "--label", "sorting-competition.worker=true", "--network", "none", "--ipc", "none",
        "--read-only", "--cap-drop=ALL", "--cap-add=SETUID", "--cap-add=SETGID", "--cap-add=CHOWN",
        "--security-opt=no-new-privileges", "--pids-limit=64", "--memory=1024m", "--memory-swap=1024m",
        f"--cpuset-cpus={settings.cpu}", "--ulimit=core=0", "--log-driver=none",
        "--tmpfs=/work:rw,exec,nosuid,nodev,size=128m,mode=1777",
        "--tmpfs=/tmp:rw,noexec,nosuid,nodev,size=16m,mode=1777",
        "--mount", f"type=bind,src={source},dst=/input/student.c,readonly", "-i", image_id,
    ]


def execute(settings, source, cases, image_id):
    name = "sorting-judge-" + uuid.uuid4().hex
    with tempfile.TemporaryDirectory(prefix="sorting-", dir=settings.data_dir) as directory:
        Path(directory).chmod(0o755)
        path = Path(directory) / "student.c"
        path.write_text(source)
        path.chmod(0o644)
        job = {"cases": cases, "repetitions": settings.repetitions, "time_limit": settings.time_limit,
               "wall_limit": settings.wall_limit, "memory_mb": settings.memory_mb}
        try:
            timeout = 45 + len(cases) * (settings.repetitions + 1) * (settings.wall_limit + 1)
            run = docker(sandbox_args(settings, name, str(path.resolve()), image_id), input=json.dumps(job), timeout=timeout)
            if run.returncode:
                log.error("Sandbox exited %s: %s", run.returncode, run.stderr[:1000])
                raise RuntimeError("격리 컨테이너가 종료되었습니다. Docker 상태와 메모리 제한을 확인하세요.")
            result = json.loads(run.stdout)
            if result.get("status") not in {"accepted", "compile_error", "wrong_answer", "time_limit", "memory_limit", "runtime_error", "output_limit", "system_error"}:
                raise RuntimeError("격리 채점기의 응답이 올바르지 않습니다.")
            return result
        finally:
            docker(["rm", "-f", name], timeout=15)


def ready_profile(settings):
    if settings.cpu not in os.sched_getaffinity(0):
        raise RuntimeError(f"CPU {settings.cpu}는 이 worker에서 사용할 수 없습니다.")
    version = docker(["info", "--format", "{{.ServerVersion}}"], timeout=10)
    if version.returncode:
        raise RuntimeError("Docker 실행 권한이 없습니다. deploy/README.md의 worker 설정을 완료하세요.")
    image = docker(["image", "inspect", "--format", "{{.Id}}", settings.image], timeout=10)
    if image.returncode:
        raise RuntimeError("채점 이미지가 없습니다. sudo docker build -f judge/Dockerfile -t sorting-judge:local . 를 실행하세요.")
    image_id = image.stdout.strip()
    identity = {"version": 1, "image": image_id, "cpu": settings.cpu, "repetitions": settings.repetitions,
                "time_limit": settings.time_limit, "wall_limit": settings.wall_limit, "memory_mb": settings.memory_mb}
    digest = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()[:20]
    return digest, image_id


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    settings = Settings.from_env()
    database = Database(settings.data_dir)
    lock = open(settings.data_dir / "worker.lock", "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise SystemExit("동시 채점을 막기 위해 worker는 한 개만 실행할 수 있습니다.")
    stop = threading.Event()
    state = {"status": "starting", "message": "채점기를 준비하고 있습니다."}

    def heartbeat():
        while not stop.is_set():
            database.set_meta("worker", {**state, "updated_at": time.time()})
            stop.wait(3)

    thread = threading.Thread(target=heartbeat, daemon=True)
    thread.start()
    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, lambda *_: stop.set())
    with database.connect() as db:
        db.execute("UPDATE submissions SET status='queued', started_at=NULL WHERE status='running'")
    last_check, base, image_id = 0, None, None
    try:
        while not stop.is_set():
            if time.monotonic() - last_check > 10:
                try:
                    base, image_id = ready_profile(settings)
                    database.set_meta("base_profile", base)
                    state.update(status="ready", message="순차 채점 대기 중")
                except (RuntimeError, OSError, subprocess.SubprocessError) as exc:
                    base = None
                    state.update(status="blocked", message=str(exc))
                last_check = time.monotonic()
            if not base:
                stop.wait(2)
                continue
            with database.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                row = db.execute("SELECT * FROM submissions WHERE status='queued' ORDER BY id LIMIT 1").fetchone()
                if row:
                    db.execute("UPDATE submissions SET status='running', started_at=? WHERE id=?", (time.time(), row["id"]))
            if not row:
                stop.wait(1)
                continue
            cases = load_cases(database, row["problem_id"])
            profile = problem_profile(base, cases)
            state.update(status="running", message=f"제출 #{row['id']} 채점 중")
            try:
                if not cases:
                    raise RuntimeError("이 문제에 원본/정답 CSV가 등록되어 있지 않습니다.")
                result = execute(settings, row["source"], cases, image_id)
            except (RuntimeError, OSError, ValueError, subprocess.SubprocessError) as exc:
                log.exception("Submission %s failed", row["id"])
                result = {"status": "system_error", "message": str(exc)}
            with database.connect() as db:
                db.execute("UPDATE submissions SET status=?, result=?, profile=?, finished_at=? WHERE id=?", (result["status"], json.dumps(result, ensure_ascii=False), profile, time.time(), row["id"]))
            state.update(status="ready", message="순차 채점 대기 중")
    finally:
        stop.set()
        thread.join(timeout=5)
        database.set_meta("worker", {"status": "offline", "message": "채점기가 종료되었습니다.", "updated_at": time.time()})
        lock.close()


if __name__ == "__main__":
    main()
