#!/usr/bin/env python3
"""Import originals and explicitly generated sample answers without changing CSV files."""
import argparse
import csv
import io
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.config import ROOT, Settings
from app.db import Database
from judge.datasets import parse_csv, validate_pair
from judge.profile import case_digest


def sample_texts(filename, column, header, rows, columns):
    stem = Path(filename).stem
    return {
        "en": {"title": f"[Sample] {stem} · Column {column}", "description": f"Sort by column {column}: {header}. Compare UTF-8 strings in ascending strcmp order. Break ties by comparing all columns from left to right. Preserve the header and cells.\n\n{rows:,} rows · {columns} columns.\nGenerated sample answer. Replace it with your own answer for an assignment."},
        "ko": {"title": f"[샘플] {stem} · {column}열 정렬", "description": f"{column}번째 열 '{header}'을 UTF-8 strcmp 오름차순으로 정렬하세요. 동률이면 전체 열을 왼쪽부터 순서대로 비교하세요. 헤더와 셀 내용은 보존해야 합니다.\n\n{rows:,}행 · {columns}열.\n자동 생성한 샘플 정답입니다. 본 과제에서는 직접 준비한 정답으로 교체하세요."},
        "zh": {"title": f"[示例] {stem} · 第 {column} 列", "description": f"按第 {column} 列“{header}”排序，使用 UTF-8 strcmp 升序比较。相同时，从左到右比较所有列。保留表头和单元格内容。\n\n{rows:,} 行 · {columns} 列。\n本示例答案由系统生成，正式作业请替换为教师准备的答案。"},
        "vi": {"title": f"[Mẫu] {stem} · Cột {column}", "description": f"Sắp xếp theo cột {column}: {header}, dùng strcmp tăng dần trên chuỗi UTF-8. Nếu bằng nhau, so sánh mọi cột từ trái sang phải. Giữ nguyên tiêu đề và nội dung ô.\n\n{rows:,} hàng · {columns} cột.\nĐáp án mẫu được tạo tự động. Hãy thay bằng đáp án riêng cho bài tập chính thức."},
        "ru": {"title": f"[Пример] {stem} · Столбец {column}", "description": f"Сортируйте по столбцу {column}: {header}, по возрастанию strcmp для строк UTF-8. При равенстве сравнивайте все столбцы слева направо. Сохраните заголовок и ячейки.\n\n{rows:,} строк · {columns} столбцов.\nОтвет примера создан автоматически. Для задания замените его собственным ответом."},
    }


def main():
    parser = argparse.ArgumentParser(description="Create sample problems with generated answers and five-language statements.")
    parser.add_argument("--directory", type=Path, default=None)
    parser.add_argument("--column", type=int, default=1, help="Primary sort column, starting at 1. Ties compare all columns from left to right.")
    args = parser.parse_args()
    settings = Settings.from_env()
    sample_directory = args.directory or settings.data_dir / "sample_csv"
    database = Database(settings.data_dir)
    output_dir = settings.data_dir / "generated_answers"
    output_dir.mkdir(mode=0o700, exist_ok=True)
    for source in sorted(sample_directory.glob("*.csv")):
        with source.open(encoding="utf-8-sig", newline="") as stream:
            original = stream.read()
        header, rows = parse_csv(original)
        column = args.column - 1
        if not 0 <= column < len(header):
            raise SystemExit(f"{source.name}: column index out of range.")
        texts = sample_texts(source.name, args.column, header[column], len(rows), len(header))
        base = texts.pop("en")
        old_title = f"[샘플] {source.stem} · {args.column}열 정렬"
        with database.connect() as db:
            existing = db.execute("SELECT * FROM problems WHERE title IN (?,?)", (base["title"], old_title)).fetchone()
            if existing:
                # Migrate only our untouched, earlier generated sample statements.
                if existing["title"] == old_title and existing["description"].endswith("본 과제의 정답 파일은 운영 관리에서 별도로 등록하세요."):
                    db.execute("UPDATE problems SET title=?,description=?,translations=? WHERE id=?", (base["title"], base["description"], json.dumps(texts, ensure_ascii=False), existing["id"]))
                    print(f"Updated sample statement: {source.name}")
                else:
                    print(f"Keeping existing problem: {source.name}")
                continue
        rows.sort(key=lambda r: (r[column].encode(), tuple(cell.encode() for cell in r)))
        buffer = io.StringIO(newline="")
        writer = csv.writer(buffer)
        writer.writerow(header)
        writer.writerows(rows)
        answer = buffer.getvalue()
        row_count, column_count = validate_pair(original, answer)
        answer_path = output_dir / f"{source.stem}.column-{args.column}.answer.csv"
        answer_path.write_text(answer, encoding="utf-8")
        answer_path.chmod(0o600)
        with database.connect() as db:
            cursor = db.execute("INSERT INTO problems(title,description,translations,created_at) VALUES (?,?,?,?)", (base["title"], base["description"], json.dumps(texts, ensure_ascii=False), time.time()))
            db.execute("INSERT INTO testcases(problem_id,title,input_csv,answer_csv,is_public,row_count,column_count,digest,created_at) VALUES (?,?,?,?,1,?,?,?,?)", (cursor.lastrowid, source.stem, original, answer, row_count, column_count, case_digest(original, answer), time.time()))
        print(f"Imported: {source.name} ({row_count:,} rows × {column_count} columns); generated answer: {answer_path.name}")


if __name__ == "__main__":
    main()
