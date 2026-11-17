"""Trusted supervisor. Production entry point runs ONLY inside the judge container.

The supervisor remains uid 0; both GCC and the submitted program run as nobody.
Scores come from wait4(), never from output supplied by the student process.
"""
import json
import hashlib
import math
import os
import re
from pathlib import Path
import signal
import statistics
import sys
import tempfile
import time

from judge.datasets import encode_table, parse_csv, validate_pair, verify_output
from judge.policy import prepare_preprocessed, validate_symbols, validate_source


def run_child(argv, input_data, *, wall_limit, cpu_limit, memory_mb, output_limit, process_limit=1, work_dir="/work", uid=65534, launcher="/opt/launch"):
    with tempfile.TemporaryFile(dir=work_dir) as inp, tempfile.TemporaryFile(dir=work_dir) as out, tempfile.TemporaryFile(dir=work_dir) as err, tempfile.TemporaryFile(dir=work_dir) as metrics:
        inp.write(input_data)
        inp.seek(0)
        started = time.monotonic()
        command = [launcher, str(math.ceil(cpu_limit)), str(memory_mb * 1024**2), str(output_limit), str(process_limit), str(uid if uid is not None else -1), work_dir, *argv]
        actions = [(os.POSIX_SPAWN_DUP2, inp.fileno(), 0), (os.POSIX_SPAWN_DUP2, out.fileno(), 1), (os.POSIX_SPAWN_DUP2, err.fileno(), 2), (os.POSIX_SPAWN_DUP2, metrics.fileno(), 3)]
        pid = os.posix_spawn(launcher, command, {"PATH": "/usr/bin:/bin", "LC_ALL": "C", "HOME": work_dir}, file_actions=actions)
        timed_out = False
        try:
            while True:
                waited, status, usage = os.wait4(pid, os.WNOHANG)
                if waited:
                    break
                if time.monotonic() - started > wall_limit:
                    timed_out = True
                    try:
                        os.killpg(pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    _, status, usage = os.wait4(pid, 0)
                    break
                time.sleep(0.002)
        finally:
            try:
                os.killpg(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            # As PID 1 in the sandbox, adopt and reap descendants if their native
            # measurement parent was killed by a wall-time limit.
            while True:
                try:
                    os.waitpid(-pid, 0)
                except ChildProcessError:
                    break
        out.seek(0)
        err.seek(0)
        metrics.seek(0)
        report = metrics.read(128).decode("ascii").split()
        if len(report) == 3:
            micros, memory_kb, exit_code = map(int, report)
            cpu_ms = micros / 1000
        elif timed_out:
            cpu_ms, memory_kb, exit_code = 0, 0, -signal.SIGKILL
        else:
            raise RuntimeError("Native measurement process did not report a result.")
        return {
            "exit_code": exit_code, "timed_out": timed_out or cpu_ms > cpu_limit * 1000,
            "time_ms": round(cpu_ms, 4), "memory_kb": memory_kb,
            "stdout": out.read(output_limit + 1), "stderr": err.read(8192).decode("utf-8", errors="replace"),
        }


def classify(run):
    if run["timed_out"] or run["exit_code"] == -signal.SIGXCPU:
        return "time_limit", "CPU or wall time limit exceeded."
    if run["exit_code"] == -signal.SIGXFSZ:
        return "output_limit", "Output size limit exceeded."
    if run["exit_code"] == 72:
        return "memory_limit", "Failed to allocate CSV memory."
    if run["exit_code"] == 70:
        return "wrong_answer", "Row count, column count, or cell length was changed."
    if run["exit_code"]:
        return "runtime_error", f"Program terminated abnormally (exit {run['exit_code']})."
    return None


def summarize_times(samples, trim_fraction):
    discard = int(len(samples) * trim_fraction)
    retained = sorted(samples)[discard:len(samples)-discard] if discard else list(samples)
    return statistics.mean(retained), retained, 2*discard


def judge_job(job, *, source_path="/input/student.c", work_dir="/work", harness="/opt/harness.c", include="/opt/include", uid=65534, launcher="/opt/launch"):
    cases = job["cases"]
    total = len(cases)
    binary = str(Path(work_dir) / "sort")
    def checked(argv, output_limit=2 * 1024**2):
        run = run_child(argv, b"", wall_limit=30, cpu_limit=20, memory_mb=384,
            output_limit=output_limit, process_limit=64, work_dir=work_dir, uid=uid, launcher=launcher)
        if run["exit_code"] or run["timed_out"]:
            raise ValueError(run["stderr"][:8000] or "Compilation time or memory limit exceeded.")
        return run["stdout"].decode("utf-8", errors="replace")

    try:
        original_source = Path(source_path).read_text()
        validate_source(original_source)
        preprocessed = checked(["/usr/bin/gcc", "-E", "-dI", "-std=c11", "-Werror=date-time", "-I", include, source_path], 8 * 1024**2)
        preprocessed_path = str(Path(work_dir) / "student.i")
        Path(preprocessed_path).write_text(prepare_preprocessed(preprocessed, source_path))
        Path(preprocessed_path).chmod(0o644)
        policy_script = str(Path(__file__).with_name("policy.py"))
        policy = json.loads(checked([sys.executable, policy_script, preprocessed_path, source_path]))
        if not policy.get("ok"):
            return {"status": "compile_error", "reason": "policy_violation", "passed": 0, "total": total, "cases": [], "message": policy.get("message", "Library policy validation failed.")}
        object_path = str(Path(work_dir) / "student.o")
        checked(["/usr/bin/gcc", "-O2", "-std=c11", "-fno-builtin", "-fno-tree-loop-distribute-patterns", "-Wall", "-Wextra", "-Werror=implicit-function-declaration", "-c", preprocessed_path, "-o", object_path])
        validate_symbols(checked(["/usr/bin/nm", "--undefined-only", "--format=posix", object_path]))
        checked(["/usr/bin/gcc", "-O2", "-std=c11", "-I", include, object_path, harness, "-o", binary])
        checked(["/usr/bin/strip", "--strip-all", "--remove-section=.comment", "--remove-section=.note.gnu.build-id", binary])
    except ValueError as error:
        return {"status": "compile_error", "passed": 0, "total": total, "cases": [], "message": str(error)}
    if uid is not None:
        os.chown(binary, 0, 0)
    os.chmod(binary, 0o555)
    binary_hash = hashlib.sha256(Path(binary).read_bytes()).hexdigest()
    time.sleep(job.get("settle_seconds", 0.2))
    results, suite_times, peak_rss = [], [], 0
    for case in cases:
        validate_pair(case["input_csv"], case["answer_csv"])
        header, original = parse_csv(case["input_csv"])
        answer_header, answer = parse_csv(case["answer_csv"])
        payload = encode_table(header, original)
        expected_blob = encode_table(answer_header, answer)
        del original
        samples, memories = [], []
        result = {"id": case["id"], "name": case["title"], "status": "accepted", "rows": len(answer), "columns": len(header)}
        # One checked warm-up and then independent, fresh processes for every sample.
        warmups = job.get("warmup_runs", 3)
        for repetition in range(job["repetitions"] + warmups):
            run = run_child([binary], payload, wall_limit=job["wall_limit"], cpu_limit=job["time_limit"], memory_mb=job["memory_mb"], output_limit=len(payload) + 1, work_dir=work_dir, uid=uid, launcher=launcher)
            failure = classify(run)
            if failure:
                result.update(status=failure[0], message=failure[1])
                break
            error = verify_output(run["stdout"], answer_header, answer, expected_blob)
            if error:
                result.update(status="wrong_answer", message=error)
                break
            if repetition >= warmups:
                samples.append(run["time_ms"])
                memories.append(run["memory_kb"])
        if result["status"] == "accepted":
            median = statistics.median(samples)
            average, retained, discarded = summarize_times(samples, job.get("trim_fraction",0.1))
            result.update(time_ms=round(average, 4), median_ms=round(median,4), discarded_runs=discarded,
                          retained_runs=len(retained), memory_kb=max(memories), samples_ms=samples,
                          mad_ms=round(statistics.median(abs(x - median) for x in samples), 4))
            suite_times.append(average)
            peak_rss = max(peak_rss, max(memories))
        results.append(result)
    passed = sum(r["status"] == "accepted" for r in results)
    status = next((r["status"] for r in results if r["status"] != "accepted"), "accepted")
    return {"status": status, "passed": passed, "total": total, "cases": results,
            "binary_hash": binary_hash, "time_statistic": "trimmed_mean",
            "time_ms": round(sum(suite_times), 4) if passed == total else None,
            "memory_kb": peak_rss if passed == total else None}


def main():
    if os.getuid() != 0 or not Path("/.dockerenv").exists():
        raise SystemExit("Student submissions may only run inside the Docker sandbox.")
    job = json.load(sys.stdin)
    try:
        result = judge_job(job)
    except Exception:
        result = {"status": "system_error", "message": "Internal judge error. Contact the administrator."}
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
