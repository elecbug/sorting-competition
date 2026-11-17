"""CSV parsing, binary harness protocol, and the external answer oracle."""
from collections import Counter
import csv
import io
import struct

MAX_CSV_BYTES = 32 * 1024 * 1024


def parse_csv(text):
    if len(text.encode("utf-8")) > MAX_CSV_BYTES or "\x00" in text:
        raise ValueError("CSV는 NUL 문자가 없는 UTF-8 파일이며 32 MiB 이하여야 합니다.")
    try:
        parsed = list(csv.reader(io.StringIO(text.lstrip("\ufeff"), newline=""), strict=True))
    except csv.Error as exc:
        raise ValueError(f"CSV 형식 오류: {exc}") from exc
    if not parsed or not 1 <= len(parsed[0]) <= 64:
        raise ValueError("첫 행에 1~64개 열의 헤더가 필요합니다.")
    columns = len(parsed[0])
    if len(parsed) > 100001 or any(len(row) != columns for row in parsed):
        raise ValueError("CSV는 100,000행 이하이며 모든 행의 열 개수가 헤더와 같아야 합니다.")
    if len(parsed) * columns > 2000000:
        raise ValueError("CSV는 헤더를 포함해 총 2,000,000개 셀 이하여야 합니다.")
    if any(len(cell.encode("utf-8")) > 16384 for row in parsed for cell in row):
        raise ValueError("한 셀은 UTF-8 기준 16 KiB 이하여야 합니다.")
    return parsed[0], parsed[1:]


def validate_pair(input_csv, answer_csv):
    header, original = parse_csv(input_csv)
    answer_header, answer = parse_csv(answer_csv)
    if header != answer_header:
        raise ValueError("원본과 정답의 헤더가 같아야 합니다.")
    if Counter(map(tuple, original)) != Counter(map(tuple, answer)):
        raise ValueError("정답은 원본의 행 순서만 바꿔야 합니다. 셀 내용과 각 행의 개수를 확인하세요.")
    return len(original), len(header)


def encode_table(header, rows):
    out = bytearray(struct.pack("<II", len(header), len(rows)))
    for row in [header, *rows]:
        for text in row:
            value = text.encode("utf-8")
            out.extend(struct.pack("<I", len(value)))
            out.extend(value)
    return bytes(out)


def decode_table(blob):
    if len(blob) < 8:
        raise ValueError("출력 헤더가 없습니다.")
    columns, count = struct.unpack_from("<II", blob)
    if not 1 <= columns <= 64 or count > 100000 or (count + 1) * columns > 2000000:
        raise ValueError("행/열 개수가 잘못되었습니다.")
    offset, table = 8, []
    for _ in range(count + 1):
        row = []
        for _ in range(columns):
            if offset + 4 > len(blob):
                raise ValueError("출력이 잘렸습니다.")
            size = struct.unpack_from("<I", blob, offset)[0]
            offset += 4
            if size > 16384 or offset + size > len(blob):
                raise ValueError("셀 길이가 잘못되었습니다.")
            row.append(blob[offset:offset + size])
            offset += size
        table.append(tuple(row))
    if offset != len(blob):
        raise ValueError("불필요한 출력이 있습니다.")
    return table[0], table[1:]


def verify_output(blob, answer_header, answer_rows, expected_blob=None):
    if expected_blob is not None and blob == expected_blob:
        return None
    try:
        header, actual = decode_table(blob)
    except ValueError as exc:
        return str(exc)
    if header != tuple(s.encode("utf-8") for s in answer_header):
        return "CSV 헤더가 변경되었습니다."
    expected = [tuple(s.encode("utf-8") for s in row) for row in answer_rows]
    if len(actual) != len(expected):
        return "데이터 행 개수가 정답과 다릅니다."
    for index, (a, b) in enumerate(zip(actual, expected), 1):
        if a != b:
            return f"데이터 {index}번째 행이 정답과 다릅니다. (헤더 제외)"
    return None
