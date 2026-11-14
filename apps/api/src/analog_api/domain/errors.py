from __future__ import annotations


class AppError(Exception):
    """An error with a stable machine-readable code and HTTP status."""

    def __init__(
        self,
        code: str,
        message: str,
        status_code: int = 422,
        details: list[dict[str, object]] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details or []
