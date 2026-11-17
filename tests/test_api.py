import json
from fastapi.testclient import TestClient
from conftest import register_student


def test_page_and_no_fake_results(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "CSV Sorting" in response.text
    assert "script-src 'self'" in response.headers["Content-Security-Policy"]
    assert client.get("/api/problems").json()["problems"] == []
    assert client.get("/api/health").json()["ready"] is False


def test_auth_and_csrf(app, client):
    assert client.post("/api/login", json={"student_id": "admin", "password": "wrong-pass-long"}).status_code == 401
    with TestClient(app) as outsider:
        assert outsider.post("/api/login", json={"student_id": "admin", "password": "admin-test-password-123"}).status_code == 403
    assert client.post("/api/login", json={"student_id": "admin", "password": "admin-test-password-123"}, headers={"Origin": "https://evil.example"}).status_code == 403
    response = register_student(client)
    assert "httponly" in response.headers["set-cookie"].lower()
    assert "samesite=strict" in response.headers["set-cookie"].lower()
    assert client.get("/api/admin/settings").status_code == 403
    assert client.get("/api/me").json()["user"]["role"] == "student"
    client.post("/api/logout")
    assert client.get("/api/submissions").status_code == 401


def test_golden_answers_and_other_students_source_are_private(client, problem):
    register_student(client)
    detail = client.get(f"/api/problems/{problem}").json()
    assert "answer_csv" not in json.dumps(detail)
    case = detail["testcases"][0]
    assert client.get(f"/api/problems/{problem}/testcases/{case['id']}/input.csv").status_code == 200
    assert client.get(f"/api/problems/{problem}/testcases/{case['id']}/answer.csv").status_code == 404
    source = '#include "csv_sort.h"\nvoid student_sort(rows *table) { (void)table; }'
    submission = client.post("/api/submissions", json={"problem_id": problem, "source": source}).json()
    assert submission["status"] == "queued"
    register_student(client, "20260002", "학생02")
    assert client.get(f"/api/submissions/{submission['id']}").status_code == 404
    assert client.get("/api/submissions").json()["submissions"] == []
    assert client.post("/api/admin/submissions/1/rejudge").status_code == 403


def test_private_input_download_denied_and_invalid_golden_rejected(client, problem):
    bad = client.post(f"/api/admin/problems/{problem}/testcases", json={"title":"bad", "input_csv":"key\na\n", "answer_csv":"key\nb\n"})
    assert bad.status_code == 400
    case = client.post(f"/api/admin/problems/{problem}/testcases", json={"title":"hidden", "input_csv":"key\na\n", "answer_csv":"key\na\n", "is_public":False}).json()["id"]
    register_student(client)
    assert client.get(f"/api/problems/{problem}/testcases/{case}/input.csv").status_code == 404


def test_source_limit_submission_cooldown_and_close(client, problem):
    register_student(client)
    assert client.post("/api/submissions", json={"problem_id":problem, "source":"가"*30000}).status_code == 400
    assert client.post("/api/submissions", json={"problem_id":problem, "source":"a"*300000}).status_code == 413
    assert client.post("/api/submissions", json={"problem_id":problem, "source":"void student_sort() {}"}).status_code == 201
    assert client.post("/api/submissions", json={"problem_id":problem, "source":"void student_sort() {}"}).status_code == 429
    client.post("/api/login", json={"student_id":"admin", "password":"admin-test-password-123"})
    client.post("/api/admin/submissions-open", json={"is_open":False})
    register_student(client,"20260003")
    assert client.post("/api/submissions", json={"problem_id":problem, "source":"void student_sort() {}"}).status_code == 400


def test_login_rate_limit(client):
    for _ in range(15):
        assert client.post("/api/login",json={"student_id":"somebody","password":"wrong-pass-123"}).status_code == 401
    assert client.post("/api/login",json={"student_id":"somebody","password":"wrong-pass-123"}).status_code == 429
