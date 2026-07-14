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
restart-on-failure without claiming that a full Windows logon cycle or external downtime
alert has been tested.

## Remaining Evidence

- perform a real sign-out/sign-in or reboot drill to prove the logon trigger
- connect an external health monitor and verify a downtime notification
- run the collector under representative load before and after an injected failure
