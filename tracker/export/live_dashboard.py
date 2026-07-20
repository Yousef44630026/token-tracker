"""Live local dashboard — animates as the ledger receives tokens.

A small standard-library HTTP server that reads the ledger (archive-aware) and serves:
  * ``/``      a self-contained HTML page that polls ``/data`` and animates counters when
               new tokens arrive (per service / provider / model), flashing changed rows.
  * ``/data``  JSON aggregates, recomputed ONLY when the ledger actually changed (cached by
               store signature), so polling is cheap and the numbers move exactly when new
               events land.

Loopback only; it reads the local ledger directly (no collector round-trip, no auth needed).
Totals use ``event_contributing_tokens`` so the live numbers match the canonical ledger and the
Excel/Power BI exports exactly.

Run:  python -m tracker.export.live_dashboard --store C:\\ai-token-tracker-data\\collector_events.jsonl --port 8790
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
from collections import defaultdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from tracker.derive.derived_fields import event_contributing_tokens
from tracker.storage.file_repository import FileRepository


def _signature(store: str) -> tuple:
    parts: list[tuple[int, int]] = []
    for path in (store, f"{store}.archive"):
        try:
            st = os.stat(path)
            parts.append((int(st.st_size), int(st.st_mtime_ns)))
        except FileNotFoundError:
            parts.append((0, 0))
    # archive is a directory; include its segment count/mtimes
    arch = f"{store}.archive"
    try:
        for entry in sorted(os.scandir(arch), key=lambda e: e.name):
            if entry.name.endswith(".jsonl.gz"):
                st = entry.stat()
                parts.append((int(st.st_size), int(st.st_mtime_ns)))
    except FileNotFoundError:
        pass
    return tuple(parts)


def aggregate(store: str) -> dict[str, Any]:
    repo = FileRepository(store)
    events = 0
    total = 0
    by_service: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    by_provider: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    by_model: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    flags: dict[str, int] = defaultdict(int)
    for event in repo.iter_events():
        events += 1
        contributing = event_contributing_tokens(event)
        total += contributing
        service = str(event.observation.get("service_name") or "unknown")
        provider = event.provider or "unknown"
        model = event.model or "unknown"
        for bucket, key in ((by_service, service), (by_provider, provider), (by_model, model)):
            bucket[key][0] += 1
            bucket[key][1] += contributing
        for flag in event.data_quality_flags:
            flags[flag] += 1

    def rows(bucket: dict[str, list[int]]) -> list[dict[str, Any]]:
        return [
            {"name": name, "events": n, "tokens": tok}
            for name, (n, tok) in sorted(bucket.items(), key=lambda kv: kv[1][1], reverse=True)
        ]

    return {
        "events": events,
        "total_tokens": total,
        "by_service": rows(by_service),
        "by_provider": rows(by_provider),
        "by_model": rows(by_model),
        "flags": dict(sorted(flags.items(), key=lambda kv: kv[1], reverse=True)),
        "generated_at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "ledger": store,
    }


_PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>AI Token Tracker — Live</title>
<style>
:root{color-scheme:light dark;--bg:#0b0e14;--card:#151a23;--line:#232b38;--fg:#e6edf3;--dim:#8b98a9;--accent:#3fb950;--flash:rgba(63,185,80,.25)}
@media (prefers-color-scheme:light){:root{--bg:#f5f7fa;--card:#fff;--line:#e2e8f0;--fg:#0b0e14;--dim:#5b6472;--flash:rgba(63,185,80,.18)}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 -apple-system,Segoe UI,Roboto,sans-serif}
header{padding:20px 28px;border-bottom:1px solid var(--line);display:flex;align-items:baseline;gap:18px;flex-wrap:wrap}
h1{font-size:16px;margin:0;font-weight:600}#live{font-size:12px;color:var(--accent);display:flex;align-items:center;gap:6px}
#dot{width:8px;height:8px;border-radius:50%;background:var(--accent);box-shadow:0 0 0 0 var(--accent);animation:pulse 2s infinite}
@keyframes pulse{0%{box-shadow:0 0 0 0 rgba(63,185,80,.6)}70%{box-shadow:0 0 0 8px rgba(63,185,80,0)}100%{box-shadow:0 0 0 0 rgba(63,185,80,0)}}
.stale #dot{background:#d29922;animation:none}.stale #live{color:#d29922}
main{padding:24px 28px;max-width:1200px;margin:auto}
.big{display:flex;gap:40px;flex-wrap:wrap;margin-bottom:8px}
.kpi{display:flex;flex-direction:column}.kpi .n{font-size:40px;font-weight:700;font-variant-numeric:tabular-nums;transition:color .25s}
.kpi .l{font-size:12px;color:var(--dim);text-transform:uppercase;letter-spacing:.05em}
.toast{position:fixed;right:20px;bottom:20px;background:var(--accent);color:#06210d;padding:10px 16px;border-radius:8px;font-weight:600;opacity:0;transform:translateY(8px);transition:.3s}
.toast.show{opacity:1;transform:none}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:18px;margin-top:22px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;overflow:hidden}
.card h2{font-size:12px;text-transform:uppercase;letter-spacing:.05em;color:var(--dim);margin:0;padding:12px 16px;border-bottom:1px solid var(--line)}
table{width:100%;border-collapse:collapse}td{padding:8px 16px;border-bottom:1px solid var(--line);font-variant-numeric:tabular-nums}
tr:last-child td{border-bottom:none}td.n{text-align:right;color:var(--dim)}td.t{text-align:right;font-weight:600}
tr.flash td{background:var(--flash)}.muted{color:var(--dim);font-size:12px;padding:10px 28px}
.flag{color:#d29922}
</style></head><body>
<header><h1>AI Token Tracker — Live</h1><span id="live"><span id="dot"></span><span id="livetxt">connecting…</span></span></header>
<main>
<div class="big">
<div class="kpi"><span class="n" id="total">—</span><span class="l">contributing tokens</span></div>
<div class="kpi"><span class="n" id="events">—</span><span class="l">events</span></div>
<div class="kpi"><span class="n" id="services">—</span><span class="l">services</span></div>
</div>
<div id="flags" class="muted"></div>
<div class="grid">
<div class="card"><h2>By service</h2><table id="by_service"></table></div>
<div class="card"><h2>By provider</h2><table id="by_provider"></table></div>
<div class="card"><h2>By model</h2><table id="by_model"></table></div>
</div>
</main>
<div class="toast" id="toast"></div>
<script>
const fmt=n=>n.toLocaleString();let prev={total:0,rows:{}};let first=true;
function tbl(id,rows){const t=document.getElementById(id);const pr=prev.rows[id]||{};t.innerHTML='';
 for(const r of rows){const tr=document.createElement('tr');const was=pr[r.name];
  if(!first&&was!==undefined&&was!==r.tokens)tr.className='flash';
  tr.innerHTML=`<td>${r.name}</td><td class="n">${fmt(r.events)}</td><td class="t">${fmt(r.tokens)}</td>`;t.appendChild(tr);}
 prev.rows[id]={};for(const r of rows)prev.rows[id][r.name]=r.tokens;}
function toast(msg){const el=document.getElementById('toast');el.textContent=msg;el.classList.add('show');setTimeout(()=>el.classList.remove('show'),2200);}
async function tick(){try{
 const d=await(await fetch('/data',{cache:'no-store'})).json();
 document.body.classList.remove('stale');document.getElementById('livetxt').textContent='live · '+d.generated_at.slice(11,19)+'Z';
 document.getElementById('total').textContent=fmt(d.total_tokens);
 document.getElementById('events').textContent=fmt(d.events);
 document.getElementById('services').textContent=fmt(d.by_service.length);
 if(!first&&d.total_tokens>prev.total){const el=document.getElementById('total');el.style.color='var(--accent)';setTimeout(()=>el.style.color='',400);toast('+'+fmt(d.total_tokens-prev.total)+' tokens');}
 tbl('by_service',d.by_service);tbl('by_provider',d.by_provider);tbl('by_model',d.by_model);
 const f=Object.entries(d.flags||{});document.getElementById('flags').innerHTML=f.length?('flags: '+f.map(([k,v])=>`<span class="flag">${k}</span> ${v}`).join(' · ')):'no data-quality flags';
 prev.total=d.total_tokens;first=false;
}catch(e){document.body.classList.add('stale');document.getElementById('livetxt').textContent='disconnected';}}
tick();setInterval(tick,2000);
</script></body></html>"""


def make_handler(store: str):
    cache: dict[str, Any] = {"sig": None, "payload": b"{}"}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args: Any) -> None:
            pass

        def _send(self, code: int, body: bytes, ctype: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802
            if self.path == "/" or self.path.startswith("/index"):
                self._send(200, _PAGE.encode("utf-8"), "text/html; charset=utf-8")
                return
            if self.path.startswith("/data"):
                sig = _signature(store)
                if sig != cache["sig"]:
                    cache["payload"] = json.dumps(aggregate(store)).encode("utf-8")
                    cache["sig"] = sig
                self._send(200, cache["payload"], "application/json")
                return
            self._send(404, b'{"error":"not_found"}', "application/json")

    return Handler


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--store", default=os.environ.get("TRACKER_STORE", r"C:\ai-token-tracker-data\collector_events.jsonl"))
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8790)
    args = parser.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), make_handler(args.store))
    print(f"live dashboard on http://{args.host}:{args.port}  (ledger: {args.store})")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
