from __future__ import annotations

import logging
import time
from collections import defaultdict
from contextlib import contextmanager
from dataclasses import dataclass
from threading import Lock
from typing import Iterator

from app.core.config import get_settings

logger = logging.getLogger("app.telemetry")

try:
    from opentelemetry import metrics
    from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
    from opentelemetry.sdk.resources import Resource
except Exception:  # pragma: no cover - optional dependency
    metrics = None
    OTLPMetricExporter = None
    MeterProvider = None
    PeriodicExportingMetricReader = None
    Resource = None


@dataclass
class CounterSnapshot:
    name: str
    value: int


class Telemetry:
    def __init__(self) -> None:
        self._settings = get_settings()
        self._lock = Lock()
        self._counters: dict[str, int] = defaultdict(int)
        self._latency_totals: dict[str, float] = defaultdict(float)
        self._latency_counts: dict[str, int] = defaultdict(int)
        self._otel_counters: dict[str, object] = {}
        self._otel_histograms: dict[str, object] = {}
        self._meter = None
        self._configure_otel()

    def inc(self, name: str, value: int = 1) -> None:
        with self._lock:
            self._counters[name] += value
        if self._meter is not None:
            counter = self._otel_counters.get(name)
            if counter is None:
                counter = self._meter.create_counter(name)
                self._otel_counters[name] = counter
            counter.add(value)

    def observe_ms(self, name: str, duration_ms: float) -> None:
        with self._lock:
            self._latency_totals[name] += duration_ms
            self._latency_counts[name] += 1
        if self._meter is not None:
            hist_name = f"{name}.latency.ms"
            histogram = self._otel_histograms.get(hist_name)
            if histogram is None:
                histogram = self._meter.create_histogram(hist_name)
                self._otel_histograms[hist_name] = histogram
            histogram.record(duration_ms)

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
                "service": self._settings.otel_service_name,
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

    def _configure_otel(self) -> None:
        if not self._settings.otel_enabled:
            return
        if not all([metrics, OTLPMetricExporter, MeterProvider, PeriodicExportingMetricReader, Resource]):
            logger.warning("OTEL enabled but opentelemetry packages are unavailable.")
            return
        try:
            exporter = OTLPMetricExporter(
                endpoint=self._settings.otel_exporter_otlp_endpoint,
                headers=self._settings.parsed_otel_exporter_headers,
            )
            reader = PeriodicExportingMetricReader(
                exporter=exporter,
                export_interval_millis=max(1000, self._settings.otel_export_interval_millis),
            )
            provider = MeterProvider(
                metric_readers=[reader],
                resource=Resource.create({"service.name": self._settings.otel_service_name}),
            )
            metrics.set_meter_provider(provider)
            self._meter = metrics.get_meter(self._settings.otel_service_name)
            logger.info(
                "OTEL metrics exporter configured endpoint=%s service=%s",
                self._settings.otel_exporter_otlp_endpoint,
                self._settings.otel_service_name,
            )
        except Exception:
            logger.exception("Failed to initialize OTEL exporter.")


telemetry = Telemetry()
