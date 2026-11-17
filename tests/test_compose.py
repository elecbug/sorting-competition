import json
import os
from pathlib import Path
import shutil
import subprocess
import threading
from types import SimpleNamespace

import pytest
from app.config import ROOT, Settings
from judge import worker


def test_compose_config_is_separated_and_persistent(tmp_path):
    if not shutil.which('docker'):
        pytest.skip('Docker CLI is unavailable')
    if subprocess.run(['docker', 'compose', 'version'], capture_output=True).returncode:
        pytest.skip('Docker Compose is unavailable')
    data = tmp_path / 'data'
    data.mkdir()
    env_file = tmp_path / 'compose.env'
    env_file.write_text(f'SORT_ADMIN_PASSWORD=test-only-secret\nSORT_DOCKER_GID=999\nSORT_DATA_DIR={data}\n')
    env = os.environ.copy()
    env.update(SORT_ADMIN_PASSWORD='test-only-secret', SORT_DOCKER_GID='999', SORT_DATA_DIR=str(data))
    result = subprocess.run(['docker', 'compose', '--env-file', str(env_file), 'config', '--format', 'json'], cwd=ROOT, env=env, text=True, capture_output=True, check=True)
    config = json.loads(result.stdout)
    web, judge = config['services']['web'], config['services']['worker']
    assert web['build']['target'] == 'web' and judge['build']['target'] == 'worker'
    assert web['restart'] == judge['restart'] == 'unless-stopped'
    assert all('docker.sock' not in v.get('source', '') for v in web['volumes'])
    assert any(v.get('target') == '/var/run/docker.sock' for v in judge['volumes'])
    assert any(v['target'] == '/data' and v['source'] == str(data) for v in web['volumes'])
    assert 'SORT_ADMIN_PASSWORD' not in judge['environment']
    assert 'SORT_ENROLLMENT_CODE' not in web['environment']
    assert judge['depends_on']['web']['condition'] == 'service_healthy'
    assert judge['depends_on']['judge-image']['condition'] == 'service_completed_successfully'
    job_volume = next(v for v in judge['volumes'] if v['target'] == '/jobs')
    assert config['volumes'][job_volume['source']]['name'] == judge['environment']['SORT_JOB_VOLUME']
    assert config['services']['judge-image']['entrypoint'] == ['/bin/true']
    assert web['read_only'] and judge['read_only']


def test_worker_mounts_only_current_job_volume_subdirectory(tmp_path):
    settings = Settings(tmp_path, job_dir=tmp_path / 'jobs', job_volume='test-private-jobs')
    args = worker.sandbox_args(settings, 'judge-test', '/jobs/sorting-unique-job/student.c', 'sha256:fixed', owner='owned')
    mount = args[args.index('--mount') + 1]
    assert mount == 'type=volume,src=test-private-jobs,dst=/input,volume-subpath=sorting-unique-job,readonly'
    assert 'type=bind' not in mount
    assert 'sorting-competition.owner=owned' in args
    assert args[-1] == 'sha256:fixed'
    assert 'docker.sock' not in ' '.join(args) and 'answer' not in mount


def test_stop_interrupts_sandbox_before_removing_job_source(tmp_path, monkeypatch):
    stop = threading.Event()
    settings = Settings(tmp_path, job_dir=tmp_path / 'jobs', job_volume='test-jobs')
    calls, process = [], None
    class FakeProcess:
        returncode = None
        def __init__(self, args, **kwargs):
            self.args = args
            self.inputs = []
        def communicate(self, input=None, timeout=None):
            self.inputs.append(input)
            if self.returncode is None:
                stop.set()
                raise subprocess.TimeoutExpired('docker', timeout)
            return '', ''
        def poll(self):
            return self.returncode
        def kill(self):
            self.returncode = -9
    def popen(*args, **kwargs):
        nonlocal process
        process = FakeProcess(*args, **kwargs)
        return process
    def docker(args, **kwargs):
        calls.append(args)
        assert list((tmp_path / 'jobs').glob('sorting-*/student.c')), 'remove container before deleting its source'
        return SimpleNamespace(returncode=0, stdout='', stderr='')
    monkeypatch.setattr(worker.subprocess, 'Popen', popen)
    monkeypatch.setattr(worker, 'docker', docker)
    with pytest.raises(worker.JudgeInterrupted):
        worker.execute(settings, 'trusted fixture', [{'id': 1}], 'sha256:fixed', stop=stop)
    assert process.returncode == -9
    assert process.inputs[0] is not None
    assert calls[-1][:2] == ['rm', '-f']
    assert not list((tmp_path / 'jobs').glob('sorting-*'))


def test_recovery_only_stops_this_database_owned_sandboxes(monkeypatch):
    calls = []
    def docker(args, **kwargs):
        calls.append(args)
        return SimpleNamespace(returncode=0, stdout='container-a\ncontainer-b\n' if args[0] == 'ps' else '', stderr='')
    monkeypatch.setattr(worker, 'docker', docker)
    worker.clean_interrupted_sandboxes('unique-database-owner')
    assert calls == [
        ['ps', '-aq', '--filter', 'label=sorting-competition.owner=unique-database-owner'],
        ['rm', '-f', 'container-a', 'container-b'],
    ]
