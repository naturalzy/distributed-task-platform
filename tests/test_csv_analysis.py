from __future__ import annotations

import io
import uuid

import pytest

from app.core.config import get_settings
from app.services.csv_storage import CSVUploadTooLarge, csv_path, store_csv
from app.tasks.csv_analysis import CSVAnalysisError, run_csv_analysis


@pytest.fixture
def csv_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("CSV_UPLOAD_DIR", str(tmp_path))
    get_settings.cache_clear()
    try:
        yield tmp_path
    finally:
        get_settings.cache_clear()


def analyze(content: bytes) -> dict:
    file_id = str(uuid.uuid4())
    store_csv(io.BytesIO(content), file_id)
    return run_csv_analysis({"file_id": file_id})


def test_statistics_missing_values_bom_and_quoted_multiline_fields(csv_environment):
    content = (
        "\ufeffname,amount,score,empty,mixed\r\n"
        "Alice,10,1,,1\r\n"
        '"Bob, Jr",20,,NA,text\r\n'
        '"quoted\nname",,3,null,2\r\n'
    ).encode("utf-8")
    assert analyze(content) == {
        "row_count": 3,
        "column_count": 5,
        "missing_values": 5,
        "missing_values_by_column": {"name": 0, "amount": 1, "score": 1, "empty": 3, "mixed": 0},
        "numeric_columns": {
            "amount": {"count": 2, "mean": 15.0, "min": 10.0, "max": 20.0},
            "score": {"count": 2, "mean": 2.0, "min": 1.0, "max": 3.0},
        },
    }


def test_negative_decimal_scientific_notation_and_large_finite_mean(csv_environment):
    result = analyze(b"value,large\n-2.5,1e308\n1.5,1e308\n4e0,1e308\n")
    assert result["numeric_columns"] == {
        "value": {"count": 3, "mean": 1.0, "min": -2.5, "max": 4.0},
        "large": {"count": 3, "mean": 1e308, "min": 1e308, "max": 1e308},
    }


def test_header_only_and_missing_rows_are_distinct(csv_environment):
    assert analyze(b"a,b\n")["row_count"] == 0
    result = analyze(b"a,b\n\n,\nNA, NaN\n")
    assert result["row_count"] == 2
    assert result["missing_values"] == 4
    assert result["numeric_columns"] == {}


@pytest.mark.parametrize(
    ("content", "reason"),
    [
        (b"", "header row"),
        (b"a,a\n1,2\n", "nonempty and unique"),
        (b",b\n1,2\n", "nonempty and unique"),
        (b"a,b\n1\n", "expected 2"),
        (b"a,b\n1,2,3\n", "expected 2"),
        (b'a,b\n1,"unfinished\n', "malformed quoting"),
        (b"a\n\xff\n", "UTF-8"),
        (b"a\ninf\n", "out-of-range"),
        (b"a\n1e400\n", "out-of-range"),
        (b"a\n" + b"x" * 131073, "oversized field"),
    ],
    ids=[
        "empty",
        "duplicate-header",
        "empty-header",
        "short-row",
        "long-row",
        "unclosed-quote",
        "invalid-utf8",
        "infinity",
        "numeric-overflow",
        "oversized-field",
    ],
)
def test_invalid_csv_has_safe_failure_reason(csv_environment, content, reason):
    with pytest.raises(CSVAnalysisError, match=reason):
        analyze(content)


def test_row_and_column_limits(csv_environment, monkeypatch):
    monkeypatch.setenv("CSV_MAX_ROWS", "1")
    monkeypatch.setenv("CSV_MAX_COLUMNS", "1")
    get_settings.cache_clear()
    with pytest.raises(CSVAnalysisError, match="at most 1 columns"):
        analyze(b"a,b\n1,2\n")
    with pytest.raises(CSVAnalysisError, match="at most 1 data rows"):
        analyze(b"a\n1\n2\n")


def test_storage_size_limit_removes_only_partial_file(csv_environment, monkeypatch):
    monkeypatch.setenv("CSV_MAX_UPLOAD_BYTES", "8")
    get_settings.cache_clear()
    file_id = str(uuid.uuid4())
    with pytest.raises(CSVUploadTooLarge):
        store_csv(io.BytesIO(b"123456789"), file_id)
    assert not csv_path(file_id).exists()


def test_storage_does_not_overwrite_existing_input_or_accept_paths(csv_environment):
    file_id = str(uuid.uuid4())
    store_csv(io.BytesIO(b"original"), file_id)
    with pytest.raises(FileExistsError):
        store_csv(io.BytesIO(b"replacement"), file_id)
    assert csv_path(file_id).read_bytes() == b"original"
    with pytest.raises(ValueError):
        csv_path("../../outside")
