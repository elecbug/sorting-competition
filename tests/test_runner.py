"""Native smoke checks run only repository-authored fixture code, never submissions."""
from pathlib import Path
import shutil
import subprocess
import time
import pytest
from app.config import ROOT, Settings
from judge.supervisor import judge_job, run_child
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


def test_wire_fields_cross_input_and_output_buffer_boundaries(tmp_path, launcher):
    # Vary the offsets of 16 KiB fields, length prefixes and multibyte cells.
    rows = [f'{i},"가,나","""","{("x" * (16384 - i))}",\n' for i in range(12)]
    csv = "id,label,quote,text,empty\n" + "".join(rows)
    source = (ROOT / "examples/student_sort.c").read_text()
    assert run_fixture(tmp_path, launcher, source, csv, csv)["status"] == "accepted"


def test_wrong_order_and_compile_error(tmp_path,launcher):
    result=run_fixture(tmp_path,launcher,'#include "csv_sort.h"\nvoid student_sort(rows *table) {(void)table;}')
    assert result["status"] == "wrong_answer"
    result=run_fixture(tmp_path,launcher,"this does not compile")
    assert result["status"] == "compile_error"


def test_wall_timeout_stops_native_parent_and_sleeping_child(tmp_path, launcher):
    started = time.monotonic()
    result = run_child(['/bin/sleep', '5'], b'', wall_limit=.1, cpu_limit=2,
        memory_mb=128, output_limit=1000, work_dir=str(tmp_path), uid=None, launcher=launcher)
    assert result['timed_out']
    assert time.monotonic() - started < 2


def test_container_flags_do_not_mount_answers_or_docker_socket(tmp_path):
    settings=Settings(tmp_path,"admin-password")
    args=sandbox_args(settings,"test",str(tmp_path/"student.c"),"sha256:fixed")
    assert "--network" in args and "none" in args
    assert "--read-only" in args and "--cap-drop=ALL" in args
    assert "--security-opt=no-new-privileges" in args
    assert "--cap-add=KILL" in args  # trusted supervisor must stop the different-UID candidate
    assert "--cpuset-cpus=2" in args and "--memory=1024m" in args
    assert args[-1]=="sha256:fixed"
    mounts=[a for a in args if a.startswith("type=bind")]
    assert len(mounts)==1 and "student.c" in mounts[0]
    assert "docker.sock" not in " ".join(args)


@pytest.mark.parametrize('body,header',[('qsort(table->rows,table->count,sizeof(row),0);','stdlib.h'),('(void)strcmp("a","b");','string.h'),('(void)__builtin_strlen("abc");','stdlib.h'),('(void)rand();','stdlib.h')])
def test_forbidden_library_use_is_rejected_before_execution(tmp_path,launcher,body,header):
    source=f'#include "csv_sort.h"\n#include <{header}>\nvoid student_sort(rows *table){{{body}}}'
    result=run_fixture(tmp_path,launcher,source)
    assert result['status']=='compile_error'
    assert 'Disallowed' in result['message'] or 'policy' in result['message']


def test_helper_functions_allocations_and_io_are_allowed(tmp_path,launcher):
    source='#include "csv_sort.h"\n#include <stdlib.h>\n#include <stdio.h>\nstatic int own_compare(char*a,char*b){return (unsigned char)*a-(unsigned char)*b;}\nvoid student_sort(rows*t){void*p=malloc(8);free(p);fprintf(stderr,"debug");if(t->count==2&&own_compare(t->rows[0].data[0].text,t->rows[1].data[0].text)>0){row r=t->rows[0];t->rows[0]=t->rows[1];t->rows[1]=r;}}'
    assert run_fixture(tmp_path,launcher,source)['status']=='accepted'


def test_same_executable_fingerprint_ignores_comments_and_debug_names(tmp_path,launcher):
    a=tmp_path/'a';b=tmp_path/'b';a.mkdir();b.mkdir()
    source='#include "csv_sort.h"\nvoid student_sort(rows*t){if(t->count==2){row r=t->rows[0];t->rows[0]=t->rows[1];t->rows[1]=r;}}'
    first=run_fixture(a,launcher,source)
    second=run_fixture(b,launcher,'/* different source formatting */\n'+source)
    assert first['status']==second['status']=='accepted'
    assert first['binary_hash']==second['binary_hash']
