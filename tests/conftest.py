from pathlib import Path
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


@pytest.fixture
def app(tmp_path):
    return create_app(Settings(data_dir=tmp_path / "data", admin_password="admin-test-password-123", enrollment_code="test-invite-code-123"))


@pytest.fixture
def client(app):
    with TestClient(app, headers={"X-Requested-With": "SortingLab"}) as client:
        yield client


@pytest.fixture
def admin(client):
    assert client.post("/api/login", json={"student_id": "admin", "password": "admin-test-password-123"}).status_code == 200
    return client


@pytest.fixture
def problem(admin):
    problem_id = admin.post("/api/admin/problems", json={"title": "CSV 정렬", "description": "첫 열 기준, 같은 값은 두 번째 열 기준"}).json()["id"]
    result = admin.post(f"/api/admin/problems/{problem_id}/testcases", json={"title": "공개 데이터", "input_csv": 'key,name\nb,둘\na,"하나,첫"\n', "answer_csv": 'key,name\na,"하나,첫"\nb,둘\n', "is_public": True})
    assert result.status_code == 201, result.text
    return problem_id


def register_student(client, name="20260001", nickname="학생01"):
    response = client.post("/api/register", json={"student_id": name, "nickname": nickname, "password": "student-pass-123", "enrollment_code": "test-invite-code-123"})
    assert response.status_code == 201, response.text
    response = client.post("/api/login", json={"student_id": name, "password": "student-pass-123"})
    assert response.status_code == 200
    return response
