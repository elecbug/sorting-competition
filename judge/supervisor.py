"""Trusted supervisor. Production entry point runs ONLY inside the judge container.

The supervisor remains uid 0; both GCC and the submitted program run as nobody.
Scores come from wait4(), never from output supplied by the student process.
"""
import json
import math
import os
from pathlib import Path
import signal
import statistics
import sys
import tempfile
import time

from judge.datasets import encode_table, parse_csv, validate_pair, verify_output


def run_child(argv, input_data, *, wall_limit, cpu_limit, memory_mb, output_limit, process_limit=1, work_dir="/work", uid=65534, launcher="/opt/launch"):
    with tempfile.TemporaryFile(dir=work_dir) as inp, tempfile.TemporaryFile(dir=work_dir) as out, tempfile.TemporaryFile(dir=work_dir) as err:
        inp.write(input_data)
        inp.seek(0)
        started = time.monotonic()
        command = [launcher, str(math.ceil(cpu_limit)), str(memory_mb * 1024**2), str(output_limit), str(process_limit), str(uid if uid is not None else -1), work_dir, *argv]
        actions = [(os.POSIX_SPAWN_DUP2, inp.fileno(), 0), (os.POSIX_SPAWN_DUP2, out.fileno(), 1), (os.POSIX_SPAWN_DUP2, err.fileno(), 2)]
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
        out.seek(0)
        err.seek(0)
        cpu_ms = (usage.ru_utime + usage.ru_stime) * 1000
        return {
            "exit_code": os.waitstatus_to_exitcode(status), "timed_out": timed_out or cpu_ms > cpu_limit * 1000,
            "time_ms": round(cpu_ms, 4), "memory_kb": usage.ru_maxrss,
            "stdout": out.read(output_limit + 1), "stderr": err.read(8192).decode("utf-8", errors="replace"),
        }


def classify(run):
    if run["timed_out"] or run["exit_code"] == -signal.SIGXCPU:
        return "time_limit", "CPU 또는 실제 경과 시간 제한을 초과했습니다."
    if run["exit_code"] == -signal.SIGXFSZ:
        return "output_limit", "허용된 출력 크기를 초과했습니다."
    if run["exit_code"] == 72:
        return "memory_limit", "CSV 메모리 할당에 실패했습니다."
    if run["exit_code"] == 70:
        return "wrong_answer", "행/열 개수 또는 셀 길이가 변경되었습니다."
    if run["exit_code"]:
        return "runtime_error", f"프로그램이 비정상 종료되었습니다. (exit {run['exit_code']})"
    return None


def judge_job(job, *, source_path="/input/student.c", work_dir="/work", harness="/opt/harness.c", include="/opt/include", uid=65534, launcher="/opt/launch"):
    cases = job["cases"]
    total = len(cases)
    binary = str(Path(work_dir) / "sort")
    compile_run = run_child(
        ["/usr/bin/gcc", "-O2", "-std=c11", "-Wall", "-Wextra", "-I", include, source_path, harness, "-o", binary],
        b"", wall_limit=30, cpu_limit=20, memory_mb=384, output_limit=2 * 1024**2,
        process_limit=64, work_dir=work_dir, uid=uid, launcher=launcher,
    )
    if compile_run["exit_code"] or compile_run["timed_out"]:
        return {"status": "compile_error", "passed": 0, "total": total, "cases": [], "message": compile_run["stderr"][:8000] or "컴파일 시간 또는 메모리 제한을 초과했습니다."}
    if uid is not None:
        os.chown(binary, 0, 0)
    os.chmod(binary, 0o555)
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
        for repetition in range(job["repetitions"] + 1):
            run = run_child([binary], payload, wall_limit=job["wall_limit"], cpu_limit=job["time_limit"], memory_mb=job["memory_mb"], output_limit=len(payload) + 1, work_dir=work_dir, uid=uid, launcher=launcher)
            failure = classify(run)
            if failure:
                result.update(status=failure[0], message=failure[1])
                break
            error = verify_output(run["stdout"], answer_header, answer, expected_blob)
            if error:
                result.update(status="wrong_answer", message=error)
                break
            if repetition:
                samples.append(run["time_ms"])
                memories.append(run["memory_kb"])
        if result["status"] == "accepted":
            median = statistics.median(samples)
            result.update(time_ms=round(median, 4), memory_kb=max(memories), samples_ms=samples,
                          mad_ms=round(statistics.median(abs(x - median) for x in samples), 4))
            suite_times.append(median)
            peak_rss = max(peak_rss, max(memories))
        results.append(result)
    passed = sum(r["status"] == "accepted" for r in results)
    status = next((r["status"] for r in results if r["status"] != "accepted"), "accepted")
    return {"status": status, "passed": passed, "total": total, "cases": results,
            "time_ms": round(sum(suite_times), 4) if passed == total else None,
            "memory_kb": peak_rss if passed == total else None}


def main():
    if os.getuid() != 0 or not Path("/.dockerenv").exists():
        raise SystemExit("실제 제출물은 Docker 격리 환경에서만 실행할 수 있습니다.")
    job = json.load(sys.stdin)
    try:
        result = judge_job(job)
    except Exception:
        result = {"status": "system_error", "message": "격리 채점기 내부 오류입니다. 관리자에게 문의하세요."}
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
