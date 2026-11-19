"""One correctness queue and one benchmark queue. Only benchmark replicas share measured cores."""
import base64
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import fcntl
import hashlib
import heapq
import json
import logging
import os
from pathlib import Path
import selectors
import signal
import subprocess
import tempfile
import threading
import time
import uuid

from app.config import Settings
from app.db import Database
from judge.cores import plan
from judge.profile import case_metadata, problem_profile
from judge.prepared import directory, prepare_cases
from judge.supervisor import case_summary, suite_result
from judge.benchmarks import binary_result, finish, source_result

log = logging.getLogger("judge")


def docker(args, **kwargs):
    return subprocess.run(["docker", *args], capture_output=True, text=True, **kwargs)


def sandbox_args(settings, name, source, image_id, owner="", *, cpu=None, cases=()):
    source = Path(source)
    if settings.job_volume:
        # Docker resolves bind paths on its HOST, not inside this worker container.
        # Mount only this job's subdirectory from the shared named volume.
        mount = f"type=volume,src={settings.job_volume},dst=/input,volume-subpath={source.parent.name},readonly"
    else:
        mount = f"type=bind,src={source},dst=/input/{source.name},readonly"
    args = [
        "run", "--rm", "--name", name, "--label", "sorting-competition.worker=true", "--network", "none", "--ipc", "none",
        "--read-only", "--cap-drop=ALL", "--cap-add=SETUID", "--cap-add=SETGID", "--cap-add=CHOWN", "--cap-add=KILL",
        "--security-opt=no-new-privileges", "--pids-limit=64", "--memory=1024m", "--memory-swap=1024m", "--cpus=1",
        f"--cpuset-cpus={settings.cpu if cpu is None else cpu}", "--ulimit=core=0", "--log-driver=none",
        "--tmpfs=/work:rw,exec,nosuid,nodev,size=128m,mode=1777",
        "--tmpfs=/tmp:rw,noexec,nosuid,nodev,size=16m,mode=1777",
        "--mount", mount, "-i", image_id,
    ]
    if cases:
        # Per-container PID accounting avoids host-wide nobody process limits across replicas.
        args[args.index("--pids-limit=64")] = "--pids-limit=3"
        args.insert(1, "--init=false")
        if 65534 in (os.getgid(), os.getuid()):
            raise RuntimeError("The worker needs an identity distinct from the sandbox candidate.")
        mounts = ["--group-add", str(os.getgid())]
        for case in cases:
            target = directory(settings, case)
            dest = f"/cases/{int(case['id'])}"
            if settings.job_volume:
                subpath = target.relative_to(settings.job_dir)
                spec = f"type=volume,src={settings.job_volume},volume-subpath={subpath},dst={dest},readonly"
            else:
                spec = f"type=bind,src={target.resolve()},dst={dest},readonly"
            mounts.extend(["--mount", spec])
        args[-1:-1] = mounts
    if owner:
        args[-1:-1] = ["--label", f"sorting-competition.owner={owner}"]
    return args


class JudgeInterrupted(RuntimeError):
    pass


class JobControl:
    def __init__(self, shutdown, database, submission_id, token):
        self.shutdown, self.database, self.submission_id, self.token = shutdown, database, submission_id, token

    def is_set(self):
        if self.shutdown.is_set():
            return True
        with self.database.connect() as db:
            return not db.execute("SELECT 1 FROM submissions WHERE id=? AND judge_token=? AND status='running'", (self.submission_id, self.token)).fetchone()


def execute(settings, source, cases, image_id, stop=None, owner="", *, phase="full", cpu=None,
            program=None, binary_hash=None, repetitions=None, barrier=None):
    name = "sorting-judge-" + uuid.uuid4().hex
    job_dir = settings.job_dir or settings.data_dir / "jobs"
    job_dir.mkdir(mode=0o755, parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="sorting-", dir=job_dir) as directory:
        Path(directory).chmod(0o755)
        if program is None:
            path = Path(directory) / "student.c"
            path.write_text(source)
        else:
            path = Path(directory) / "program"
            path.write_bytes(program)
        path.chmod(0o644)
        job = {"synchronized": barrier is not None, "phase": phase, "binary_hash": binary_hash, "cases": cases, "repetitions": repetitions or settings.repetitions, "time_limit": settings.time_limit,
               "wall_limit": settings.wall_limit, "memory_mb": settings.memory_mb,
               "warmup_runs": settings.warmup_runs, "trim_fraction": settings.trim_fraction,
               "settle_seconds": settings.settle_seconds}
        try:
            timeout = 90 + len(cases) * (settings.repetitions + settings.warmup_runs) * (settings.wall_limit + 1)
            run = subprocess.Popen(["docker", *sandbox_args(settings, name, str(path.resolve()), image_id, owner=owner, cpu=cpu, cases=cases if program is not None else ())], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            deadline = time.monotonic() + timeout
            input_data = json.dumps(job)
            try:
                if barrier is not None:
                    run.stdin.write(input_data + "\n")
                    run.stdin.flush()
                    with selectors.DefaultSelector() as selector:
                        selector.register(run.stdout, selectors.EVENT_READ)
                        while not selector.select(timeout=0.5):
                            if stop is not None and stop.is_set():
                                raise JudgeInterrupted()
                            if time.monotonic() > deadline or barrier.broken:
                                raise RuntimeError("Benchmark startup interrupted.")
                        if run.stdout.readline() != "ready\n":
                            raise RuntimeError("Benchmark sandbox failed to start.")
                    barrier.wait(timeout=60)
                    input_data = "go\n"
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
            except BaseException:
                if barrier is not None:
                    barrier.abort()
                raise
            finally:
                if run.poll() is None:
                    run.kill()
                    run.communicate(timeout=5)
            if run.returncode:
                log.error("Sandbox exited %s: %s", run.returncode, stderr[:1000])
                raise RuntimeError("Sandbox container failed. Check Docker and container memory limits.")
            result = json.loads(stdout)
            if result.get("status") not in {"compiled", "accepted", "compile_error", "wrong_answer", "time_limit", "memory_limit", "runtime_error", "output_limit", "system_error"}:
                raise RuntimeError("Invalid response from the sandbox supervisor.")
            return result
        finally:
            docker(["rm", "-f", name], timeout=15)


def ready_profile(settings, cores, check_cpu):
    version = docker(["info", "--format", "{{.ServerVersion}}"], timeout=10)
    if version.returncode:
        raise RuntimeError("Docker access denied. Complete the worker setup in deploy/README.md.")
    image = docker(["image", "inspect", "--format", "{{.Id}}", settings.image], timeout=10)
    if image.returncode:
        raise RuntimeError("Judge image not found. Run sudo docker build -f judge/Dockerfile -t sorting-judge:local .")
    image_id = image.stdout.strip()
    boot_file = Path('/proc/sys/kernel/random/boot_id')
    identity = {"version": 3, "image": image_id, "cpus": cores, "check_cpu": check_cpu, "repetitions": settings.repetitions,
                "time_limit": settings.time_limit, "wall_limit": settings.wall_limit, "memory_mb": settings.memory_mb,
                "warmup_runs": settings.warmup_runs, "trim_fraction": settings.trim_fraction,
                "settle_seconds": settings.settle_seconds, "boot": boot_file.read_text().strip() if boot_file.exists() else os.uname().nodename}
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


@dataclass
class Submission:
    id: int
    problem_id: int
    source: str
    token: str
    control: JobControl
    priority: int
    base: str
    image: str
    source_hash: str
    cases: list
    profile: str | None = None
    program: bytes | None = None
    binary_hash: str | None = None


def activate_profile(database, base):
    # Preserve previous results and remeasure the latest completed entry for each student/problem.
    with database.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT value FROM metadata WHERE key='base_profile'").fetchone()
        if row and json.loads(row[0]) == base:
            return
        if row:
            latest = db.execute("""
                SELECT MAX(s.id) id FROM submissions s JOIN users u ON u.id=s.user_id
                WHERE u.role='student' AND (s.recalibrating=1 OR s.status IN ('accepted','wrong_answer','compile_error','time_limit',
                    'memory_limit','runtime_error','output_limit')) GROUP BY s.user_id,s.problem_id
            """).fetchall()
            for entry in latest:
                db.execute("""INSERT OR IGNORE INTO prior_measurements
                    SELECT id,profile,status,result,finished_at FROM submissions WHERE id=? AND profile IS NOT NULL""", (entry["id"],))
                db.execute("""UPDATE submissions SET status='queued',recalibrating=1,stage=NULL,
                    result=NULL,profile=NULL,started_at=NULL,finished_at=NULL,judge_token=NULL WHERE id=?""", (entry["id"],))
        db.execute("INSERT OR REPLACE INTO metadata VALUES ('base_profile',?)", (json.dumps(base),))


def claim(database, stop, base, image):
    with database.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT * FROM submissions WHERE status='queued' ORDER BY recalibrating,id LIMIT 1").fetchone()
        if not row:
            return None
        token = uuid.uuid4().hex
        db.execute("UPDATE submissions SET status='running',stage='checking',started_at=?,judge_token=? WHERE id=?",
                   (time.time(), token, row["id"]))
    control = JobControl(stop, database, row["id"], token)
    return Submission(row["id"], row["problem_id"], row["source"], token, control, row["recalibrating"],
                      base, image, hashlib.sha256(row["source"].encode()).hexdigest(), [])


def stage(database, item, name, result=None):
    with database.connect() as db:
        db.execute("UPDATE submissions SET stage=?,result=COALESCE(?,result) WHERE id=? AND judge_token=?",
                   (name, json.dumps(result) if result else None, item.id, item.token))


def compile_submission(settings, item, cpu, owner):
    compiled = execute(settings, item.source, item.cases, item.image, item.control, owner, phase="compile", cpu=cpu)
    if compiled["status"] != "compiled":
        return compiled
    item.program = base64.b64decode(compiled["program"], validate=True)
    item.binary_hash = hashlib.sha256(item.program).hexdigest()
    if item.binary_hash != compiled["binary_hash"]:
        raise RuntimeError("Compiled program digest mismatch.")


def precheck(database, settings, item, cpu, owner):
    item.cases = case_metadata(database, item.problem_id)
    if not item.cases:
        raise RuntimeError("No input/answer CSV pairs are registered for this problem.")
    item.profile = problem_profile(item.base, item.cases)
    cached = source_result(database, item.profile, item.source_hash)
    if cached:
        return cached
    failure = compile_submission(settings, item, cpu, owner)
    if failure:
        return failure
    cached = binary_result(database, item.profile, item.binary_hash)
    if cached:
        return cached
    prepare_cases(database, settings, item.cases)
    checked = execute(settings, None, item.cases, item.image, item.control, owner, phase="check", cpu=cpu,
                      program=item.program, binary_hash=item.binary_hash)
    if checked["status"] == "time_limit":
        # Only a run on the official cores may eliminate a submission on timing.
        stage(database, item, "measurement_pending")
        return None
    if checked["status"] != "accepted":
        return checked
    stage(database, item, "benchmark_pending", {"passed": checked["passed"], "total": checked["total"]})
    return None


def measure(settings, item, cores, owner):
    per_core, extra = divmod(settings.repetitions, len(cores))
    barrier = threading.Barrier(len(cores))

    def replica(slot, cpu):
        try:
            return execute(settings, None, item.cases, item.image, item.control, owner, phase="measure",
                           cpu=cpu, repetitions=per_core + int(slot < extra), program=item.program,
                           binary_hash=item.binary_hash, barrier=barrier)
        except BaseException:
            barrier.abort()
            raise

    with ThreadPoolExecutor(max_workers=len(cores)) as pool:
        futures = [pool.submit(replica, i, cpu) for i, cpu in enumerate(cores)]
        replicas = [f.result() for f in futures]
    for result in replicas:
        if result["status"] != "accepted":
            return result
        if result["binary_hash"] != item.binary_hash or len(result["cases"]) != len(item.cases):
            raise RuntimeError("Unexpected benchmark result.")
    results = []
    for index, case in enumerate(item.cases):
        parts = [r["cases"][index] for r in replicas]
        samples = []
        for slot, part in enumerate(parts):
            if part["id"] != case["id"] or len(part["samples_ms"]) != per_core + int(slot < extra):
                raise RuntimeError("Missing benchmark observations.")
            samples.extend(part["samples_ms"])
        results.append(case_summary(parts[0], samples, [part["memory_kb"] for part in parts], settings.trim_fraction))
    return suite_result(results, item.binary_hash, settings.trim_fraction)


def benchmark(database, settings, item, cores, owner):
    cached = binary_result(database, item.profile, item.binary_hash)
    if cached:
        return cached
    stage(database, item, "benchmarking")
    return measure(settings, item, cores, owner)


def process(database, settings, item, operation, cpus, owner):
    try:
        if item.control.is_set():
            raise JudgeInterrupted()
        result = operation(database, settings, item, cpus, owner)
    except Exception as error:
        if isinstance(error, JudgeInterrupted) or item.control.is_set():
            with database.connect() as db:
                db.execute("""UPDATE submissions SET status='queued',stage=NULL,started_at=NULL,profile=NULL,
                              result=NULL,judge_token=NULL WHERE id=? AND judge_token=?""", (item.id, item.token))
            return False
        log.exception("Submission %s failed during %s", item.id, operation.__name__)
        result = {"status": "system_error", "message": "Internal judge error. Contact the administrator."}
    if result is None:
        return True
    finish(database, item.id, item.token, item.profile, item.source_hash, result, time.time())
    return False


def run_queue(database, settings, cores, housekeeping, stop, state, owner):
    check_cpu = housekeeping[0] if housekeeping else cores[0]
    waiting = []
    preparing = measuring = None
    active_base = image = None
    recovered = False
    next_check = 0
    enabled = False
    with ThreadPoolExecutor(max_workers=2) as pool:
        while not stop.is_set():
            if preparing and preparing[1].done():
                item, future = preparing
                if future.result():
                    heapq.heappush(waiting, (item.priority, item.id, item))
                preparing = None
            if measuring and measuring.done():
                measuring.result()
                measuring = None
            if time.monotonic() >= next_check:
                try:
                    base, new_image = ready_profile(settings, cores, check_cpu)
                    if not recovered:
                        clean_interrupted_sandboxes(owner)
                        recover(database)
                        recovered = True
                    # Drain the previous image before activating a replacement.
                    enabled = base == active_base or not (waiting or preparing or measuring)
                    if enabled and base != active_base:
                        activate_profile(database, base)
                        active_base, image = base, new_image
                    state.update(status="ready", message="Waiting for submissions.")
                except (ValueError, RuntimeError, OSError, subprocess.SubprocessError) as exc:
                    enabled = False
                    state.update(status="blocked", message=str(exc))
                next_check = time.monotonic() + 10
            if waiting and measuring is None and state["status"] != "blocked":
                item = heapq.heappop(waiting)[2]
                measuring = pool.submit(process, database, settings, item, benchmark, cores, owner)
            if enabled and preparing is None and (housekeeping or measuring is None):
                item = claim(database, stop, active_base, image)
                if item:
                    preparing = (item, pool.submit(process, database, settings, item, precheck, check_cpu, owner))
            if enabled:
                busy = preparing or measuring or waiting
                state.update(status="running" if busy else "ready",
                             message="Judging submissions." if busy else "Waiting for submissions.")
            stop.wait(0.15 if preparing or measuring else 0.5)


def recover(database):
    with database.connect() as db:
        db.execute("""UPDATE submissions SET status='queued',stage=NULL,result=NULL,
                      started_at=NULL,judge_token=NULL WHERE status='running'""")



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

    cores, housekeeping = plan(settings)
    if housekeeping:
        os.sched_setaffinity(0, housekeeping)
    log.info("Benchmark CPUs %s; preparation CPU %d", cores, (housekeeping or cores)[0])
    thread = threading.Thread(target=heartbeat, daemon=True)
    thread.start()
    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, lambda *_: stop.set())
    try:
        run_queue(database, settings, cores, housekeeping, stop, state, owner)
    finally:
        stop.set()
        recover(database)
        thread.join(timeout=5)
        database.set_meta("worker", {"status": "offline", "message": "Judge worker stopped.", "updated_at": time.time()})
        lock.close()


if __name__ == "__main__":
    main()
