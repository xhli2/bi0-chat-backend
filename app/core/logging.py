import logging
import time
import uuid
from collections.abc import Awaitable, Callable

from fastapi import Request, Response

from app.core.config import get_settings
from app.core.telemetry import telemetry


def configure_logging() -> None:
    settings = get_settings()
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


async def request_log_middleware(
    request: Request,
    call_next: Callable[[Request], Awaitable[Response]],
) -> Response:
    logger = logging.getLogger("app.request")
    request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
    started_at = time.perf_counter()
    telemetry.inc("http.request.count")

    try:
        response = await call_next(request)
    except Exception:
        elapsed = (time.perf_counter() - started_at) * 1000
        telemetry.inc("http.request.error")
        telemetry.observe_ms("http.request", elapsed)
        logger.exception(
            "request.failed request_id=%s method=%s path=%s elapsed_ms=%.2f",
            request_id,
            request.method,
            request.url.path,
            elapsed,
        )
        raise

    elapsed = (time.perf_counter() - started_at) * 1000
    telemetry.observe_ms("http.request", elapsed)
    response.headers["X-Request-ID"] = request_id
    logger.info(
        "request.completed request_id=%s method=%s path=%s status=%s elapsed_ms=%.2f",
        request_id,
        request.method,
        request.url.path,
        response.status_code,
        elapsed,
    )
    return response
