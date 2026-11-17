from concurrent.futures import ThreadPoolExecutor
import json
import time

from fastapi.testclient import TestClient
from app.auth import hash_password, verify_password
from conftest import submit_student


def test_page_and_no_registration(client):
    response = client.get('/')
    assert response.status_code == 200
    assert 'Sorting Competition' in response.text
    assert 'script-src \'self\'' in response.headers['Content-Security-Policy']
    assert 'register-only' not in response.text
    assert 'enrollment_code' not in response.text
    assert client.post('/api/register', json={}).status_code == 404
    assert client.get('/api/problems').json()['problems'] == []
    assert client.get('/api/health').json()['ready'] is False


def test_admin_auth_and_csrf(app, client):
    credentials = {'student_id': 'admin', 'password': 'admin-test-password-123'}
    assert client.post('/api/login', json={**credentials, 'password': 'wrong'}).status_code == 401
    with TestClient(app) as outsider:
        assert outsider.post('/api/login', json=credentials).status_code == 403
    assert client.post('/api/login', json=credentials, headers={'Origin': 'https://evil.example'}).status_code == 403
    assert client.get('/api/admin/settings').status_code == 401
    assert client.post('/api/login', json=credentials).status_code == 200
    assert 'enrollment_code' not in client.get('/api/admin/settings').json()


def test_first_submission_needs_no_account(app, client, problem):
    client.post('/api/logout')
    response = submit_student(client, problem)
    assert response.status_code == 201, response.text
    assert response.json()['status'] == 'queued'
    assert response.json()['user']['student_id'] == '20****01'
    assert response.json()['user']['role'] == 'student'
    assert 'httponly' in response.headers['set-cookie'].lower()
    assert 'samesite=strict' in response.headers['set-cookie'].lower()
    with app.state.db.connect() as db:
        student = db.execute("SELECT * FROM users WHERE student_id='20260001'").fetchone()
        assert student['password'] != '1234'
        assert verify_password('1234', student['password'])
        assert db.execute('SELECT COUNT(*) FROM submissions WHERE user_id=?', (student['id'],)).fetchone()[0] == 1
    assert client.get('/api/admin/settings').status_code == 403
    assert client.get('/api/submissions').json()['submissions'][0]['id'] == response.json()['id']


def test_reuse_password_and_reject_mismatched_identity(app, client, problem):
    first = submit_student(client, problem).json()
    wrong = submit_student(client, problem, password='9999')
    assert wrong.status_code == 401
    assert 'different personal password' in wrong.json()['detail']
    assert client.post('/api/submissions', json={'problem_id': problem, 'source': 'void student_sort(){}'}).status_code == 400
    with app.state.db.connect() as db:
        db.execute('UPDATE submissions SET created_at=?', (time.time() - 21,))
    second = submit_student(client, problem)
    assert second.status_code == 201
    assert second.json()['user']['id'] == first['user']['id']
    with app.state.db.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM users WHERE role='student'").fetchone()[0] == 1
        assert db.execute('SELECT COUNT(*) FROM submissions').fetchone()[0] == 2


def test_private_sources_answers_and_history_verification(client, problem):
    first = submit_student(client, problem).json()
    detail = client.get(f'/api/problems/{problem}').json()
    assert 'answer_csv' not in json.dumps(detail)
    case = detail['testcases'][0]
    assert client.get(f"/api/problems/{problem}/testcases/{case['id']}/input.csv").status_code == 200
    assert client.get(f"/api/problems/{problem}/testcases/{case['id']}/answer.csv").status_code == 404
    submit_student(client, problem, '20260002')
    assert client.get(f"/api/submissions/{first['id']}").status_code == 404
    client.post('/api/logout')
    assert client.get('/api/submissions').status_code == 401
    assert client.post('/api/student/session', json={'student_id': '20260001', 'password': 'wrong'}).status_code == 401
    assert client.post('/api/student/session', json={'student_id': '20260001', 'password': '1234'}).status_code == 200
    assert client.get('/api/submissions').json()['submissions'][0]['id'] == first['id']
    assert client.post('/api/login', json={'student_id': '20260001', 'password': '1234'}).status_code == 401
    assert client.post('/api/admin/submissions/1/rejudge').status_code == 403


def test_unknown_history_verification_creates_nothing(app, client):
    assert client.post('/api/student/session', json={'student_id': '20260001', 'password': '1234'}).status_code == 401
    with app.state.db.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM users WHERE role='student'").fetchone()[0] == 0


def test_password_values_are_not_echoed_in_validation_errors(client, problem):
    for password in ('abc', 'private-test-value-' * 20):
        response = submit_student(client, problem, password=password)
        assert response.status_code == 422
        assert password not in response.text
        assert all('input' not in item and 'ctx' not in item for item in response.json()['detail'])


def test_no_identity_created_for_invalid_submission(app, client, problem):
    assert submit_student(client, problem, source='가' * 30000).status_code == 400
    assert submit_student(client, problem, source='a' * 300000).status_code == 413
    assert submit_student(client, problem, password='123').status_code == 422
    client.post('/api/admin/submissions-open', json={'is_open': False})
    assert submit_student(client, problem).status_code == 400
    with app.state.db.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM users WHERE role='student'").fetchone()[0] == 0
        assert db.execute('SELECT COUNT(*) FROM submissions').fetchone()[0] == 0


def test_submission_cooldown_and_pending_limit(app, client, problem):
    assert submit_student(client, problem).status_code == 201
    assert submit_student(client, problem).status_code == 429
    with app.state.db.connect() as db:
        db.execute('UPDATE submissions SET created_at=?', (time.time() - 21,))
    assert submit_student(client, problem).status_code == 201
    with app.state.db.connect() as db:
        db.execute('UPDATE submissions SET created_at=?', (time.time() - 21,))
    assert submit_student(client, problem).status_code == 429


def test_private_input_and_invalid_answer(client, problem):
    assert client.post(f'/api/admin/problems/{problem}/testcases', json={'title': 'bad', 'input_csv': 'key\na\n', 'answer_csv': 'key\nb\n'}).status_code == 400
    case = client.post(f'/api/admin/problems/{problem}/testcases', json={'title': 'private', 'input_csv': 'key\na\n', 'answer_csv': 'key\na\n', 'is_public': False}).json()['id']
    submit_student(client, problem)
    assert client.get(f'/api/problems/{problem}/testcases/{case}/input.csv').status_code == 404


def test_existing_student_credentials_are_preserved(app, client, problem):
    with app.state.db.connect() as db:
        uid = db.execute('INSERT INTO users(student_id,nickname,password,created_at) VALUES (?,?,?,?)', ('00123456', 'Legacy name', hash_password('old-student-pass'), time.time())).lastrowid
    response = submit_student(client, problem, '00123456', 'old-student-pass')
    assert response.status_code == 201
    assert response.json()['user']['id'] == uid
    assert response.json()['user']['student_id'] == '00****56'
    assert submit_student(client, problem, 'admin', '1234').status_code == 401


def test_concurrent_first_submission_does_not_overwrite_password(app, client, problem):
    def attempt(password):
        with TestClient(app, headers={'X-Requested-With': 'SortingLab'}) as separate:
            response = submit_student(separate, problem, password=password)
            return password, response.status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, ['1234', '5678']))
    assert sorted(status for _, status in results) == [201, 401]
    winner = next(password for password, status in results if status == 201)
    with app.state.db.connect() as db:
        records = db.execute("SELECT * FROM users WHERE student_id='20260001'").fetchall()
        assert len(records) == 1 and verify_password(winner, records[0]['password'])
        assert db.execute('SELECT COUNT(*) FROM submissions').fetchone()[0] == 1


def test_admin_password_verifies_id_correction_without_losing_submissions(app, client, problem):
    submission = submit_student(client, problem, '20269999').json()
    old_cookie = client.cookies.get('sorting_session')
    uid = submission['user']['id']
    client.post('/api/login', json={'student_id': 'admin', 'password': 'admin-test-password-123'})
    assert client.patch(f'/api/admin/students/{uid}', json={'student_id': '20260001', 'password': 'wrong'}).status_code == 401
    assert client.patch(f'/api/admin/students/{uid}', json={'student_id': '20260001', 'password': '1234'}).status_code == 200
    with app.state.db.connect() as db:
        student = db.execute('SELECT * FROM users WHERE id=?', (uid,)).fetchone()
        assert student['student_id'] == '20260001' and verify_password('1234', student['password'])
        assert db.execute('SELECT user_id FROM submissions WHERE id=?', (submission['id'],)).fetchone()[0] == uid
        event = dict(db.execute('SELECT * FROM identity_events').fetchone())
        assert event['old_student_id'] == '20269999' and event['new_student_id'] == '20260001'
        assert 'password' not in event
    with TestClient(app, cookies={'sorting_session': old_cookie}) as old_session:
        assert old_session.get('/api/me').json()['user'] is None
    assert client.post('/api/student/session', json={'student_id': '20269999', 'password': '1234'}).status_code == 401
    assert client.post('/api/student/session', json={'student_id': '20260001', 'password': '1234'}).status_code == 200
    assert client.get('/api/submissions').json()['submissions'][0]['id'] == submission['id']
    assert client.patch(f'/api/admin/students/{uid}', json={'student_id': '20260003', 'password': '1234'}).status_code == 403


def test_correction_cannot_merge_other_student(app, client, problem):
    first = submit_student(client, problem).json()
    second = submit_student(client, problem, '20260002').json()
    client.post('/api/login', json={'student_id': 'admin', 'password': 'admin-test-password-123'})
    assert client.patch(f"/api/admin/students/{first['user']['id']}", json={'student_id': '20260002', 'password': '1234'}).status_code == 409
    with app.state.db.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM users WHERE role='student'").fetchone()[0] == 2
        assert db.execute('SELECT COUNT(*) FROM identity_events').fetchone()[0] == 0


def test_login_and_student_verification_limits(client):
    for _ in range(15):
        assert client.post('/api/login', json={'student_id': 'somebody', 'password': 'wrong'}).status_code == 401
    assert client.post('/api/login', json={'student_id': 'somebody', 'password': 'wrong'}).status_code == 429
    for _ in range(30):
        assert client.post('/api/student/session', json={'student_id': '20260001', 'password': 'wrong'}).status_code == 401
    assert client.post('/api/student/session', json={'student_id': '20260001', 'password': 'wrong'}).status_code == 429
