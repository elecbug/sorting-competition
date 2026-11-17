import json
import sqlite3

from app.problem_sets import install_pending
from conftest import submit_student
from judge.supervisor import summarize_times
from scripts.prepare_course_problems import statements,unique_rows


def test_pending_set_replaces_only_samples_with_backup_and_is_idempotent(app,client,problem):
    submitted=submit_student(client,problem).json()
    database=app.state.db;directory=app.state.settings.data_dir
    with database.connect() as db:db.execute("UPDATE problems SET title='[Sample] old' WHERE id=?",(problem,))
    original=directory/'input.csv';answer=directory/'answer.csv'
    original.write_text('name\nb\na\n');answer.write_text('name\na\nb\n')
    problems=[]
    for index,kind in enumerate(('stable','fast','radix')):
        texts=statements(kind,1,'name',2,1);base=texts.pop('en')
        problems.append({**base,'translations':texts,'scoring_mode':'memory' if index==0 else 'time','is_published':False,'cases':[{'title':'Input','input_file':'input.csv','answer_file':'answer.csv','is_public':True}]})
    manifest={'set_id':'test','replace':[{'id':problem,'title':'[Sample] old'}],'problems':problems}
    (directory/'pending_problem_set.json').write_text(json.dumps(manifest))
    install_pending(database,directory);install_pending(database,directory)
    with database.connect() as db:
        rows=db.execute('SELECT * FROM problems ORDER BY id').fetchall()
        assert len(rows)==3 and all(not r['is_published'] for r in rows)
        assert [r['scoring_mode'] for r in rows]==['memory','time','time']
        assert db.execute('SELECT COUNT(*) FROM submissions').fetchone()[0]==0
        assert db.execute("SELECT COUNT(*) FROM users WHERE role='student'").fetchone()[0]==1
    backup=next((directory/'backups').glob('*.db'))
    assert backup.stat().st_mode & 0o777==0o600
    with sqlite3.connect(backup) as db:
        assert db.execute('SELECT COUNT(*) FROM submissions').fetchone()[0]==1
    client.post('/api/login',json={'student_id':'admin','password':'admin-test-password-123'})
    client.patch(f"/api/admin/problems/{rows[0]['id']}/publication",json={'is_published':True})
    response=submit_student(client,rows[0]['id'])
    assert response.status_code==201 and response.json()['id']>submitted['id']


def test_problem_copy_states_conditions_without_algorithm_hints():
    for kind in ('stable','fast','radix'):
        texts=statements(kind,2,'key',1234,3)
        assert set(texts)=={'en','ko','zh','vi','ru'}
        assert 'column 2' in texts['en']['description']
        assert 'Up to 1,234 rows / 3 columns.' in texts['en']['description']
        all_text=' '.join(x['title']+' '+x['description'] for x in texts.values()).lower()
        assert 'radix' not in all_text and 'fixed-width' not in all_text and 'five ascii' not in all_text
    assert 'relative order' in statements('stable',2,'key',2,3)['en']['description']


def test_unique_filter_preserves_original_payload():
    rows=[['A1234','first'],['bad','x'],['A1234','second'],['B9876','third']]
    assert unique_rows(rows,0,lambda v:len(v)==5)==[rows[0],rows[3]]


def test_outlier_trimmed_mean_keeps_only_central_observations():
    average,retained,discarded=summarize_times([1000,1,1,1,1,1,1,1,1,0],.1)
    assert average==1 and retained==[1]*8 and discarded==2
    assert summarize_times([10,20,30],0)[0]==20
