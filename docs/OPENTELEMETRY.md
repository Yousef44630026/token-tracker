# OpenTelemetry Metric Export

The JSONL ledger remains the source of truth. OpenTelemetry receives a secondary metric view:
`gen_ai.client.token.usage`, split by `gen_ai.token.type=input|output`. Superseded and
non-authoritative events are excluded, and estimates are excluded by default.

The projection follows the official [OpenTelemetry GenAI semantic conventions](https://opentelemetry.io/docs/specs/semconv/gen-ai/gen-ai-metrics/)
and the isolated runtime follows the [Python exporter setup](https://opentelemetry.io/docs/languages/python/exporters/).

## Existing MeterProvider

No OpenTelemetry dependency is needed in the tracker package for this path. Pass the Meter from
your application's SDK setup:

```python
from tracker.export.otel_sdk import recorder_from_meter

recorder = recorder_from_meter(meter)
recorder.record(event)
```

The metric does not carry event IDs, trace IDs, request IDs, user IDs, or prompt content as
labels. Provider, operation, model, workflow, surface, and input/output direction are the only
dimensions produced by the projection.

## Isolated OTLP/HTTP Runtime

Install the optional exporter:

```console
pip install -e ".[otel]"
```

Configure the official exporter with its standard environment variables:

```powershell
$env:OTEL_EXPORTER_OTLP_METRICS_ENDPOINT = "http://localhost:4318/v1/metrics"
$env:OTEL_SERVICE_NAME = "my-llm-service"
```

Then create a runtime and close it cleanly:

```python
from tracker.export.otel_sdk import create_otlp_http_runtime

runtime = create_otlp_http_runtime(service_name="my-llm-service")
try:
    runtime.recorder.record(event)
    runtime.force_flush()
finally:
    runtime.shutdown()
```

The helper owns its MeterProvider but does not install it globally. Credentials may be supplied
through standard OTLP environment variables or the `headers=` argument; never write them into
JSONL, audit artifacts, or source control.

## Aggregation Rules

- The histogram value is a provider-observed token quantity, not a cumulative ledger total.
- One authoritative event emits at most one input and one output observation.
- Cache read/write input buckets are included once according to tracker additivity rules.
- Reasoning/thinking subtotals do not duplicate output.
- Replaying a historical JSONL file emits the same observations again. Do not use replay as an
  incremental exporter without an external checkpoint; the tracker deliberately avoids claiming
  exactly-once delivery to an OTLP backend.
- OTLP loss or backend aggregation never changes the auditable JSONL total.
