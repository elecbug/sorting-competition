"""CSV parsing, binary harness protocol, and the external answer oracle."""
from collections import Counter
import csv
import io
import struct

MAX_CSV_BYTES = 32 * 1024 * 1024


def parse_csv(text):
    if len(text.encode("utf-8")) > MAX_CSV_BYTES or "\x00" in text:
        raise ValueError("CSV must be UTF-8 without NUL characters and at most 32 MiB.")
    try:
        parsed = list(csv.reader(io.StringIO(text.lstrip("\ufeff"), newline=""), strict=True))
    except csv.Error as exc:
        raise ValueError(f"Invalid CSV format: {exc}") from exc
    if not parsed or not 1 <= len(parsed[0]) <= 64:
        raise ValueError("The first row must be a header with 1 to 64 columns.")
    columns = len(parsed[0])
    if len(parsed) > 100001 or any(len(row) != columns for row in parsed):
        raise ValueError("CSV supports at most 100,000 rows; every row must match the header width.")
    if len(parsed) * columns > 2000000:
        raise ValueError("CSV supports at most 2,000,000 cells including the header.")
    if any(len(cell.encode("utf-8")) > 16384 for row in parsed for cell in row):
        raise ValueError("Each cell must not exceed 16 KiB in UTF-8.")
    return parsed[0], parsed[1:]


def validate_pair(input_csv, answer_csv):
    header, original = parse_csv(input_csv)
    answer_header, answer = parse_csv(answer_csv)
    if header != answer_header:
        raise ValueError("Input and answer headers must be identical.")
    if Counter(map(tuple, original)) != Counter(map(tuple, answer)):
        raise ValueError("The answer must contain exactly the original rows, including duplicate counts.")
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
        raise ValueError("Output header is missing.")
    columns, count = struct.unpack_from("<II", blob)
    if not 1 <= columns <= 64 or count > 100000 or (count + 1) * columns > 2000000:
        raise ValueError("Invalid output row or column count.")
    offset, table = 8, []
    for _ in range(count + 1):
        row = []
        for _ in range(columns):
            if offset + 4 > len(blob):
                raise ValueError("Output is truncated.")
            size = struct.unpack_from("<I", blob, offset)[0]
            offset += 4
            if size > 16384 or offset + size > len(blob):
                raise ValueError("Invalid output cell length.")
            row.append(blob[offset:offset + size])
            offset += size
        table.append(tuple(row))
    if offset != len(blob):
        raise ValueError("Unexpected trailing output.")
    return table[0], table[1:]


def verify_output(blob, answer_header, answer_rows, expected_blob=None):
    if expected_blob is not None and blob == expected_blob:
        return None
    try:
        header, actual = decode_table(blob)
    except ValueError as exc:
        return str(exc)
    if header != tuple(s.encode("utf-8") for s in answer_header):
        return "CSV header was changed."
    expected = [tuple(s.encode("utf-8") for s in row) for row in answer_rows]
    if len(actual) != len(expected):
        return "Output row count does not match the answer."
    for index, (a, b) in enumerate(zip(actual, expected), 1):
        if a != b:
            return f"Data row {index} does not match the answer (header excluded)."
    return None
