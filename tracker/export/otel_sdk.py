"""Optional OpenTelemetry SDK/OTLP bridge for derived token measurements.

No OpenTelemetry package is imported when this module is imported. The ledger remains the
source of truth; this bridge emits a secondary, lossy metric view only when explicitly used.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from tracker.export.otel_projection import (
    TOKEN_USAGE_EXPLICIT_BUCKET_BOUNDARIES,
    TOKEN_USAGE_METRIC_NAME,
    TOKEN_USAGE_UNIT,
    record_token_usage,
)
from tracker.models.token_event import TokenEvent

TOKEN_USAGE_DESCRIPTION = "Number of input or output tokens used in a GenAI operation"
DEFAULT_INSTRUMENTATION_SCOPE = "ai-token-tracker"


@dataclass(frozen=True)
class OTelTokenMetricRecorder:
    """Record authoritative TokenEvents into one OpenTelemetry Histogram."""

    histogram: Any
    include_estimates: bool = False

    def record(self, event: TokenEvent) -> int:
        return record_token_usage(event, self.histogram, include_estimates=self.include_estimates)

    def record_many(self, events: Iterable[TokenEvent]) -> int:
        return sum(self.record(event) for event in events)


@dataclass
class OTelMetricRuntime:
    """Own the optional SDK provider and its tracker recorder."""

    recorder: OTelTokenMetricRecorder
    meter_provider: Any

    def force_flush(self, timeout_millis: int = 10_000) -> bool:
        result = self.meter_provider.force_flush(timeout_millis=timeout_millis)
        return True if result is None else bool(result)

    def shutdown(self, timeout_millis: int = 30_000) -> bool:
        result = self.meter_provider.shutdown(timeout_millis=timeout_millis)
        return True if result is None else bool(result)


@dataclass(frozen=True)
class _OTelComponents:
    exporter: Any
    reader: Any
    meter_provider: Any
    resource: Any


def create_token_usage_histogram(meter: Any) -> Any:
    """Create the standard GenAI histogram on an OTel-compatible Meter.

    Older SDKs may not accept the advisory boundary argument. The fallback keeps the metric
    usable while leaving aggregation configuration to the SDK/exporter.
    """
    kwargs = {
        "unit": TOKEN_USAGE_UNIT,
        "description": TOKEN_USAGE_DESCRIPTION,
        "explicit_bucket_boundaries_advisory": TOKEN_USAGE_EXPLICIT_BUCKET_BOUNDARIES,
    }
    try:
        return meter.create_histogram(TOKEN_USAGE_METRIC_NAME, **kwargs)
    except TypeError:
        kwargs.pop("explicit_bucket_boundaries_advisory")
        return meter.create_histogram(TOKEN_USAGE_METRIC_NAME, **kwargs)


def recorder_from_meter(meter: Any, *, include_estimates: bool = False) -> OTelTokenMetricRecorder:
    """Attach the tracker projection to an existing application MeterProvider."""
    return OTelTokenMetricRecorder(
        histogram=create_token_usage_histogram(meter),
        include_estimates=include_estimates,
    )


def _load_otel_components() -> _OTelComponents:
    try:
        from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
        from opentelemetry.sdk.metrics import MeterProvider
        from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
        from opentelemetry.sdk.resources import Resource
    except ImportError as exc:
        raise RuntimeError(
            'OpenTelemetry export is optional; install it with: pip install -e ".[otel]"'
        ) from exc
    return _OTelComponents(
        exporter=OTLPMetricExporter,
        reader=PeriodicExportingMetricReader,
        meter_provider=MeterProvider,
        resource=Resource,
    )


def create_otlp_http_runtime(
    *,
    endpoint: str | None = None,
    headers: Mapping[str, str] | None = None,
    service_name: str = "ai-token-tracker",
    resource_attributes: Mapping[str, str | int | float | bool] | None = None,
    export_interval_millis: int = 60_000,
    include_estimates: bool = False,
    instrumentation_scope: str = DEFAULT_INSTRUMENTATION_SCOPE,
) -> OTelMetricRuntime:
    """Build an isolated OTLP/HTTP metric runtime using lazy optional imports.

    If ``endpoint`` or ``headers`` are omitted, the official exporter reads the standard
    ``OTEL_EXPORTER_OTLP_*`` environment variables. This function intentionally does not set
    the process-global MeterProvider.
    """
    if not isinstance(service_name, str) or not service_name.strip():
        raise ValueError("service_name must be a non-empty string")
    if not isinstance(instrumentation_scope, str) or not instrumentation_scope.strip():
        raise ValueError("instrumentation_scope must be a non-empty string")
    if isinstance(export_interval_millis, bool) or not isinstance(export_interval_millis, int):
        raise TypeError("export_interval_millis must be an integer")
    if export_interval_millis <= 0:
        raise ValueError("export_interval_millis must be positive")

    components = _load_otel_components()
    exporter_kwargs: dict[str, Any] = {}
    if endpoint:
        exporter_kwargs["endpoint"] = endpoint
    if headers:
        exporter_kwargs["headers"] = dict(headers)
    exporter = components.exporter(**exporter_kwargs)
    reader = components.reader(exporter, export_interval_millis=export_interval_millis)
    attributes: dict[str, str | int | float | bool] = {"service.name": service_name.strip()}
    if resource_attributes:
        attributes.update(resource_attributes)
    resource = components.resource.create(attributes)
    provider = components.meter_provider(resource=resource, metric_readers=[reader])
    meter = provider.get_meter(instrumentation_scope.strip())
    return OTelMetricRuntime(
        recorder=recorder_from_meter(meter, include_estimates=include_estimates),
        meter_provider=provider,
    )


__all__ = [
    "DEFAULT_INSTRUMENTATION_SCOPE",
    "OTelMetricRuntime",
    "OTelTokenMetricRecorder",
    "TOKEN_USAGE_DESCRIPTION",
    "create_otlp_http_runtime",
    "create_token_usage_histogram",
    "recorder_from_meter",
]
