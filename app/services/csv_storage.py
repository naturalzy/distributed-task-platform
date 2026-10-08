from __future__ import annotations

from typing import BinaryIO
from uuid import UUID

from app.core.config import get_settings


class CSVUploadTooLarge(ValueError):
    pass


def csv_path(file_id: str):
    # Generated IDs, never user filenames or arbitrary paths, identify stored files.
    return get_settings().csv_upload_dir.resolve() / f"{UUID(file_id)}.csv"


def store_csv(source: BinaryIO, file_id: str) -> None:
    settings = get_settings()
    path = csv_path(file_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    destination = path.open("xb")
    try:
        with destination:
            size = 0
            while chunk := source.read(1024 * 1024):
                size += len(chunk)
                if size > settings.csv_max_upload_bytes:
                    raise CSVUploadTooLarge(
                        f"CSV exceeds the {settings.csv_max_upload_bytes}-byte upload limit"
                    )
                destination.write(chunk)
    except BaseException:
        # Only this newly created, partial file is removed; completed inputs are retained.
        path.unlink(missing_ok=True)
        raise
