from dataclasses import replace
import time

from fastapi.testclient import TestClient
from app.main import create_app


def test_shared_gate_protects_every_role_and_download(app, client, problem):
    with TestClient(app, headers={"X-Requested-With": "SortingLab"}) as visitor:
        assert 'id="access-form"' in visitor.get("/").text
        assert 'id="source-code"' not in visitor.get("/").text
        assert visitor.get("/healthz").json() == {"web": "ok"}
        visitor.cookies.set("sorting_session", client.cookies.get("sorting_session"))
        for path in ("/api/problems", "/api/health", "/api/admin/settings", "/static/app.js",
                     "/api/submissions", "/api/download/student_sort.c", f"/api/problems/{problem}/leaderboard.csv"):
            assert visitor.get(path).json().get("access_required") is True
        assert visitor.post("/api/submissions", json={}).status_code == 401
        assert visitor.post("/api/access", json={"code": "wrong"}).status_code == 401
        response = visitor.post("/api/access", json={"code": "class-code"})
        assert response.status_code == 200 and "httponly" in response.headers["set-cookie"].lower()
        assert 'id="source-code"' in visitor.get("/").text
        assert visitor.get("/api/admin/settings").status_code == 200


def test_gate_rotation_expiry_and_forgery(app, client, monkeypatch):
    original = client.cookies.get("sorting_access")
    rotated = create_app(replace(app.state.settings, access_code="new-code"))
    with TestClient(rotated, cookies={"sorting_access": original}) as browser:
        assert browser.get("/api/problems").status_code == 401
    altered = original[:-1] + ("a" if original[-1] != "a" else "b")
    with TestClient(app, cookies={"sorting_access": altered}) as browser:
        assert browser.get("/api/problems").status_code == 401
    expiry = int(original.partition(".")[0])
    monkeypatch.setattr(time, "time", lambda: expiry + 1)
    assert client.get("/api/problems").status_code == 401


def test_pending_status_returns_only_state_to_owner_or_admin(app, client, problem):
    from conftest import submit_student

    sid = submit_student(client, problem).json()["id"]
    path = f"/api/submissions/{sid}/status"
    assert client.get(path).json() == {"status": "queued", "stage": None}
    assert submit_student(client, problem, "20269999").status_code == 201
    assert client.get(path).status_code == 404
    with app.state.db.connect() as db:
        db.execute("UPDATE problems SET is_published=0 WHERE id=?", (problem,))
    assert client.post("/api/student/session", json={"student_id":"20260001","password":"1234"}).status_code == 200
    assert client.get(path).status_code == 404
    assert client.post("/api/login", json={"student_id":"admin","password":"admin-test-password-123"}).status_code == 200
    assert client.get(path).json() == {"status": "queued", "stage": None}
    with TestClient(app) as anonymous:
        assert anonymous.get(path).status_code == 401
