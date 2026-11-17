import json
import shutil
import subprocess
from pathlib import Path

import pytest

from app.config import ROOT
from app.db import Database


def test_problem_translations_roundtrip_and_private_answers(admin, problem):
    payload = {
        "title": "Sort by name", "description": "Use the second column.",
        "translations": {
            "ko": {"title": "이름 정렬", "description": "두 번째 열을 기준으로 정렬하세요."},
            "zh": {"title": "按姓名排序", "description": "使用第二列。"},
            "vi": {"title": "Sắp xếp tên", "description": "Dùng cột thứ hai."},
            "ru": {"title": "Сортировка имён", "description": "Используйте второй столбец."},
        },
    }
    assert admin.patch(f"/api/admin/problems/{problem}", json=payload).status_code == 200
    detail = admin.get(f"/api/problems/{problem}").json()
    listing = admin.get("/api/problems").json()["problems"][0]
    assert detail["title"] == "Sort by name"
    assert detail["translations"] == payload["translations"] == listing["translations"]
    assert "answer_csv" not in json.dumps(detail)
    payload["translations"]["xx"] = {"title": "unsupported"}
    assert admin.patch(f"/api/admin/problems/{problem}", json=payload).status_code == 422


def test_existing_database_migrates_without_replacing_problem(tmp_path):
    import sqlite3
    directory = tmp_path / "legacy"
    directory.mkdir()
    with sqlite3.connect(directory / "competition.db") as conn:
        conn.execute("CREATE TABLE problems (id INTEGER PRIMARY KEY,title TEXT NOT NULL,description TEXT NOT NULL,is_open INTEGER NOT NULL DEFAULT 1,created_at REAL NOT NULL)")
        conn.execute("INSERT INTO problems(title,description,created_at) VALUES ('Existing','Preserve this',1)")
    database = Database(directory)
    with database.connect() as conn:
        record = dict(conn.execute("SELECT * FROM problems").fetchone())
    assert record["description"] == "Preserve this"
    assert json.loads(record["translations"]) == {}
    Database(directory)  # migration is idempotent


def test_catalog_completeness_interpolation_and_admin_english():
    if not shutil.which("node"):
        pytest.skip("Node is unavailable")
    script = r'''
      const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
      const select={value:'',disabled:false},storage=new Map();
      const context=vm.createContext({window:{},navigator:{language:'ko-KR'},
        localStorage:{getItem:k=>storage.get(k),setItem:(k,v)=>storage.set(k,v)},
        document:{documentElement:{lang:''},querySelectorAll:()=>[],querySelector:()=>select}});
      vm.runInContext(fs.readFileSync('app/static/i18n.js','utf8'),context);
      const messages=vm.runInContext('SORT_MESSAGES',context),i=context.window.I18n;
      for(const [key,values] of Object.entries(messages)) {
        assert.equal(values.length,5,key);
        const tokens=s=>[...s.matchAll(/\{(\w+)\}/g)].map(m=>m[1]).sort();
        for(const value of values){assert.ok(value.length>0,key);assert.deepEqual(tokens(value),tokens(values[0]),key);}
      }
      const html=fs.readFileSync('app/templates/index.html','utf8');
      for(const match of html.matchAll(/data-i18n="([^"]+)"/g))assert.ok(messages[match[1]],match[1]);
      assert.ok(!html.includes('hero-art')&&!html.includes('page-heading'));
      for(const locale of ['en','ko','zh','vi','ru']) {
        i.use(locale);assert.equal(context.document.documentElement.lang,locale);
        assert.notEqual(i.t('rowMismatch',{row:7}).indexOf('7'),-1);
        assert.equal(i.server('Incorrect student ID or password.'),i.t('badCredentials'));
        assert.equal(i.server('Data row 7 does not match the answer (header excluded).'),i.t('rowMismatch',{row:7}));
      }
      i.use('ru');i.use(i.preferred,true);assert.equal(i.locale,'en');assert.equal(select.disabled,true);
      i.use(i.preferred,false);assert.equal(i.locale,'ru');assert.equal(select.disabled,false);
      console.log('Five locales, interpolation, English fallback, admin language: OK');
    '''
    subprocess.run(["node", "-e", script], cwd=ROOT, check=True, capture_output=True, text=True)


def test_server_errors_are_english(client):
    response = client.post("/api/login", json={"student_id": "admin", "password": "incorrect"})
    assert response.json()["detail"] == "Incorrect student ID or password."
