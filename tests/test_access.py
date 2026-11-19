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
