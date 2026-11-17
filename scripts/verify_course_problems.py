#!/usr/bin/env python3
"""Verify staged assignments using only repository-authored C references."""
from pathlib import Path
import json
import os
import subprocess
import sys
import tempfile
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from app.config import ROOT, Settings
from judge.supervisor import judge_job

settings=Settings.from_env()
manifest=json.loads((settings.data_dir/'pending_problem_set.json').read_text())
allowed=sorted(os.sched_getaffinity(0)-{settings.cpu})
if allowed:os.sched_setaffinity(0,{allowed[0]})
report=[]
for problem in manifest['problems']:
    cases=[]
    for index,case in enumerate(problem['cases'],1):
        cases.append({'id':index,'title':case['title'],'input_csv':(settings.data_dir/case['input_file']).read_bytes().decode(),'answer_csv':(settings.data_dir/case['answer_file']).read_bytes().decode()})
    implementations=[('merge','reference_sort.c')]
    if problem['source'].startswith('전국무인'):implementations.append(('radix','reference_radix.c'))
    for algorithm,filename in implementations:
        with tempfile.TemporaryDirectory(prefix='course-reference-') as directory:
            launch=str(Path(directory)/'launch')
            subprocess.run(['gcc','-O2','-std=c11',str(ROOT/'judge/launch.c'),'-o',launch],check=True)
            source=Path(directory)/'reference.c'
            source.write_text(f"#define SORT_COLUMN {problem['column']-1}\n"+(ROOT/'examples'/filename).read_text())
            result=judge_job({'cases':cases,'repetitions':10,'warmup_runs':3,'trim_fraction':.1,'settle_seconds':.2,'time_limit':settings.time_limit,'wall_limit':settings.wall_limit,'memory_mb':settings.memory_mb},source_path=str(source),work_dir=directory,harness=str(ROOT/'judge/harness.c'),include=str(ROOT/'examples'),uid=None,launcher=launch)
            item={'title':problem['title'],'algorithm':algorithm,'status':result['status'],'passed':result.get('passed'),'total':result.get('total'),'time_ms':result.get('time_ms'),'memory_kb':result.get('memory_kb')}
            report.append(item);print(json.dumps(item),flush=True)
            if result['status']!='accepted':print(result);raise SystemExit(1)
path=settings.data_dir/'generated_problems/reference-validation.json'
path.write_text(json.dumps(report,indent=2));path.chmod(0o600)
