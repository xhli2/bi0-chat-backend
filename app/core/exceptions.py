from datetime import datetime, timezone
from http import HTTPStatus

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel


class ErrorBody(BaseModel):
    error: str
    code: str
    detail: str
    timestamp: datetime
    request_id: str | None = None


class ApiError(Exception):
    def __init__(self, *, status_code: int, code: str, detail: str, error: str = "API_ERROR") -> None:
        self.status_code = status_code
        self.code = code
        self.detail = detail
        self.error = error
        super().__init__(detail)


def _build_error_response(request: Request, status_code: int, code: str, detail: str, error: str) -> JSONResponse:
    body = ErrorBody(
        error=error,
        code=code,
        detail=detail,
        timestamp=datetime.now(timezone.utc),
        request_id=request.headers.get("X-Request-ID"),
    )
    return JSONResponse(status_code=status_code, content=body.model_dump(mode="json"))


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def handle_api_error(request: Request, exc: ApiError) -> JSONResponse:
        return _build_error_response(request, exc.status_code, exc.code, exc.detail, exc.error)

    @app.exception_handler(Exception)
    async def handle_uncaught_error(request: Request, exc: Exception) -> JSONResponse:
        status = HTTPStatus.INTERNAL_SERVER_ERROR
        return _build_error_response(
            request=request,
            status_code=status.value,
            code="INTERNAL_SERVER_ERROR",
            detail="An unexpected error occurred.",
            error=status.phrase,
        )
