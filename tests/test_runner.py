"""Native smoke checks run only repository-authored fixture code, never submissions."""
from pathlib import Path
import shutil
import subprocess
import pytest
from app.config import ROOT, Settings
from judge.supervisor import judge_job
from judge.worker import sandbox_args


@pytest.fixture
def launcher(tmp_path):
    if not shutil.which("gcc"):
        pytest.skip("GCC unavailable")
    path=tmp_path/"launch"
    subprocess.run(["gcc","-O2","-std=c11","-Wall","-Wextra",str(ROOT/"judge/launch.c"),"-o",str(path)],check=True)
    return str(path)


def run_fixture(tmp_path,launcher,source,original="key,name\nb,둘\na,하나\n",answer="key,name\na,하나\nb,둘\n"):
    source_path=tmp_path/"fixture.c"
    source_path.write_text(source)
    case={"id":1,"title":"fixture","input_csv":original,"answer_csv":answer}
    return judge_job({"cases":[case],"repetitions":3,"time_limit":2,"wall_limit":4,"memory_mb":128}, source_path=str(source_path),work_dir=str(tmp_path),harness=str(ROOT/"judge/harness.c"),include=str(ROOT/"examples"),uid=None,launcher=launcher)


def test_native_csv_reference(tmp_path,launcher):
    result=run_fixture(tmp_path,launcher,(ROOT/"examples/reference_sort.c").read_text())
    assert result["status"] == "accepted", result
    assert result["passed"] == 1
    assert result["time_ms"] > 0
    assert 0 < result["memory_kb"] < 16*1024
    assert len(result["cases"][0]["samples_ms"]) == 3


def test_wrong_order_and_compile_error(tmp_path,launcher):
    result=run_fixture(tmp_path,launcher,'#include "csv_sort.h"\nvoid student_sort(rows *table) {(void)table;}')
    assert result["status"] == "wrong_answer"
    result=run_fixture(tmp_path,launcher,"this does not compile")
    assert result["status"] == "compile_error"


def test_container_flags_do_not_mount_answers_or_docker_socket(tmp_path):
    settings=Settings(tmp_path,"admin-password","class-code-long")
    args=sandbox_args(settings,"test",str(tmp_path/"student.c"),"sha256:fixed")
    assert "--network" in args and "none" in args
    assert "--read-only" in args and "--cap-drop=ALL" in args
    assert "--security-opt=no-new-privileges" in args
    assert "--cpuset-cpus=2" in args and "--memory=1024m" in args
    assert args[-1]=="sha256:fixed"
    mounts=[a for a in args if a.startswith("type=bind")]
    assert len(mounts)==1 and "student.c" in mounts[0]
    assert "docker.sock" not in " ".join(args)
