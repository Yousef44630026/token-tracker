"""Optional OpenTelemetry bridge stays lazy, bounded, and source-truth neutral."""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests._harness import make_checker  # noqa: E402
from tracker.adapters.bedrock_converse_adapter import BedrockConverseAdapter  # noqa: E402
from tracker.context.propagation import new_trace  # noqa: E402
from tracker.export import otel_sdk  # noqa: E402
from tracker.export.otel_projection import TOKEN_USAGE_EXPLICIT_BUCKET_BOUNDARIES, TOKEN_USAGE_METRIC_NAME  # noqa: E402
from tracker.normalization.normalizer import normalize  # noqa: E402

check = make_checker()
FIXTURE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "bedrock_converse_cache.SIMULATED.json")

with open(FIXTURE, encoding="utf-8") as handle:
    event = normalize(json.load(handle)["response"], BedrockConverseAdapter(), context=new_trace())


class Histogram:
    def __init__(self) -> None:
        self.records = []

    def record(self, value, *, attributes):
        self.records.append((value, attributes))


class Meter:
    def __init__(self) -> None:
        self.calls = []
        self.histogram = Histogram()

    def create_histogram(self, name, **kwargs):
        self.calls.append((name, kwargs))
        return self.histogram


meter = Meter()
recorder = otel_sdk.recorder_from_meter(meter)
check(meter.calls[0][0] == TOKEN_USAGE_METRIC_NAME, "bridge creates the standard GenAI metric")
check(
    meter.calls[0][1]["explicit_bucket_boundaries_advisory"] == TOKEN_USAGE_EXPLICIT_BUCKET_BOUNDARIES,
    "bridge supplies bounded explicit token buckets",
)
check(recorder.record(event) == 2 and len(meter.histogram.records) == 2, "one event records input and output observations")
check(recorder.record_many([event, event]) == 4, "record_many reports observation count")


class LegacyMeter(Meter):
    def create_histogram(self, name, **kwargs):
        if "explicit_bucket_boundaries_advisory" in kwargs:
            raise TypeError("unsupported advisory")
        return super().create_histogram(name, **kwargs)


legacy = LegacyMeter()
otel_sdk.recorder_from_meter(legacy)
check("explicit_bucket_boundaries_advisory" not in legacy.calls[0][1], "older Meter APIs receive a compatible fallback")


class FakeExporter:
    instances = []

    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs
        self.__class__.instances.append(self)


class FakeReader:
    instances = []

    def __init__(self, exporter, *, export_interval_millis) -> None:
        self.exporter = exporter
        self.export_interval_millis = export_interval_millis
        self.__class__.instances.append(self)


class FakeResource:
    @classmethod
    def create(cls, attributes):
        return dict(attributes)


class FakeProvider:
    instances = []

    def __init__(self, *, resource, metric_readers) -> None:
        self.resource = resource
        self.metric_readers = metric_readers
        self.meter = Meter()
        self.flush_timeout = None
        self.shutdown_timeout = None
        self.__class__.instances.append(self)

    def get_meter(self, name):
        self.scope = name
        return self.meter

    def force_flush(self, *, timeout_millis):
        self.flush_timeout = timeout_millis
        return True

    def shutdown(self, *, timeout_millis):
        self.shutdown_timeout = timeout_millis
        return None


original_loader = otel_sdk._load_otel_components
otel_sdk._load_otel_components = lambda: otel_sdk._OTelComponents(
    exporter=FakeExporter,
    reader=FakeReader,
    meter_provider=FakeProvider,
    resource=FakeResource,
)
try:
    runtime = otel_sdk.create_otlp_http_runtime(
        endpoint="http://collector:4318/v1/metrics",
        headers={"authorization": "test-only"},
        service_name="unit-service",
        resource_attributes={"deployment.environment.name": "test"},
        export_interval_millis=1234,
    )
finally:
    otel_sdk._load_otel_components = original_loader

provider = FakeProvider.instances[-1]
check(provider.resource["service.name"] == "unit-service", "runtime sets an explicit service.name")
check(provider.resource["deployment.environment.name"] == "test", "runtime preserves extra resource attributes")
check(provider.scope == otel_sdk.DEFAULT_INSTRUMENTATION_SCOPE, "runtime uses a stable instrumentation scope")
check(FakeReader.instances[-1].export_interval_millis == 1234, "runtime configures periodic export")
check(
    FakeExporter.instances[-1].kwargs["endpoint"].endswith("/v1/metrics"),
    "runtime passes the OTLP metrics endpoint to the official exporter",
)
check(runtime.recorder.record(event) == 2, "isolated OTLP runtime records tracker events")
check(runtime.force_flush(99) and provider.flush_timeout == 99, "force_flush delegates with timeout")
check(runtime.shutdown(101) and provider.shutdown_timeout == 101, "shutdown treats SDK None as success")

for invalid_interval in (0, -1):
    try:
        otel_sdk.create_otlp_http_runtime(export_interval_millis=invalid_interval)
    except ValueError:
        rejected = True
    else:
        rejected = False
    check(rejected, f"invalid export interval {invalid_interval} is rejected before SDK loading")

sys.exit(check.report("RESULT test_otel_sdk_bridge"))
