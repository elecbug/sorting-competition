import json

from app.config import Settings
from app.db import Database
from judge.datasets import parse_csv, validate_pair
from scripts import import_samples


def test_importer_uses_configured_data_mount_and_preserves_original(tmp_path, monkeypatch):
    data = tmp_path / 'mounted-data'
    samples = data / 'sample_csv'
    samples.mkdir(parents=True)
    original = 'key,note\r\nb,"hello\r\nworld"\r\na,"value,quoted"\r\n'
    source = samples / 'sample.csv'
    source.write_bytes(original.encode())
    monkeypatch.setattr(import_samples.Settings, 'from_env', lambda: Settings(data_dir=data))
    monkeypatch.setattr('sys.argv', ['import_samples.py'])
    import_samples.main()
    assert source.read_bytes() == original.encode()
    with Database(data).connect() as db:
        problem = dict(db.execute('SELECT * FROM problems').fetchone())
        case = dict(db.execute('SELECT * FROM testcases').fetchone())
    assert set(json.loads(problem['translations'])) == {'ko', 'zh', 'vi', 'ru'}
    assert case['input_csv'] == original
    assert validate_pair(case['input_csv'], case['answer_csv']) == (2, 2)
    assert parse_csv(case['answer_csv'])[1][1][1] == 'hello\r\nworld'
    generated = data / 'generated_answers/sample.column-1.answer.csv'
    assert generated.stat().st_mode & 0o777 == 0o600
    import_samples.main()
    with Database(data).connect() as db:
        assert db.execute('SELECT COUNT(*) FROM problems').fetchone()[0] == 1
