import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


@pytest.fixture
def app(tmp_path):
    return create_app(Settings(data_dir=tmp_path / 'data', admin_password='admin-test-password-123', access_code='class-code'))


@pytest.fixture
def client(app):
    with TestClient(app, headers={'X-Requested-With': 'SortingLab'}) as client:
        assert client.post('/api/access', json={'code':'class-code'}).status_code == 200
        yield client


@pytest.fixture
def admin(client):
    assert client.post('/api/login', json={'student_id': 'admin', 'password': 'admin-test-password-123'}).status_code == 200
    return client


@pytest.fixture
def problem(admin):
    problem_id = admin.post('/api/admin/problems', json={'title': 'CSV sorting', 'description': 'First column, then second column for ties.', 'is_published': True}).json()['id']
    result = admin.post(f'/api/admin/problems/{problem_id}/testcases', json={'title': 'Public input', 'input_csv': 'key,name\nb,둘\na,"하나,첫"\n', 'answer_csv': 'key,name\na,"하나,첫"\nb,둘\n', 'is_public': True})
    assert result.status_code == 201, result.text
    return problem_id


def submit_student(client, problem_id, student_id='20260001', password='1234', source=None):
    return client.post('/api/submissions', json={
        'problem_id': problem_id, 'student_id': student_id, 'password': password,
        'source': source or '#include "csv_sort.h"\nvoid student_sort(rows *table) {(void)table;}',
    })
