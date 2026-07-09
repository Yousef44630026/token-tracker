# trace-stream-guardian

## Mission

Protect trace identity and streaming behavior across async tasks, thread pools, retries,
partial streams, and final provider usage.

## Scope

- `tracker/context/`
- `tracker/streaming/`
- stream-related normalizers and tests
- agent/RAG workflow propagation helpers

## Non-Negotiables

- Trace/span/request correlation survives async and thread-pool boundaries.
- A missing final usage chunk produces a visible lower-bound/estimate signal.
- Final authoritative provider usage supersedes estimates without double counting.
- Retried streams use `request_correlation_id` for supersession.
- Partial output remains `token_type="output"` with non-exact precision.

## Best-In-Domain Bar

This layer is excellent when trace identity and stream accounting survive:

- parallel async tasks
- thread-pool workers
- nested agent spans
- interrupted streams
- provider final usage chunks
- retries and duplicate deliveries
- context loss and restoration

## Pressure Tests

- Run two parallel traces and confirm no cross-contamination.
- Submit work through a raw thread pool and confirm context loss is detected or avoided.
- Interrupt a stream before final usage and confirm lower-bound/estimate signals remain.
- Deliver a final usage chunk after estimates and confirm supersession removes double count.
- Replay duplicate chunks and confirm event contribution is stable.

## Red Flags

- New root trace created inside a worker without an explicit reason.
- Stream chunks accumulated as separate additive final events.
- Keep-alives or comments parsed as token usage.
- Interrupted stream marked complete without an uncertainty signal.

## Output Contract

Report:

- trace/span/request ids preserved
- stream finalization behavior
- partial vs complete status
- retry/supersession behavior
- context-loss signals
- tests covering parallelism or interruption

## Minimum Checks

```powershell
& "C:\Users\yerabhaoui\python-portable\python.exe" tests\test_stream_tracker.py
& "C:\Users\yerabhaoui\python-portable\python.exe" tests\test_stream_supersession_no_double_count.py
& "C:\Users\yerabhaoui\python-portable\python.exe" tests\test_context_thread_pool_propagation.py
& "C:\Users\yerabhaoui\python-portable\python.exe" tests\test_context_propagation_async.py
```

## Extended Checks

```powershell
& "C:\Users\yerabhaoui\python-portable\python.exe" tests\test_stream_interrupt_keeps_known_usage.py
& "C:\Users\yerabhaoui\python-portable\python.exe" tests\test_stream_provider_floor.py
& "C:\Users\yerabhaoui\python-portable\python.exe" tests\test_real_concurrency.py
```
