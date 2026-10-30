"""Uploaded files (multipart): safe names and a size limit."""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import BinaryIO

from .config import settings
from .errors import ApiError


def upload_name(filename: str | None) -> str:
    """The client's file name without any path; hidden or empty names are refused."""
    name = (filename or "").replace("\\", "/").rsplit("/", 1)[-1].strip()
    if not name or name.startswith(".") or name in ("..",):
        raise ApiError(400, "invalid_filename", f"unusable file name {filename!r}")
    return name


def copy_limited(data: BinaryIO, dest: Path) -> None:
    limit = settings().max_upload_mb * 2**20
    n = 0
    with dest.open("wb") as out:
        while chunk := data.read(1 << 20):
            n += len(chunk)
            if n > limit:
                raise ApiError(413, "upload_too_large", f"uploads are limited to {settings().max_upload_mb} MB")
            out.write(chunk)
