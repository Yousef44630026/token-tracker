# Collector Supervision Drill - 2026-07-14

## Scope

- task: `AI Token Tracker Collector`
- trigger: current-user logon
- listener: `127.0.0.1:8787`
- store: `C:\ai-token-tracker-data\collector_events.jsonl`
- durable persistence: enabled
- log: `C:\ai-token-tracker-data\logs\collector-service.log`

No credential value was written to the task action, report, or log.

## Falsification And Correction

The initial design relied only on Task Scheduler `RestartOnFailure`. Killing the Python
child produced task result `0xFFFFFFFF`, but the action did not restart within 86.1 seconds.
The drill therefore failed.

The runner was changed to supervise the collector child directly with a bounded 10-second
restart delay. Task Scheduler remains the outer layer for logon start and wrapper recovery.

## Passing Drill

- old collector PID: `21268`
- injected fault: forced termination of the collector child
- replacement collector PID: `8276`
- measured recovery: `11.1 seconds`
- post-recovery `/healthz`: `ok`
- post-recovery ledger: readable, `0` events, `0` contributing tokens
- task state after drill: `Running`

The PID changed and the same configured store remained available. This demonstrates local
restart-on-failure without claiming that a full Windows logon cycle has been tested.

## Independent Monitor And Alert Drill

An independent scheduled task, `AI Token Tracker Monitor`, now probes `/healthz` and
`/v1/stats` every 60 seconds. It writes append-only, secret-free evidence to:

- `C:\ai-token-tracker-data\health\collector-health.jsonl`
- `C:\ai-token-tracker-data\health\collector-alerts.jsonl`

The alert drill disabled automatic recovery temporarily, terminated collector PID `8276`,
and guaranteed task re-enablement in a `finally` block.

- offline observation: `URLError`, `healthy=false`
- alert signal: `collector_unavailable`
- alert timestamp: `2026-07-14T22:04:27Z`
- post-recovery observation: `healthy=true`, `0` events, `0` contributing tokens
- measured recovery: `2 seconds`
- task state after drill: `Running`

A three-sample recovery soak then reported `uptime_ratio=1.0`, no outage, no counter
regression, and an unchanged SHA-256 store prefix. This proves the soak harness and short
recovery window only; it does not replace the required 72-hour representative-load run.

## Remaining Evidence

- perform a real sign-out/sign-in or reboot drill to prove the logon trigger
- run the collector under representative load before and after an injected failure
- run the strict 72-hour soak and archive its summary
