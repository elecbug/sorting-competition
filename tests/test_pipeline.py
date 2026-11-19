import base64
from dataclasses import replace
from types import SimpleNamespace
import json
import shutil
import subprocess
import sys
import threading
import time

import pytest

from app.config import ROOT, Settings
from conftest import submit_student
from judge import worker
from judge.benchmarks import finish
from judge.cores import plan
from judge.prepared import directory, prepare_cases
from judge.profile import case_metadata
from judge.supervisor import judge_job


def test_cpu_plan_excludes_smt_and_leaves_housekeeping(tmp_path):
    for cpu in range(8):
        path = tmp_path / f"cpu{cpu}/topology"
        path.mkdir(parents=True)
        (path / "thread_siblings_list").write_text(f"{cpu % 4},{cpu % 4 + 4}")
    settings = Settings(tmp_path, cpu=2, repetitions=7)
    cores, other = plan(settings, range(8), tmp_path)
    assert cores == (2, 0, 1) and other == (3, 7)
    for invalid in ("0,4", "0-3", "9", "2-1"):
        with pytest.raises(ValueError):
            plan(replace(settings, benchmark_cpus=invalid), range(8), tmp_path)
    assert plan(settings, [3], tmp_path) == ((3,), ())


def test_prepared_cache_reuses_atomic_private_files(app, problem, tmp_path, monkeypatch):
    settings = Settings(tmp_path)
    db = app.state.db
    case = case_metadata(db, problem)[0]
    prepare_cases(db, settings, [case])
    pair = directory(settings, case)
    assert (pair / "input").read_bytes() != (pair / "answer").read_bytes()
    assert (pair / "answer").stat().st_mode & 0o007 == 0
    assert pair.stat().st_mode & 0o007 == 0
    monkeypatch.setattr(db, "connect", lambda: pytest.fail("cache hit loaded CSVs"))
    prepare_cases(db, settings, [case])
    args = worker.sandbox_args(settings, "check", tmp_path/"program", "image", cpu=3, cases=[case])
    assert "--cpuset-cpus=3" in args and "--pids-limit=3" in args
    assert all(arg.endswith("readonly") for arg in args if arg.startswith("type=bind"))
    assert not any("competition.db" in arg for arg in args)


def test_phases_run_trusted_fixture_once_compiled(tmp_path, app, problem):
    if not shutil.which("gcc"):
        pytest.skip("GCC unavailable")
    settings, db = Settings(tmp_path), app.state.db
    cases = case_metadata(db, problem)
    prepare_cases(db, settings, cases)
    cases_dir = tmp_path / "cases"
    cases_dir.mkdir()
    for case in cases:
        (cases_dir / str(case["id"])).symlink_to(directory(settings, case))
    launch = tmp_path / "launch"
    subprocess.run(["gcc","-O2","-std=c11",str(ROOT/"judge/launch.c"),"-o",str(launch)], check=True)
    job = dict(cases=cases, repetitions=3, time_limit=2, wall_limit=4, memory_mb=128, warmup_runs=1, settle_seconds=0)
    kwargs = dict(source_path=ROOT/"examples/reference_sort.c", work_dir=tmp_path, harness=str(ROOT/"judge/harness.c"),
                  include=str(ROOT/"examples"), uid=None, launcher=str(launch), cases_dir=cases_dir)
    compiled = judge_job({**job, "phase":"compile"}, **kwargs)
    assert compiled["status"] == "compiled"
    check_dir, measure_dir = tmp_path/"check", tmp_path/"measure"
    check_dir.mkdir()
    measure_dir.mkdir()
    program = tmp_path / "program"
    program.write_bytes(base64.b64decode(compiled["program"]))
    kwargs['work_dir'] = check_dir
    checked = judge_job({**job, "phase":"check", "binary_hash":compiled["binary_hash"]}, program_path=program, **kwargs)
    assert checked["status"] == "accepted" and checked["time_ms"] is None
    kwargs['work_dir'] = measure_dir
    measured = judge_job({**job, "phase":"measure", "binary_hash":compiled["binary_hash"]}, program_path=program, **kwargs)
    assert measured["status"] == "accepted" and len(measured["cases"][0]["samples_ms"]) == 3
    with pytest.raises(RuntimeError, match="digest"):
        judge_job({**job, "phase":"measure", "binary_hash":"wrong"}, program_path=program, **kwargs)


def test_lanes_merge_all_observations_before_trimming(monkeypatch, tmp_path):
    settings = Settings(tmp_path, repetitions=10, trim_fraction=.1)
    item = SimpleNamespace(cases=[{"id":1}], image="image", control=None, program=b"fixture", binary_hash="hash")
    observations = {1:[1,1000,4,5], 3:[2,3,6], 5:[7,8,9]}
    def execute(*args, cpu, repetitions, barrier, **kw):
        barrier.wait(timeout=3)
        assert repetitions == len(observations[cpu])
        return {"status":"accepted", "binary_hash":"hash",
                "cases":[{"id":1,"status":"accepted","samples_ms":observations[cpu],"memory_kb":100+cpu}]}
    monkeypatch.setattr(worker, "execute", execute)
    result = worker.measure(settings, item, (1,3,5), "")
    assert result["time_ms"] == 5.5 and result["memory_kb"] == 105
    assert result["cases"][0]["retained_runs"] == 8


def test_worker_start_handshake_with_real_pipes(tmp_path, monkeypatch):
    # Exercise Docker CLI pipe handling with a trusted stand-in that speaks the supervisor protocol.
    settings = Settings(tmp_path, repetitions=3)
    original = subprocess.Popen
    code = """import json,sys
job=json.loads(sys.stdin.readline())
print('ready',flush=True)
assert sys.stdin.readline()=='go\\n'
print(json.dumps({'status':'accepted','count':job['repetitions']}))
"""
    monkeypatch.setattr(worker.subprocess,"Popen",lambda args,**kwargs: original([sys.executable,"-c",code],**kwargs))
    monkeypatch.setattr(worker,"docker",lambda *a, **kw: None)
    assert worker.execute(settings,None,[], "image",program=b"fixture",phase="measure",
                          barrier=threading.Barrier(1))["count"] == 3


def test_profile_transition_and_source_binary_hits(app, client, problem, monkeypatch):
    db, settings = app.state.db, app.state.settings
    source = "trusted fixture"
    sid = submit_student(client, problem, source=source).json()["id"]
    db.set_meta("base_profile", "old")
    with db.connect() as conn:
        conn.execute("UPDATE submissions SET status='accepted',result=?,profile='previous',finished_at=2 WHERE id=?",
                     (json.dumps({"status":"accepted"}),sid))
    worker.activate_profile(db, "new")
    worker.activate_profile(db, "new")
    with db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM prior_measurements").fetchone()[0] == 1
    new = submit_student(client, problem, "20261111", source=source).json()["id"]
    stop = threading.Event()
    fresh = worker.claim(db, stop, "new", "image")
    assert fresh.id == new
    fresh.profile = worker.problem_profile("new", case_metadata(db, problem))
    result = {"status":"accepted","binary_hash":"canonical","time_ms":10,"memory_kb":100,"total":1,"passed":1}
    finish(db, new, fresh.token, fresh.profile, fresh.source_hash, result, time.time())
    previous = worker.claim(db, stop, "new", "image")
    assert previous.id == sid and previous.priority == 1
    monkeypatch.setattr(worker,"execute",lambda *a, **kw: pytest.fail("source or binary hit executed candidate"))
    monkeypatch.setattr(worker,"prepare_cases",lambda *a: pytest.fail("cache hit loaded CSV"))
    assert worker.precheck(db,settings,previous,1,"")["measurement_reused"]
    previous.source_hash = "different"
    def matches(settings, item, cpu, owner):
        item.binary_hash = "canonical"
    monkeypatch.setattr(worker,"compile_submission",matches)
    assert worker.precheck(db,settings,previous,1,"")["measurement_reused"]
    assert worker.benchmark(db,settings,previous,(1,),"")["time_ms"] == 10
    worker.recover(db)
    assert previous.control.is_set()
    with db.connect() as conn:
        assert tuple(conn.execute("SELECT status,stage,judge_token FROM submissions WHERE id=?",(sid,)).fetchone()) == ("queued", None, None)


def test_compile_feedback_during_another_students_benchmark(app, client, problem, monkeypatch):
    db, settings = app.state.db, app.state.settings
    first = submit_student(client, problem, "20261111").json()["id"]
    second = submit_student(client, problem, "20262222").json()["id"]
    measuring, release, stop = threading.Event(), threading.Event(), threading.Event()
    monkeypatch.setattr(worker, "ready_profile", lambda *args: ("base", "image"))
    monkeypatch.setattr(worker, "clean_interrupted_sandboxes", lambda owner: None)
    def check(database, settings, item, cpu, owner):
        item.profile = "test-profile"
        assert cpu == 0
        if item.id == first:
            worker.stage(db, item, "benchmark_pending", {"passed":1, "total":1})
            return None
        assert measuring.wait(3)
        return {"status":"compile_error","passed":0,"total":1}
    def bench(database, settings, item, cores, owner):
        assert item.id == first and cores == (1, 2)
        worker.stage(db, item, "benchmarking")
        measuring.set()
        assert release.wait(5)
        return {"status":"accepted","passed":1,"total":1,"time_ms":1,"memory_kb":100}
    monkeypatch.setattr(worker, "precheck", check)
    monkeypatch.setattr(worker, "benchmark", bench)
    thread = threading.Thread(target=worker.run_queue, args=(db, settings, (1,2), (0,), stop, {}, "test"))
    thread.start()
    try:
        assert measuring.wait(5)
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            with db.connect() as conn:
                statuses = [tuple(r) for r in conn.execute("SELECT status,stage FROM submissions WHERE id IN (?,?) ORDER BY id", (first,second))]
            if statuses == [("running","benchmarking"),("compile_error",None)]:
                break
            time.sleep(.02)
        else:
            pytest.fail(f"Fast feedback was delayed: {statuses}")
    finally:
        release.set()
        stop.set()
        thread.join(timeout=5)
    assert not thread.is_alive()


def test_docker_pipeline_with_reference_image(app, problem, tmp_path):
    if not shutil.which("docker"):
        pytest.skip("Docker CLI unavailable")
    settings = replace(app.state.settings, job_dir=tmp_path/"jobs", repetitions=4, warmup_runs=1)
    if worker.docker(["image","inspect",settings.image], timeout=10).returncode:
        pytest.skip("Built judge image and Docker daemon access required")
    # Place this small integration fixture outside the competition's benchmark pool.
    _, housekeeping = plan(Settings.from_env())
    if len(housekeeping) < 2:
        pytest.skip("Two housekeeping CPUs required for the Docker smoke test")
    cpus, _ = plan(replace(settings, benchmark_cpus="auto"), available=housekeeping, auto_limit=2)
    if len(cpus) < 2:
        pytest.skip("Two distinct physical cores required for the Docker smoke test")
    cases = case_metadata(app.state.db, problem)
    _, image = worker.ready_profile(settings, cpus, housekeeping[0])
    item = SimpleNamespace(source=(ROOT/"examples/reference_sort.c").read_text(), image=image,
                           cases=cases, control=threading.Event())
    assert worker.compile_submission(settings,item,housekeeping[0],"") is None
    prepare_cases(app.state.db,settings,cases)
    checked = worker.execute(settings, None, cases, image, program=item.program, binary_hash=item.binary_hash,
                             phase="check", cpu=housekeeping[0])
    assert checked["status"] == "accepted", checked
    measured = worker.measure(settings, item, cpus, "")
    assert measured["status"] == "accepted", measured
    assert len(measured["cases"][0]["samples_ms"]) == 4
