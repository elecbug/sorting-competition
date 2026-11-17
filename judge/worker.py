"""Sequential worker with Docker access; the web container has no Docker socket."""
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

from app.config import Settings
from app.db import Database
from judge.profile import load_cases, problem_profile

log = logging.getLogger("judge")


def docker(args, **kwargs):
    return subprocess.run(["docker", *args], capture_output=True, text=True, **kwargs)


def sandbox_args(settings, name, source, image_id, owner=""):
    source = Path(source)
    if settings.job_volume:
        # Docker resolves bind paths on its HOST, not inside this worker container.
        # Mount only this job's subdirectory from the shared named volume.
        mount = f"type=volume,src={settings.job_volume},dst=/input,volume-subpath={source.parent.name},readonly"
    else:
        mount = f"type=bind,src={source},dst=/input/student.c,readonly"
    args = [
        "run", "--rm", "--name", name, "--label", "sorting-competition.worker=true", "--network", "none", "--ipc", "none",
        "--read-only", "--cap-drop=ALL", "--cap-add=SETUID", "--cap-add=SETGID", "--cap-add=CHOWN", "--cap-add=KILL",
        "--security-opt=no-new-privileges", "--pids-limit=64", "--memory=1024m", "--memory-swap=1024m",
        f"--cpuset-cpus={settings.cpu}", "--ulimit=core=0", "--log-driver=none",
        "--tmpfs=/work:rw,exec,nosuid,nodev,size=128m,mode=1777",
        "--tmpfs=/tmp:rw,noexec,nosuid,nodev,size=16m,mode=1777",
        "--mount", mount, "-i", image_id,
    ]
    if owner:
        args[-1:-1] = ["--label", f"sorting-competition.owner={owner}"]
    return args


class JudgeInterrupted(RuntimeError):
    pass


def execute(settings, source, cases, image_id, stop=None, owner=""):
    name = "sorting-judge-" + uuid.uuid4().hex
    job_dir = settings.job_dir or settings.data_dir / "jobs"
    job_dir.mkdir(mode=0o755, parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="sorting-", dir=job_dir) as directory:
        Path(directory).chmod(0o755)
        path = Path(directory) / "student.c"
        path.write_text(source)
        path.chmod(0o644)
        job = {"cases": cases, "repetitions": settings.repetitions, "time_limit": settings.time_limit,
               "wall_limit": settings.wall_limit, "memory_mb": settings.memory_mb}
        try:
            timeout = 45 + len(cases) * (settings.repetitions + 1) * (settings.wall_limit + 1)
            run = subprocess.Popen(["docker", *sandbox_args(settings, name, str(path.resolve()), image_id, owner=owner)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            deadline = time.monotonic() + timeout
            input_data = json.dumps(job)
            try:
                while True:
                    if stop is not None and stop.is_set():
                        raise JudgeInterrupted("Worker stopped; submission returned to the queue.")
                    if time.monotonic() > deadline:
                        raise RuntimeError("Sandbox job deadline exceeded.")
                    try:
                        stdout, stderr = run.communicate(input=input_data, timeout=1)
                        break
                    except subprocess.TimeoutExpired:
                        input_data = None
            finally:
                if run.poll() is None:
                    run.kill()
                    run.communicate(timeout=5)
            if run.returncode:
                log.error("Sandbox exited %s: %s", run.returncode, stderr[:1000])
                raise RuntimeError("Sandbox container failed. Check Docker and container memory limits.")
            result = json.loads(stdout)
            if result.get("status") not in {"accepted", "compile_error", "wrong_answer", "time_limit", "memory_limit", "runtime_error", "output_limit", "system_error"}:
                raise RuntimeError("Invalid response from the sandbox supervisor.")
            return result
        finally:
            docker(["rm", "-f", name], timeout=15)


def ready_profile(settings):
    if settings.cpu not in os.sched_getaffinity(0):
        raise RuntimeError(f"CPU {settings.cpu} is unavailable to this worker.")
    version = docker(["info", "--format", "{{.ServerVersion}}"], timeout=10)
    if version.returncode:
        raise RuntimeError("Docker access denied. Complete the worker setup in deploy/README.md.")
    image = docker(["image", "inspect", "--format", "{{.Id}}", settings.image], timeout=10)
    if image.returncode:
        raise RuntimeError("Judge image not found. Run sudo docker build -f judge/Dockerfile -t sorting-judge:local .")
    image_id = image.stdout.strip()
    identity = {"version": 1, "image": image_id, "cpu": settings.cpu, "repetitions": settings.repetitions,
                "time_limit": settings.time_limit, "wall_limit": settings.wall_limit, "memory_mb": settings.memory_mb}
    digest = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()[:20]
    return digest, image_id


def clean_interrupted_sandboxes(owner):
    result = docker(["ps", "-aq", "--filter", f"label=sorting-competition.owner={owner}"], timeout=10)
    if result.returncode:
        raise RuntimeError("Cannot inspect previous judge containers; queue recovery is waiting.")
    containers = result.stdout.split()
    if containers:
        result = docker(["rm", "-f", *containers], timeout=15)
        if result.returncode:
            raise RuntimeError("Cannot stop previous judge containers; queue recovery is waiting.")


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    settings = Settings.from_env()
    database = Database(settings.data_dir)
    lock = open(settings.data_dir / "worker.lock", "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise SystemExit("Only one worker can run against this database.")
    owner = database.get_meta("worker_owner")
    if not owner:
        owner = uuid.uuid4().hex
        database.set_meta("worker_owner", owner)
    stop = threading.Event()
    state = {"status": "starting", "message": "Preparing the judge worker."}

    def heartbeat():
        while not stop.is_set():
            database.set_meta("worker", {**state, "updated_at": time.time()})
            stop.wait(3)

    thread = threading.Thread(target=heartbeat, daemon=True)
    thread.start()
    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, lambda *_: stop.set())
    recovered = False
    last_check, base, image_id = 0, None, None
    try:
        while not stop.is_set():
            if time.monotonic() - last_check > 10:
                try:
                    base, image_id = ready_profile(settings)
                    if not recovered:
                        clean_interrupted_sandboxes(owner)
                        with database.connect() as db:
                            db.execute("UPDATE submissions SET status='queued', started_at=NULL WHERE status='running'")
                        recovered = True
                    database.set_meta("base_profile", base)
                    state.update(status="ready", message="Waiting for submissions.")
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
            state.update(status="running", message=f"Judging submission #{row['id']}")
            try:
                if not cases:
                    raise RuntimeError("No input/answer CSV pairs are registered for this problem.")
                result = execute(settings, row["source"], cases, image_id, stop=stop, owner=owner)
            except JudgeInterrupted:
                with database.connect() as db:
                    db.execute("UPDATE submissions SET status='queued',started_at=NULL,profile=NULL,result=NULL WHERE id=?", (row["id"],))
                break
            except (RuntimeError, OSError, ValueError, subprocess.SubprocessError) as exc:
                log.exception("Submission %s failed", row["id"])
                result = {"status": "system_error", "message": str(exc)}
            with database.connect() as db:
                db.execute("UPDATE submissions SET status=?, result=?, profile=?, finished_at=? WHERE id=?", (result["status"], json.dumps(result, ensure_ascii=False), profile, time.time(), row["id"]))
            state.update(status="ready", message="Waiting for submissions.")
    finally:
        stop.set()
        thread.join(timeout=5)
        database.set_meta("worker", {"status": "offline", "message": "Judge worker stopped.", "updated_at": time.time()})
        lock.close()


if __name__ == "__main__":
    main()
