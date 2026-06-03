from __future__ import annotations

import logging
import time
from collections import defaultdict
from contextlib import contextmanager
from threading import Lock
from typing import Iterator

from app.core.config import get_settings

logger = logging.getLogger("app.telemetry")


class Telemetry:
    def __init__(self) -> None:
        self._settings = get_settings()
        self._lock = Lock()
        self._counters: dict[str, int] = defaultdict(int)
        self._latency_totals: dict[str, float] = defaultdict(float)
        self._latency_counts: dict[str, int] = defaultdict(int)

    def inc(self, name: str, value: int = 1) -> None:
        with self._lock:
            self._counters[name] += value

    def observe_ms(self, name: str, duration_ms: float) -> None:
        with self._lock:
            self._latency_totals[name] += duration_ms
            self._latency_counts[name] += 1

    @contextmanager
    def span(self, name: str, **attrs) -> Iterator[None]:
        started = time.perf_counter()
        self.inc(f"{name}.count", 1)
        try:
            yield
            self.inc(f"{name}.success", 1)
        except Exception:
            self.inc(f"{name}.error", 1)
            logger.exception("telemetry.span.error name=%s attrs=%s", name, attrs)
            raise
        finally:
            elapsed_ms = (time.perf_counter() - started) * 1000
            self.observe_ms(name, elapsed_ms)
            logger.debug("telemetry.span name=%s elapsed_ms=%.2f attrs=%s", name, elapsed_ms, attrs)

    def snapshot(self) -> dict:
        with self._lock:
            latency_avg = {
                key: (self._latency_totals[key] / self._latency_counts[key]) if self._latency_counts[key] else 0.0
                for key in self._latency_totals
            }
            return {
                "service": self._settings.app_name,
                "counters": dict(self._counters),
                "latency_avg_ms": latency_avg,
            }

    def quick_alerts(self) -> list[dict[str, str]]:
        snap = self.snapshot()
        counters = snap["counters"]
        alerts: list[dict[str, str]] = []
        tool_errors = int(counters.get("tool.execute.error", 0))
        tool_count = int(counters.get("tool.execute.count", 0))
        if tool_count >= 10 and tool_errors / max(tool_count, 1) > 0.2:
            alerts.append({"severity": "warning", "message": "Tool error ratio is above 20%."})
        agent_errors = int(counters.get("agent.run.error", 0))
        if agent_errors > 0:
            alerts.append({"severity": "info", "message": f"Detected {agent_errors} agent run errors."})
        return alerts

    def record_tool_outcome(
        self,
        *,
        tool_name: str,
        tenant_id: str,
        ok: bool,
        error_code: str | None,
        duration_ms: int,
    ) -> None:
        status_key = "success" if ok else "error"
        self.inc(f"tool.taxonomy.{tool_name}.{status_key}", 1)
        self.inc(f"tool.tenant.{tenant_id}.{status_key}", 1)
        if error_code:
            self.inc(f"tool.error_code.{error_code}", 1)
        self.observe_ms(f"tool.taxonomy.{tool_name}", float(duration_ms))


telemetry = Telemetry()
