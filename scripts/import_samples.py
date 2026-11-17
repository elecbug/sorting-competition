#!/usr/bin/env python3
"""Import sample originals and explicitly GENERATED answers; never change originals."""
import argparse
import csv
import io
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.config import ROOT, Settings
from app.db import Database
from judge.datasets import parse_csv, validate_pair
from judge.profile import case_digest


def main():
    parser = argparse.ArgumentParser(description="샘플 CSV에서 명시한 규칙으로 정답을 생성해 문제로 등록합니다.")
    parser.add_argument("--directory", type=Path, default=ROOT / "data/sample_csv")
    parser.add_argument("--column", type=int, default=1, help="주 정렬 열 (1부터 시작, 기본 1). 동률이면 전체 열을 왼쪽부터 비교합니다.")
    args = parser.parse_args()
    settings = Settings.from_env()
    database = Database(settings.data_dir)
    output_dir = settings.data_dir / "generated_answers"
    output_dir.mkdir(mode=0o700, exist_ok=True)
    for source in sorted(args.directory.glob("*.csv")):
        original = source.read_text(encoding="utf-8-sig")
        header, rows = parse_csv(original)
        column = args.column - 1
        if not 0 <= column < len(header):
            raise SystemExit(f"{source.name}: 열 번호가 범위를 벗어났습니다.")
        title = f"[샘플] {source.stem} · {args.column}열 정렬"
        with database.connect() as db:
            if db.execute("SELECT 1 FROM problems WHERE title=?", (title,)).fetchone():
                print(f"기존 문제 보존: {source.name}")
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
        description = (
            f"{source.name}의 데이터 행을 {args.column}번째 열 '{header[column]}' 기준으로 정렬하세요.\n\n"
            "비교는 UTF-8 바이트의 strcmp 오름차순입니다. 주 정렬 열이 같으면 첫 번째 열부터 마지막 열까지 순서대로 strcmp로 비교합니다. "
            "행 전체가 같으면 순서에 관계없습니다. 헤더 및 셀 내용은 바꾸지 마세요.\n\n"
            f"데이터: {row_count:,}행, {column_count}열. CSV 파싱은 채점기가 제공합니다.\n\n"
            "이 문제는 제공된 샘플 원본에서 위 규칙으로 자동 생성한 정답을 사용한 시연용 문제입니다. 본 과제의 정답 파일은 운영 관리에서 별도로 등록하세요."
        )
        with database.connect() as db:
            cursor = db.execute("INSERT INTO problems(title,description,created_at) VALUES (?,?,?)", (title, description, time.time()))
            db.execute("INSERT INTO testcases(problem_id,title,input_csv,answer_csv,is_public,row_count,column_count,digest,created_at) VALUES (?,?,?,?,1,?,?,?,?)", (cursor.lastrowid, source.stem, original, answer, row_count, column_count, case_digest(original, answer), time.time()))
        print(f"등록: {source.name} ({row_count:,}행 × {column_count}열), 생성 정답: {answer_path.name}")


if __name__ == "__main__":
    main()
