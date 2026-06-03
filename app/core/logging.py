import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path

from fastapi import Request, Response

from app.core.config import get_settings
from app.core.telemetry import telemetry

_LOGGING_CONFIGURED = False
_LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"


def configure_logging() -> None:
    global _LOGGING_CONFIGURED
    if _LOGGING_CONFIGURED:
        return

    settings = get_settings()
    level = getattr(logging, settings.log_level.upper(), logging.INFO)
    formatter = logging.Formatter(_LOG_FORMAT)

    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()

    console = logging.StreamHandler()
    console.setFormatter(formatter)
    root.addHandler(console)

    if settings.log_file.strip():
        log_path = Path(settings.log_file.strip())
        log_path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_path, encoding="utf-8")
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)

    _LOGGING_CONFIGURED = True


async def request_log_middleware(
    request: Request,
    call_next: Callable[[Request], Awaitable[Response]],
) -> Response:
    logger = logging.getLogger("app.request")
    request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
    trace_id = request.headers.get("X-Trace-ID")
    started_at = time.perf_counter()
    telemetry.inc("http.request.count")

    try:
        response = await call_next(request)
    except Exception:
        elapsed = (time.perf_counter() - started_at) * 1000
        telemetry.inc("http.request.error")
        telemetry.observe_ms("http.request", elapsed)
        raise

    elapsed = (time.perf_counter() - started_at) * 1000
    telemetry.observe_ms("http.request", elapsed)
    response.headers["X-Request-ID"] = request_id
    logger.info(
        "request.completed request_id=%s trace_id=%s method=%s path=%s status=%s elapsed_ms=%.2f",
        request_id,
        trace_id,
        request.method,
        request.url.path,
        response.status_code,
        elapsed,
    )
    return response
