#!/usr/bin/env python3
"""Prepare two assignments; apply an explicitly requested one-time course reset."""
import argparse
import csv
import hashlib
import io
import json
from pathlib import Path
import random
import sys
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.config import Settings
from app.db import Database
from app.problem_sets import install_pending
from judge.datasets import validate_pair

FAMILY = "sorting-course-pair"
LANGUAGES = ("en", "ko", "zh", "vi", "ru")


def keyed_rows(values):
    result = [list(row) + [hashlib.sha256(json.dumps(row, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()[:24]] for row in values]
    if len({row[-1] for row in result}) != len(result):
        raise ValueError("Every record_key must be unique.")
    return result


def csv_text(header, values):
    stream = io.StringIO(newline="")
    writer = csv.writer(stream)
    writer.writerow(header)
    writer.writerows(values)
    return stream.getvalue()


def build_cases(values, kind):
    public = list(values)
    random.Random(101).shuffle(public)
    public = public[:max(1, len(public) * 4 // 5)]
    private = list(values)
    random.Random(901).shuffle(private)
    small = list(values[:256])
    random.Random(1701).shuffle(small)
    specs = [("Public input", True, public), ("Private 1", False, private), ("Private 2", False, small)]
    if kind == "district":
        groups = {}
        for row in values:
            groups.setdefault(row[1], []).append(row)
        specs.append(("Private 3", False, max(groups.values(), key=len)[:256]))
    cases = []
    for index, (title, public, original) in enumerate(specs, 1):
        if kind == "district":
            original = list(original)
            random.Random(2200 + index).shuffle(original)
            answer = sorted(original, key=lambda row: (row[1].encode(), row[-1].encode()))
        else:
            answer = sorted(original, key=lambda row: row[-1].encode())
        cases.append((title, public, original, answer))
    return cases


def problem_texts(kind, count, columns):
    index = columns - 1
    if kind == "district":
        titles = ["District equipment register", "관할 구역별 장비 목록", "辖区设备名册", "Danh sách thiết bị theo khu vực", "Реестр оборудования по районам"]
        rules = [
            f"Sort complete rows by column 2 (시군구명, C index 1) in ascending order. When those values are equal, use column {columns} (record_key, C index {index}) in ascending order. Each record_key is unique. Input rows are shuffled. Minimize CPU time. Correctness 60, time 40, memory 0.",
            f"2번 칼럼(시군구명, C 인덱스 1) 오름차순으로 정렬하세요. 값이 같으면 {columns}번 칼럼(record_key, C 인덱스 {index}) 오름차순으로 정렬하세요. record_key는 중복되지 않으며 입력 행은 무작위로 섞여 있습니다. CPU 시간을 최소화하세요. 정확성 60점, 시간 40점, 메모리 0점입니다.",
            f"按第2列（시군구명，C下标1）升序排序；相同时，按第{columns}列（record_key，C下标{index}）升序排序。record_key不重复，输入行已随机打乱。尽量减少CPU时间。正确性60分、时间40分、内存0分。",
            f"Sắp xếp tăng dần theo cột 2 (시군구명, chỉ số C 1). Nếu bằng nhau, xếp tăng dần theo cột {columns} (record_key, chỉ số C {index}). record_key không trùng; các hàng đầu vào được xáo trộn ngẫu nhiên. Giảm thời gian CPU. Độ đúng 60, thời gian 40, bộ nhớ 0.",
            f"Сортируйте по столбцу 2 (시군구명, индекс C 1) по возрастанию. При равенстве используйте столбец {columns} (record_key, индекс C {index}) по возрастанию. Значения record_key уникальны, входные строки перемешаны. Минимизируйте время CPU. Корректность 60, время 40, память 0.",
        ]
    else:
        titles = ["Equipment register order", "장비 대장 정리", "设备台账整理", "Sắp xếp sổ thiết bị", "Упорядочение реестра оборудования"]
        rules = [
            f"Sort complete rows by column {columns} (record_key, C index {index}) in ascending order. Each key is unique. Minimize Peak RSS, including the input structure. Equal memory usage is ranked by lower CPU time. Correctness 60, memory 40; CPU time breaks memory ties.",
            f"{columns}번 칼럼(record_key, C 인덱스 {index}) 오름차순으로 정렬하세요. 키는 중복되지 않습니다. 입력 구조를 포함한 Peak RSS를 최소화하세요. 메모리 사용량이 같으면 CPU 시간이 짧은 제출이 앞섭니다. 정확성 60점, 메모리 40점이며 시간은 메모리 동점자의 등수를 나누는 기준입니다.",
            f"按第{columns}列（record_key，C下标{index}）升序排序。键不重复。尽量减少包含输入结构的峰值RSS；内存相同时，CPU时间更短的提交排名在前。正确性60分、内存40分，时间用于内存相同者的排名。",
            f"Sắp xếp tăng dần theo cột {columns} (record_key, chỉ số C {index}). Khóa không trùng. Giảm Peak RSS, gồm cấu trúc đầu vào. Nếu bộ nhớ bằng nhau, bài có CPU thấp hơn đứng trước. Độ đúng 60, bộ nhớ 40; CPU phân hạng khi bộ nhớ bằng nhau.",
            f"Сортируйте по столбцу {columns} (record_key, индекс C {index}) по возрастанию. Ключи уникальны. Минимизируйте Peak RSS, включая входные данные. При равной памяти выше стоит посылка с меньшим временем CPU. Корректность 60, память 40; время разрешает ничьи по памяти.",
        ]
    common = [
        f"Compare strings lexicographically using unsigned UTF-8 bytes. Preserve the header, row count, and every cell. Only input/output and allocation library functions are allowed.\n\nUp to {count:,} rows / {columns} columns.",
        f"문자열은 UTF-8 바이트의 unsigned 값 기준 사전식 순서로 비교하세요. 헤더, 행 개수, 모든 셀을 보존하세요. 라이브러리 함수는 입출력·동적 메모리 할당만 허용합니다.\n\n최대 {count:,}행 / {columns}열.",
        f"按无符号UTF-8字节字典序比较字符串。保留表头、行数及所有单元格。库函数仅允许输入输出和动态内存分配。\n\n最多{count:,}行 / {columns}列。",
        f"So sánh chuỗi theo thứ tự từ điển của byte UTF-8 không dấu. Giữ nguyên tiêu đề, số hàng và mọi ô. Chỉ được dùng hàm thư viện nhập xuất và cấp phát bộ nhớ động.\n\nTối đa {count:,} hàng / {columns} cột.",
        f"Сравнивайте строки лексикографически по беззнаковым байтам UTF-8. Сохраните заголовок, число строк и все ячейки. Разрешены только библиотечные функции ввода-вывода и выделения памяти.\n\nДо {count:,} строк / {columns} столбцов.",
    ]
    return {language: {"title": title, "description": rule + "\n\n" + tail} for language, title, rule, tail in zip(LANGUAGES, titles, rules, common)}


def stage(settings, reset_students=False):
    directory = settings.data_dir / "generated_problems" / "course_pair"
    manifest_path = directory / "manifest.json"
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text())
        if reset_students and not manifest.get("reset_students_token"):
            raise ValueError("The existing prepared set has no reset token. Prepare a new revision explicitly.")
        return manifest_path
    source = settings.data_dir / "sample_csv" / "전국신호등표준데이터.csv"
    with source.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.reader(stream)
        header = next(reader)
        values = keyed_rows(list(reader))
    if header[1] != "시군구명" or "record_key" in header:
        raise ValueError("Unexpected source CSV columns.")
    header = header + ["record_key"]
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    manifest = {"set_id": "sorting-course-pair-v1", "family": FAMILY, "problems": []}
    if reset_students:
        manifest["reset_students_token"] = uuid.uuid4().hex
    database = Database(settings.data_dir)
    with database.connect() as db:
        manifest["replace"] = [dict(row) for row in db.execute("SELECT id,title FROM problems ORDER BY id")]
    for kind in ("district", "memory"):
        texts = problem_texts(kind, len(values), len(header))
        problem = {**texts.pop("en"), "key": kind, "column": 2 if kind == "district" else len(header), "secondary_column": len(header) if kind == "district" else None, "translations": texts, "is_open": False, "is_published": True, "scoring_mode": "time" if kind == "district" else "memory", "cases": []}
        for index, (title, public, original, answer) in enumerate(build_cases(values, kind), 1):
            original_text, answer_text = csv_text(header, original), csv_text(header, answer)
            validate_pair(original_text, answer_text)
            case = {"title": title, "is_public": public}
            for key, text, suffix in (("input_file", original_text, "input"), ("answer_file", answer_text, "answer")):
                path = directory / f"{kind}-{index}.{suffix}.csv"
                path.write_bytes(text.encode())
                path.chmod(0o600)
                case[key] = str(path.relative_to(settings.data_dir))
            problem["cases"].append(case)
        manifest["problems"].append(problem)
        print(f"Prepared {kind}: {len(values):,} rows, {len(header)} columns, {len(problem['cases'])} tests", flush=True)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    manifest_path.chmod(0o600)
    return manifest_path


def apply(settings, manifest_path):
    pending = settings.data_dir / "pending_problem_set.json"
    if pending.exists() and pending.read_bytes() != manifest_path.read_bytes():
        backup = settings.data_dir / "backups" / "problem-set-before-pair.json"
        backup.parent.mkdir(exist_ok=True, mode=0o700)
        if not backup.exists():
            backup.write_bytes(pending.read_bytes())
            backup.chmod(0o600)
    temporary = pending.with_suffix(".tmp")
    temporary.write_bytes(manifest_path.read_bytes())
    temporary.chmod(0o600)
    temporary.replace(pending)
    install_pending(Database(settings.data_dir), settings.data_dir)
    print("Applied the prepared course pair.", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reset-students", action="store_true", help="Include a one-time reset of student identities and all submissions.")
    parser.add_argument("--apply", action="store_true", help="Back up the database and install the prepared pair.")
    arguments = parser.parse_args()
    settings = Settings.from_env()
    prepared = stage(settings, arguments.reset_students)
    print(f"Prepared manifest: {prepared}")
    if arguments.apply:
        apply(settings, prepared)
