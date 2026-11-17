import json
import time

import pytest

from app.problem_sets import install_pending
from app.scoring import leaderboard
from conftest import submit_student
from judge.profile import current_profile
from scripts.prepare_course_pair import FAMILY, build_cases, keyed_rows, problem_texts
from test_scoring import seed


@pytest.mark.parametrize('identity,masked', [('abc','***'),('1234','****'),('abcde','ab*de'),('20260001','20****01')])
def test_student_identity_is_masked_in_responses_but_admin_can_read_it(app,client,problem,identity,masked):
    response=submit_student(client,problem,identity)
    assert response.status_code==201
    user=response.json()['user']
    assert user['student_id']==user['nickname']==masked
    assert client.get('/api/me').json()['user']['student_id']==masked
    assert client.get('/api/submissions').json()['submissions'][0]['student_id']==masked
    app.state.db.set_meta('base_profile','privacy-test')
    profile=current_profile(app.state.db,problem)
    with app.state.db.connect() as db:
        db.execute("UPDATE submissions SET status='accepted',profile=?,result=?",(profile,json.dumps({'passed':1,'total':1,'time_ms':1,'memory_kb':1000})))
        db.execute("UPDATE users SET nickname='Private full name' WHERE id=?",(user['id'],))
    client.post('/api/logout')
    response=client.get(f'/api/problems/{problem}/leaderboard')
    entry=response.json()['entries'][0]
    assert entry['student_id']==entry['nickname']==masked
    assert identity not in response.text and 'Private full name' not in response.text
    verified=client.post('/api/student/session',json={'student_id':identity,'password':'1234'})
    assert verified.status_code==200 and verified.json()['user']['student_id']==masked
    client.post('/api/login',json={'student_id':'admin','password':'admin-test-password-123'})
    assert client.get(f'/api/problems/{problem}/leaderboard').json()['entries'][0]['student_id']==identity
    assert client.get('/api/submissions').json()['submissions'][0]['student_id']==identity
    assert identity in client.get(f'/api/problems/{problem}/leaderboard.csv').text


def test_memory_priority_uses_cpu_for_equal_memory_and_keeps_memory_first(app,problem):
    database,profile=seed(app,problem)
    with database.connect() as db:
        db.execute("UPDATE problems SET scoring_mode='memory' WHERE id=?",(problem,))
        for identity,cpu,memory in [('student0',200,1000),('student1',100,1000),('student2',1,2000)]:
            db.execute('UPDATE submissions SET result=? WHERE user_id=(SELECT id FROM users WHERE student_id=?)',(json.dumps({'passed':1,'total':1,'time_ms':cpu,'memory_kb':memory}),identity))
    entries=leaderboard(database,profile,problem)
    assert [(e['student_id'],e['rank']) for e in entries]==[('student1',1),('student0',2),('student2',3)]
    with database.connect() as db:
        db.execute('UPDATE submissions SET result=? WHERE user_id=(SELECT id FROM users WHERE student_id=?)',(json.dumps({'passed':1,'total':1,'time_ms':100,'memory_kb':1000}),'student0'))
    assert [e['rank'] for e in leaderboard(database,profile,problem)]==[1,1,3]


def test_two_column_input_and_answer_rules_without_solution_names():
    original=[['x','B'],['y','A'],['z','B'],['w','A']]
    values=keyed_rows(original)
    assert [row[:-1] for row in values]==original
    assert len({row[-1] for row in values})==len(values)
    differs_from_single_column=False
    for _,_,source,answer in build_cases(values,'district'):
        assert answer==sorted(source,key=lambda row:(row[1].encode(),row[-1].encode()))
        differs_from_single_column |= answer!=sorted(source,key=lambda row:row[1].encode())
    assert differs_from_single_column
    for kind in ('district','memory'):
        texts=problem_texts(kind,50000,35)
        assert set(texts)=={'en','ko','zh','vi','ru'}
        all_text=' '.join(item['title']+' '+item['description'] for item in texts.values()).lower()
        for word in ('stable','stability','안정','稳定','ổn định','стабил','radix','merge sort','heap sort'):
            assert word not in all_text


def test_reset_clears_students_and_all_submissions_once_preserving_administrator(app,client,problem):
    first=submit_student(client,problem).json()
    client.post('/api/login',json={'student_id':'admin','password':'admin-test-password-123'})
    teacher=client.post('/api/submissions',json={'problem_id':problem,'source':'void student_sort(){}'}).json()
    database=app.state.db;directory=app.state.settings.data_dir
    with database.connect() as db:
        db.execute("INSERT INTO benchmark_cache VALUES ('profile','binary','{}')")
        db.execute("INSERT INTO benchmark_sources VALUES ('profile','source','binary')")
    (directory/'input.csv').write_text('key\na\nb\n')
    manifest={'set_id':'pair-test-v1','family':FAMILY,'reset_students_token':'reset-once','replace':[{'id':problem,'title':'CSV sorting'}],'problems':[]}
    for kind in ('district','memory'):
        texts=problem_texts(kind,2,1);base=texts.pop('en')
        manifest['problems'].append({**base,'key':kind,'translations':texts,'scoring_mode':'time' if kind=='district' else 'memory','is_published':True,'cases':[{'title':'Input','is_public':True,'input_file':'input.csv','answer_file':'input.csv'}]})
    path=directory/'pending_problem_set.json';path.write_text(json.dumps(manifest))
    install_pending(database,directory)
    with database.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM users WHERE role='student'").fetchone()[0]==0
        assert db.execute("SELECT COUNT(*) FROM users WHERE role='admin'").fetchone()[0]==1
        for table in ('submissions','benchmark_cache','benchmark_sources','identity_events'):
            assert db.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]==0
        new_problems=db.execute('SELECT id FROM problems ORDER BY id').fetchall()
        assert len(new_problems)==2
    assert client.get('/api/admin/settings').status_code==200
    new=submit_student(client,new_problems[0]['id']).json()
    assert new['id']>teacher['id']
    manifest['problems'][0]['description']='Revised condition; preserve new students.'
    path.write_text(json.dumps(manifest));install_pending(database,directory)
    with database.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM users WHERE role='student'").fetchone()[0]==1
        assert db.execute('SELECT COUNT(*) FROM submissions').fetchone()[0]==1
