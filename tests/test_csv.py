import csv
import io
import pytest
from judge.datasets import decode_table, encode_table, parse_csv, validate_pair, verify_output


def test_csv_bom_quotes_newlines_empty_cells_and_unicode():
    text = '\ufeffid,name,memo\r\n2,"가,나","a\nb"\r\n1,"\"\"quoted\"\"",\r\n'
    header, rows = parse_csv(text)
    assert header == ["id", "name", "memo"]
    assert rows == [["2", "가,나", "a\nb"], ["1", '"quoted"', ""]]
    output = encode_table(header, rows[::-1])
    assert verify_output(output, header, rows[::-1]) is None
    assert verify_output(output, header, rows) is not None
    assert decode_table(output)[1][0][1] == b'"quoted"'


def test_pair_checks_duplicate_multiplicity_and_cell_identity():
    assert validate_pair("a,b\nx,1\nx,1\ny,2\n", "a,b\ny,2\nx,1\nx,1\n") == (3, 2)
    with pytest.raises(ValueError):
        validate_pair("a,b\nx,1\nx,1\ny,2\n", "a,b\ny,2\ny,2\nx,1\n")
    with pytest.raises(ValueError):
        validate_pair("a,b\nx,1\n", "a,c\nx,1\n")


@pytest.mark.parametrize("text", ["", "a,b\n1\n", 'a,b\n"unfinished,2', "a\n\x00\n"])
def test_invalid_csv_rejected(text):
    with pytest.raises(ValueError):
        parse_csv(text)


def test_real_width_and_empty_dataset():
    header = [str(i) for i in range(34)]
    assert parse_csv(",".join(header) + "\n")[0] == header
    assert verify_output(encode_table(header, []), header, []) is None


def test_output_truncation_extra_bytes_and_header_mutation():
    good = encode_table(["key"], [["x"]])
    assert verify_output(good[:-1], ["key"], [["x"]])
    assert verify_output(good + b"bad", ["key"], [["x"]])
    assert verify_output(encode_table(["bad"], [["x"]]), ["key"], [["x"]])
