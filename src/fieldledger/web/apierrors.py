"""Request-level API errors (see docs/api-contract.md, section 2).

Messages are fixed strings or the text of a FieldLedgerError. They never contain paths, cell
values or header text.
"""

from __future__ import annotations

from .contract import ERROR_HTTP_STATUS


class ApiError(Exception):
    def __init__(self, code: str, message: str) -> None:
        if code not in ERROR_HTTP_STATUS:
            raise ValueError("unknown API error code")
        super().__init__(message)
        self.code = code
        self.message = message

    @property
    def status(self) -> int:
        return ERROR_HTTP_STATUS[self.code]

    def body(self) -> dict:
        return {"error": {"code": self.code, "message": self.message}}
