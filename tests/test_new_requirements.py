import json
import threading
import time

from app.auth import verify_password
from app.db import Database
from app.scoring import leaderboard
from conftest import submit_student
from judge.benchmarks import finish, source_result
from judge.datasets import encode_table, verify_output
from judge.worker import JobControl
from test_scoring import seed


def test_unpublished_problem_is_private_everywhere(app, client, problem):
    submission=submit_student(client,problem).json()
    student_cookie=client.cookies.get('sorting_session')
    client.post('/api/login',json={'student_id':'admin','password':'admin-test-password-123'})
    case=client.get(f'/api/problems/{problem}').json()['testcases'][0]['id']
    assert client.patch(f'/api/admin/problems/{problem}/publication',json={'is_published':False}).status_code==200
    assert client.get(f'/api/problems/{problem}').status_code==200
    client.post('/api/student/session',json={'student_id':'20260001','password':'1234'})
    assert client.get('/api/problems').json()['problems']==[]
    for url in [f'/api/problems/{problem}',f'/api/problems/{problem}/leaderboard',f'/api/problems/{problem}/testcases/{case}/input.csv',f"/api/submissions/{submission['id']}"]:
        assert client.get(url).status_code==404
    assert client.get('/api/submissions').json()['submissions']==[]
    assert submit_student(client,problem).status_code==400
    assert client.patch(f'/api/admin/problems/{problem}/publication',json={'is_published':True}).status_code==403
    client.post('/api/login',json={'student_id':'admin','password':'admin-test-password-123'})
    client.patch(f'/api/admin/problems/{problem}/publication',json={'is_published':True})
    client.post('/api/logout')
    assert client.get(f'/api/problems/{problem}').status_code==200


def test_purge_requires_admin_and_preserves_students_and_problems(app,client,problem):
    first=submit_student(client,problem).json()
    assert client.post('/api/admin/submissions/clear',json={'confirmation':'CLEAR'}).status_code==403
    client.post('/api/login',json={'student_id':'admin','password':'admin-test-password-123'})
    teacher=client.post('/api/submissions',json={'problem_id':problem,'source':'void student_sort(){}'}).json()
    assert client.post('/api/admin/submissions/clear',json={'confirmation':'wrong'}).status_code==422
    result=client.post('/api/admin/submissions/clear',json={'confirmation':'CLEAR'}).json()
    assert result['deleted']==1
    with app.state.db.connect() as db:
        student=db.execute('SELECT * FROM users WHERE id=?',(first['user']['id'],)).fetchone()
        assert verify_password('1234',student['password'])
        assert db.execute('SELECT COUNT(*) FROM problems').fetchone()[0]==1
        assert db.execute('SELECT COUNT(*) FROM testcases').fetchone()[0]==1
        assert db.execute('SELECT id FROM submissions').fetchone()[0]==teacher['id']
    repeated=submit_student(client,problem)
    assert repeated.status_code==201 and repeated.json()['id']>teacher['id']


def test_job_control_and_cache_cannot_recreate_deleted_submission(app,client,problem):
    submission=submit_student(client,problem).json()
    sid=submission['id'];db=app.state.db
    with db.connect() as conn:conn.execute("UPDATE submissions SET status='running',judge_token='old-job' WHERE id=?",(sid,))
    control=JobControl(threading.Event(),db,sid,'old-job')
    assert not control.is_set()
    client.post('/api/login',json={'student_id':'admin','password':'admin-test-password-123'})
    client.post('/api/admin/submissions/clear',json={'confirmation':'CLEAR'})
    assert control.is_set()
    new=submit_student(client,problem).json()
    result={'status':'accepted','binary_hash':'same','time_ms':1,'memory_kb':1000}
    assert finish(db,sid,'old-job','profile','source',result,time.time()) is False
    with db.connect() as conn:
        assert conn.execute('SELECT COUNT(*) FROM benchmark_cache').fetchone()[0]==0
        assert conn.execute('SELECT status FROM submissions WHERE id=?',(new['id'],)).fetchone()[0]=='queued'


def test_identical_executable_uses_canonical_measurement(app,client,problem):
    a=submit_student(client,problem,'20260001').json()
    b=submit_student(client,problem,'20260002').json()
    db=app.state.db
    with db.connect() as conn:
        conn.execute("UPDATE submissions SET status='running',judge_token='a' WHERE id=?",(a['id'],))
        conn.execute("UPDATE submissions SET status='running',judge_token='b' WHERE id=?",(b['id'],))
    common={'status':'accepted','passed':1,'total':1,'binary_hash':'machine-code','memory_kb':10240,'cases':[]}
    assert finish(db,a['id'],'a','profile','text-a',{**common,'time_ms':10},time.time())
    assert finish(db,b['id'],'b','profile','text-b',{**common,'time_ms':25},time.time())
    cached=source_result(db,'profile','text-b')
    assert cached['time_ms']==10 and cached['measurement_reused']
    assert source_result(db,'different-tests','text-b') is None
    with db.connect() as conn:
        values=[json.loads(r[0])['time_ms'] for r in conn.execute('SELECT result FROM submissions ORDER BY id')]
    assert values==[10,10]


def test_problem_specific_priorities_and_close_results_same_points(app,problem):
    db,profile=seed(app,problem)
    for mode in ('memory','time','balanced'):
        with db.connect() as conn:conn.execute('UPDATE problems SET scoring_mode=? WHERE id=?',(mode,problem))
        entries=leaderboard(db,profile,problem)
        assert entries[0]['score']==100
        assert entries[1]['score']<100 and entries[1]['rank']==2
        if mode=='memory':assert entries[0]['time_score']==0 and entries[0]['memory_score']==40
        if mode=='time':assert entries[0]['time_score']==40 and entries[0]['memory_score']==0


def test_wrong_answer_feedback_does_not_reveal_values_or_positions():
    header=['key','private'];answer=[['a','secret-value-a'],['b','secret-value-b']]
    order=verify_output(encode_table(header,answer[::-1]),header,answer)
    changed=verify_output(encode_table(header,[['a','bad'],['b','bad']]),header,answer)
    assert order=='Row order or equal-key order does not match the requirement.'
    assert changed=='Rows were lost, duplicated, or changed.'
    assert 'secret-value' not in order+changed and 'row 1' not in order+changed
