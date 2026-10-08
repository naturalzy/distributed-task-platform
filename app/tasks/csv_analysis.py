from __future__ import annotations

import csv
import math
from decimal import Decimal, InvalidOperation, localcontext
from typing import Any

from app.core.config import get_settings
from app.services.csv_storage import csv_path

MISSING_VALUES = {"", "na", "n/a", "null", "nan"}


class CSVAnalysisError(ValueError):
    """A safe, user-facing failure reason without paths or uploaded cell values."""


def run_csv_analysis(parameters: dict[str, Any]) -> dict[str, Any]:
    settings = get_settings()
    path = csv_path(parameters["file_id"])
    reader = None
    try:
        with path.open(encoding="utf-8-sig", newline="") as source, localcontext() as context:
            context.prec = 50
            reader = csv.reader(source, strict=True)
            headers = next(reader, None)
            if not headers:
                raise CSVAnalysisError("CSV format error: a header row is required")
            headers = [name.strip() for name in headers]
            if any(not name for name in headers) or len(set(headers)) != len(headers):
                raise CSVAnalysisError("CSV format error: column names must be nonempty and unique")
            if len(headers) > settings.csv_max_columns:
                raise CSVAnalysisError(
                    f"CSV format error: at most {settings.csv_max_columns} columns are allowed"
                )
            columns = [
                {
                    "missing": 0,
                    "count": 0,
                    "numeric": True,
                    "total": Decimal(0),
                    "min": None,
                    "max": None,
                }
                for _ in headers
            ]
            row_count = 0
            for row in reader:
                if not row:  # Ignore physically empty lines, but count rows containing empty cells.
                    continue
                if len(row) != len(headers):
                    raise CSVAnalysisError(
                        f"CSV format error: line {reader.line_num} has {len(row)} fields; "
                        f"expected {len(headers)}"
                    )
                row_count += 1
                if row_count > settings.csv_max_rows:
                    raise CSVAnalysisError(
                        f"CSV format error: at most {settings.csv_max_rows} data rows are allowed"
                    )
                for column, cell in zip(columns, row, strict=True):
                    value = cell.strip()
                    if value.lower() in MISSING_VALUES:
                        column["missing"] += 1
                        continue
                    column["count"] += 1
                    if not column["numeric"]:
                        continue
                    try:
                        number = Decimal(value)
                    except InvalidOperation:
                        column["numeric"] = False
                        continue
                    if not number.is_finite() or not math.isfinite(float(number)):
                        raise CSVAnalysisError(
                            f"CSV format error: line {reader.line_num} has an out-of-range number"
                        )
                    column["total"] += number
                    column["min"] = number if column["min"] is None else min(column["min"], number)
                    column["max"] = number if column["max"] is None else max(column["max"], number)

            numeric_columns = {}
            for name, column in zip(headers, columns, strict=True):
                if column["numeric"] and column["count"]:
                    numeric_columns[name] = {
                        "count": column["count"],
                        "mean": float(column["total"] / column["count"]),
                        "min": float(column["min"]),
                        "max": float(column["max"]),
                    }
            return {
                "row_count": row_count,
                "column_count": len(headers),
                "missing_values": sum(column["missing"] for column in columns),
                "missing_values_by_column": {
                    name: column["missing"] for name, column in zip(headers, columns, strict=True)
                },
                "numeric_columns": numeric_columns,
            }
    except UnicodeDecodeError as exc:
        raise CSVAnalysisError("CSV format error: file must use UTF-8 encoding") from exc
    except csv.Error as exc:
        line = reader.line_num if reader is not None else 1
        raise CSVAnalysisError(
            f"CSV format error: malformed quoting or oversized field near line {line}"
        ) from exc
