"""Errors returned by the API as {"error": {"code", "message", "details"}}."""
from __future__ import annotations

from typing import Any


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, details: Any = None):
        super().__init__(message)
        self.status, self.code, self.message, self.details = status, code, message, details


def not_found(what: str, name: str) -> ApiError:
    return ApiError(404, "not_found", f"{what} {name!r} not found")


def conflict(code: str, message: str, details: Any = None) -> ApiError:
    return ApiError(409, code, message, details)


def invalid(code: str, message: str, details: Any = None) -> ApiError:
    return ApiError(422, code, message, details)


def pydantic_errors(e) -> list[str]:
    """Readable messages from a pydantic ValidationError."""
    out = []
    for err in e.errors():
        loc = ".".join(str(x) for x in err["loc"])
        out.append(f"{loc}: {err['msg']}" if loc else err["msg"])
    return out
