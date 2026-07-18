# POSIX Operations

The `.sh` wrappers cover the operational commands that map cleanly across platforms:
collector supervision, Doctor, trusted verification, HTML report, and Power BI export.
Install the project in a virtual environment and select that interpreter explicitly:

```sh
python3 -m venv .venv
.venv/bin/pip install -e '.[reporting]'
export AI_TOKEN_TRACKER_PYTHON="$PWD/.venv/bin/python"
chmod +x scripts/tt-*.sh
```

Keep the ledger and authentication token outside synchronized folders. Put environment values
in a mode-600 file such as `/etc/ai-token-tracker.env`; keep the bearer itself in the file named
by `TRACKER_AUTH_TOKEN_FILE`, never in the crontab.

## Cron Equivalent

The Windows Scheduled Task scripts remain Windows-only. On POSIX, use `cron` for periodic and
boot-time execution. The following is a template: replace `/opt/token-tracker` and data paths,
create the log directory, and verify every command interactively before installing it.

```cron
SHELL=/bin/sh
PATH=/usr/local/bin:/usr/bin:/bin

@reboot /bin/sh -lc '. /etc/ai-token-tracker.env && exec /opt/token-tracker/scripts/tt-collector-run.sh >>/var/lib/ai-token-tracker/logs/collector.log 2>&1'
* * * * * /usr/bin/flock -n /tmp/ai-token-tracker-monitor.lock /bin/sh -lc '. /etc/ai-token-tracker.env && "$AI_TOKEN_TRACKER_PYTHON" -m tracker.ops.collector_monitor --json >>/var/lib/ai-token-tracker/logs/monitor.log 2>&1'
5 * * * * /usr/bin/flock -n /tmp/ai-token-tracker-import.lock /bin/sh -lc '. /etc/ai-token-tracker.env && cd /opt/token-tracker && "$AI_TOKEN_TRACKER_PYTHON" scripts/import_claude_to_collector.py --state-file /var/lib/ai-token-tracker/health/claude-import-state.json --json >>/var/lib/ai-token-tracker/logs/claude-import.log 2>&1'
15 * * * * /usr/bin/flock -n /tmp/ai-token-tracker-dashboard.lock /bin/sh -lc '. /etc/ai-token-tracker.env && "$AI_TOKEN_TRACKER_PYTHON" -m tracker.reporting.excel_dashboard --data-dir /var/lib/ai-token-tracker --output /var/lib/ai-token-tracker/dashboard.xlsx --json >>/var/lib/ai-token-tracker/logs/dashboard.log 2>&1'
25 * * * * /usr/bin/flock -n /tmp/ai-token-tracker-doctor.lock /bin/sh -lc '. /etc/ai-token-tracker.env && /opt/token-tracker/scripts/tt-doctor.sh --store "$TRACKER_STORE" --strict-warnings >>/var/lib/ai-token-tracker/logs/doctor.log 2>&1'
```

`flock -n` prevents overlapping periodic runs. Cron is only the launcher: the collector wrapper
supervises its child and restarts it after a bounded delay. After installation, reboot once and
verify the collector, monitor, import, dashboard, and Doctor logs rather than treating the
presence of crontab entries as operational evidence.

Run reports and exports on demand:

```sh
scripts/tt-report.sh /var/lib/ai-token-tracker/collector_events.jsonl
scripts/tt-powerbi-export.sh --store /var/lib/ai-token-tracker/collector_events.jsonl --output /tmp/powerbi_dataset
scripts/tt-verify.sh
```
