"""Local bridge between the browser extension, the web UI, and the pipeline.

Run:  python -m scrollwright serve        # headless bridge (extension + API)
       python -m scrollwright serve --ui  # same + open the web UI in a browser
       python -m scrollwright ui         # start server if needed, open UI, watch it

Endpoints (127.0.0.1 only):
    GET  /            → web UI (HTML)
    GET  /status      → {"running": true, "vault": "...", "stats": {...}, "busy": bool, ...}
    POST /add         → {"text": "<any text containing reel URLs>"} — dedupes + queues
    GET  /vault       → {"path": "..."}
    POST /vault       → {"path": "<new vault folder>"} — validates, updates .env.
    GET  /api/ledgers → {"ledgers": [...], "stats": {...}}
    POST /api/retry   → {"requeued": n}
    POST /api/reprocess → {"requeued": n}  (body: {"filter": "..."} optional)
    POST /api/process → {"claimed": n}    (kick the worker now)

No dependencies beyond the stdlib. Binds localhost only.
"""

import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

from . import core, pipeline

HOST = "127.0.0.1"
PORT = int(os.environ.get("SCROLLWRIGHT_PORT", "8765"))
MAX_BODY = 2 * 1024 * 1024  # 2MB of pasted text is plenty

# ---------------------------------------------------------------------------
# Minimal web UI (inline, zero extra dependencies). Served at GET /.
# Shares the dark Obsidian-ish look of the extension popup.
# "Open vault" and "open this page" use the OS file/URL handler so the browser
# does the right thing on Windows/macOS/Linux.
# ---------------------------------------------------------------------------

UI_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>ScrollWright</title>
<style>
  :root { color-scheme: dark; }
  * { box-sizing: border-box; }
  body {
    margin: 0; padding: 18px; font-family: system-ui, -apple-system, Segoe UI, Roboto, sans-serif;
    font-size: 13px; color: #e5e7eb; background: #0b1120; line-height: 1.5;
  }
  h1 { font-size: 16px; margin: 0 0 4px; display: flex; align-items: center; gap: 8px; }
  h1 .dot { width: 9px; height: 9px; border-radius: 50%; background: #6b7280; display: inline-block; }
  h1 .dot.on { background: #22c55e; box-shadow: 0 0 8px #22c55e; }
  h2 { font-size: 12px; margin: 18px 0 8px; color: #9ca3af; text-transform: uppercase; letter-spacing: .04em; }
  .card {
    background: #111827; border: 1px solid #1f2937; border-radius: 10px;
    padding: 12px 14px; margin-bottom: 14px;
  }
  .row { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin-bottom: 8px; }
  .row label { font-size: 11px; color: #9ca3af; min-width: 78px; }
  input[type=text], input[type=password] {
    flex: 1; min-width: 160px; padding: 6px 9px; border-radius: 6px;
    border: 1px solid #374151; background: #1f2937; color: #e5e7eb; font-size: 12px;
  }
  input[type=text]:focus { outline: none; border-color: #8b5cf6; }
  button {
    padding: 6px 10px; border: none; border-radius: 6px; font-weight: 600; font-size: 12px;
    cursor: pointer; background: #8b5cf6; color: #fff;
  }
  button:hover { filter: brightness(1.08); }
  button:disabled { opacity: .4; cursor: not-allowed; }
  button.secondary { background: #374151; }
  button.danger { background: #b91c1c; }
  button.small { padding: 3px 7px; font-size: 11px; }
  .muted { color: #6b7280; font-size: 11px; }
  .err { color: #f87171; }
  .ok { color: #34d399; }
  .stats { display: flex; gap: 14px; flex-wrap: wrap; }
  .stat {
    background: #1f2937; border-radius: 8px; padding: 8px 12px; min-width: 74px;
    text-align: center;
  }
  .stat .n { font-size: 20px; font-weight: 700; display: block; }
  .stat .l { font-size: 10px; color: #9ca3af; text-transform: uppercase; letter-spacing: .04em; }
  .stat.pending .n { color: #facc15; }
  .stat.done .n { color: #34d399; }
  .stat.failed .n { color: #f87171; }
  .stat.processing .n { color: #60a5fa; }
  table { width: 100%; border-collapse: collapse; font-size: 12px; }
  th, td { text-align: left; padding: 6px 8px; border-bottom: 1px solid #1f2937; }
  th { color: #9ca3af; font-weight: 600; font-size: 11px; text-transform: uppercase; letter-spacing: .03em; }
  tr:hover { background: #111827; }
  .badge {
    display: inline-block; padding: 1px 7px; border-radius: 10px; font-size: 10px;
    font-weight: 600; text-transform: uppercase; letter-spacing: .03em;
  }
  .badge.pending { background: #fde68a; color: #713f12; }
  .badge.processing { background: #93c5fd; color: #1e3a5f; }
  .badge.done { background: #6ee7b7; color: #064e3b; }
  .badge.failed { background: #fca5a5; color: #7f1d1d; }
  .log {
    background: #0b1220; border: 1px solid #1f2937; border-radius: 6px;
    padding: 8px; min-height: 64px; max-height: 180px; overflow-y: auto;
    font-size: 11.5px; line-height: 1.5; color: #9ca3af; white-space: pre-wrap; word-break: break-word;
  }
  .log .ok { color: #34d399; }
  .log .err { color: #f87171; }
  .log .muted { color: #6b7280; }
  .actions { display: flex; gap: 6px; flex-wrap: wrap; align-items: center; }
  .gap-8 { gap: 8px; }
  #refresh { margin-left: auto; }
  .empty { color: #6b7280; font-style: italic; padding: 10px 0; }
  kbd {
    background: #1f2937; border: 1px solid #374151; border-radius: 4px;
    padding: 1px 5px; font-size: 11px; font-family: inherit; color: #d1d5db;
  }
</style>
</head>
<body>
  <h1><span class="dot" id="dot"></span> ScrollWright <span class="muted" id="srv"></span></h1>
  <div class="card">
    <div class="row">
      <label for="vault">Notes folder</label>
      <input type="text" id="vault" placeholder="C:\\path\\to\\vault" spellcheck="false" />
      <button id="openVault" class="secondary small" title="Open this folder in Explorer/Finder">Open</button>
    </div>
    <div class="muted" style="margin-top:-4px">Notes land in &lt;vault&gt;/ScrollWright/ . Change it here (Enter to save).</div>
  </div>

  <div class="card">
    <h2>Ledger</h2>
    <div class="stats" id="stats"></div>
    <div class="row" style="margin-top:10px">
      <input type="text" id="filter" placeholder="Filter shorts/urls..." style="flex:2" />
      <button id="filterBtn" class="secondary small">Filter</button>
      <button id="clearFilter" class="secondary small">Clear</button>
      <span class="muted" id="count"></span>
    </div>
    <table>
      <thead><tr><th>Reel</th><th>Status</th><th>Added</th><th>Note</th><th>Actions</th></tr></thead>
      <tbody id="rows"></tbody>
    </table>
    <div class="empty" id="empty" style="display:none">No reels yet. Paste some URLs below and add them.</div>
  </div>

  <div class="card">
    <h2>Add reels</h2>
    <textarea id="addText" rows="3" style="width:100%;resize:vertical;padding:7px 9px;border-radius:6px;border:1px solid #374151;background:#1f2937;color:#e5e7eb;font-size:12px;font-family:inherit" placeholder="Paste instagram reel/post URLs (one or many, any mess OK)&#10;e.g. https://www.instagram.com/reel/ABC123/"></textarea>
    <div class="row" style="margin-top:6px">
      <button id="addBtn">Add + queue</button>
      <button id="addClipboard" class="secondary small">From clipboard</button>
      <button id="processNow" class="secondary small">Process pending now</button>
      <span class="muted" id="addResult"></span>
    </div>
  </div>

  <div class="card">
    <h2>Batch ops</h2>
    <div class="row">
      <button id="retryBtn">Requeue failed</button>
      <button id="reprocessAllBtn" class="secondary">Reprocess all done</button>
      <button id="reprocessFilterBtn" class="secondary small">Reprocess filtered</button>
    </div>
  </div>

  <div class="card">
    <div class="row">
      <button id="pollToggle">Auto-refresh: on</button>
      <button id="refresh" class="secondary small">Refresh now</button>
    </div>
    <div class="log" id="log"></div>
  </div>

<script>
(function () {
  "use strict";
  var $ = function (id) { return document.getElementById(id); };
  var logEl = $("log");
  var POLL_MS = 2500;

  function log(msg, cls) {
    var line = document.createElement("div");
    if (cls) line.className = cls;
    line.textContent = msg;
    logEl.appendChild(line);
    logEl.scrollTop = logEl.scrollHeight;
  }

  function api(path, opts) {
    opts = opts || {};
    var body = opts.body ? JSON.stringify(opts.body) : null;
    var headers = opts.headers || {};
    if (body) headers["Content-Type"] = "application/json";
    return fetch(path, { method: opts.method || "GET", headers: headers, body: body });
  }

  function json(resp) { return resp.json(); }

  function breadcrumb(url) {
    // "https://www.instagram.com/reel/CxYz/..." -> "reel / CxYz"
    var m = url && url.match(/\/reel\/([A-Za-z0-9_-]+)/i);
    if (m) return "reel / " + m[1];
    var m2 = url && url.match(/\/(reel|reels|p|tv)\/([A-Za-z0-9_-]+)/i);
    if (m2) return (m2[1] || "") + " / " + m2[2];
    return url || "?";
  }

  function badge(status) {
    var b = document.createElement("span");
    b.className = "badge " + (status || "pending");
    b.textContent = status || "pending";
    return b;
  }

  var pollTimer = null;
  var polling = true;

  function startPoll() {
    stopPoll();
    polling = true;
    $("pollToggle").textContent = "Auto-refresh: on";
    tick();
    pollTimer = setInterval(tick, POLL_MS);
  }
  function stopPoll() {
    if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
    polling = false;
    $("pollToggle").textContent = "Auto-refresh: off";
  }
  function togglePoll() {
    polling ? stopPoll() : startPoll();
  }

  function fillStats(s) {
    var el = $("stats");
    el.innerHTML = "";
    var order = [
      { k: "processing", l: "Working" },
      { k: "pending", l: "Pending" },
      { k: "done", l: "Done" },
      { k: "failed", l: "Failed" },
    ];
    var total = 0;
    order.forEach(function (o) {
      var n = (s && s[o.k]) || 0;
      total += n;
      var d = document.createElement("div");
      d.className = "stat " + o.k;
      d.innerHTML = "<span class='n'>" + n + "</span><span class='l'>" + o.l + "</span>";
      el.appendChild(d);
    });
    var t = document.createElement("div");
    t.className = "stat";
    t.style.background = "transparent";
    t.style.padding = "8px 12px";
    t.innerHTML = "<span class='n' style='color:#9ca3af;font-size:14px'>" + total + "</span><span class='l'>Total</span>";
    el.appendChild(t);
  }

  function renderLedgers(data) {
    var rows = data && data.ledgers || [];
    var filter = $("filter").value.trim().toLowerCase();
    var tbody = $("rows");
    tbody.innerHTML = "";
    if (!rows.length) {
      $("empty").style.display = "";
      $("count").textContent = "0 reels";
      return;
    }
    $("empty").style.display = "none";
    var shown = 0;
    rows.forEach(function (r) {
      if (filter) {
        var hay = (r.shortcode + " " + r.url).toLowerCase();
        if (hay.indexOf(filter) === -1) return;
      }
      shown++;
      var tr = document.createElement("tr");
      var td1 = document.createElement("td");
      var a = document.createElement("a");
      a.href = r.url;
      a.target = "_blank";
      a.textContent = r.shortcode;
      td1.appendChild(a);
      var td2 = document.createElement("td");
      td2.appendChild(badge(r.status));
      var td3 = document.createElement("td");
      td3.textContent = (r.added_at || "").slice(0, 16).replace("T", " ");
      var td4 = document.createElement("td");
      if (r.note_path) {
        var p = document.createElement("a");
        p.href = "file://" + r.note_path;
        p.textContent = r.note_path.split("/").pop();
        p.target = "_blank";
        td4.appendChild(p);
      } else {
        td4.textContent = "—";
      }
      var td5 = document.createElement("td");
      td5.className = "actions";
      if (r.status === "pending" || r.status === "processing") {
        // nothing actionable
      } else if (r.status === "done") {
        var rb = document.createElement("button");
        rb.className = "small secondary";
        rb.textContent = "Reprocess";
        rb.onclick = function () { reprocessOne(r.shortcode); };
        td5.appendChild(rb);
      } else if (r.status === "failed") {
        var ret = document.createElement("button");
        ret.className = "small";
        ret.textContent = "Retry";
        ret.onclick = function () { retryOne(r.shortcode); };
        td5.appendChild(ret);
      }
      tr.appendChild(td1); tr.appendChild(td2); tr.appendChild(td3); tr.appendChild(td4); tr.appendChild(td5);
      tbody.appendChild(tr);
    });
    $("count").textContent = shown + " / " + rows.length + " shown";
  }

  function fillVault(path) {
    $("vault").value = path || "";
  }

  function refreshStatus() {
    api("/status").then(function (r) {
      return r.json();
    }).then(function (s) {
      var on = s && s.running;
      $("dot").classList.toggle("on", !!on);
      $("srv").textContent = on ? "· running" : "· server offline";
      $("srv").className = "muted" + (on ? " ok" : " err");
      if (on && s.stats) fillStats(s.stats);
      if (on && s.busy) log("working on " + (s.current || "?") + "…", "muted");
    }).catch(function () {
      $("dot").classList.remove("on");
      $("srv").textContent = "· server offline";
      $("srv").className = "muted err";
    });
  }

  function refreshLedgers() {
    api("/api/ledgers").then(function (r) { return r.json(); })
      .then(function (d) { renderLedgers(d); })
      .catch(function () { log("ledger read failed", "err"); });
  }

  function tick() {
    refreshStatus();
    refreshLedgers();
  }

  function setAddResult(msg, cls) {
    $("addResult").textContent = msg;
    $("addResult").className = (cls || "");
  }

  function addText(text, src) {
    if (!text || !text.trim()) { setAddResult("nothing to add", "err"); return; }
    api("/add", { method: "POST", body: { text: text } }).then(function (r) { return r.json(); })
      .then(function (d) {
        if (d && d.added !== undefined) {
          setAddResult("added " + d.added + ", " + d.duplicates + " dup" + (d.duplicates === 1 ? "" : "s") + " — processing in background", "ok");
          log(src + ": queued " + d.added + " new", "ok");
          setTimeout(refreshLedgers, 400);
        } else {
          setAddResult((d && d.error) || "add failed", "err");
          log((d && d.error) || "add failed", "err");
        }
      }).catch(function () { setAddResult("server unreachable", "err"); });
  }

  function retryOne(sc) {
    api("/api/retry", { method: "POST", body: { shortcode: sc } }).then(function (r) { return r.json(); })
      .then(function (d) { log("requeued " + sc, "ok"); setTimeout(refreshLedgers, 300); })
      .catch(function () { log("retry failed", "err"); });
  }

  function reprocessOne(sc) {
    api("/api/reprocess", { method: "POST", body: { filter: sc } }).then(function (r) { return r.json(); })
      .then(function (d) { log("reprocessed " + sc, "ok"); setTimeout(refreshLedgers, 300); })
      .catch(function () { log("reprocess failed", "err"); });
  }

  function setVault(path) {
    api("/vault", { method: "POST", body: { path: path } }).then(function (r) { return r.json(); })
      .then(function (d) {
        if (d && d.path) {
          $("vault").value = d.path;
          log("vault → " + d.path, "ok");
        } else {
          log((d && d.error) || "could not set vault", "err");
        }
      }).catch(function () { log("server unreachable", "err"); });
  }

  // ---- wire up ----
  startPoll();

  $("vault").addEventListener("change", function () { setVault($("vault").value); });
  $("vault").addEventListener("keydown", function (e) { if (e.key === "Enter") { e.preventDefault(); setVault($("vault").value); } });

  $("openVault").addEventListener("click", function () {
    var p = $("vault").value.trim();
    if (!p) { log("no vault set", "err"); return; }
    window.location.href = "file://" + p;
  });

  $("addBtn").addEventListener("click", function () {
    addText($("addText").value, "added");
  });
  $("addClipboard").addEventListener("click", function () {
    addText((navigator.clipboard && navigator.clipboard.readText) ? navigator.clipboard.readText() : "", "clipboard");
  });
  $("addText").addEventListener("keydown", function (e) {
    if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); addText($("addText").value, "added"); }
  });

  $("processNow").addEventListener("click", function () {
    api("/api/process", { method: "POST" }).then(function (r) { return r.json(); })
      .then(function (d) { log("processing kicked off", "ok"); setTimeout(refreshLedgers, 300); })
      .catch(function () { log("could not kick processing", "err"); });
  });

  $("retryBtn").addEventListener("click", function () {
    api("/api/retry", { method: "POST" }).then(function (r) { return r.json(); })
      .then(function (d) { log("requeued " + d.requeued + " failed", "ok"); setTimeout(refreshLedgers, 300); })
      .catch(function () { log("retry failed", "err"); });
  });

  $("reprocessAllBtn").addEventListener("click", function () {
    api("/api/reprocess", { method: "POST", body: {} }).then(function (r) { return r.json(); })
      .then(function (d) { log("reprocessed " + d.requeued + " done reels", "ok"); setTimeout(refreshLedgers, 300); })
      .catch(function () { log("reprocess failed", "err"); });
  });

  $("reprocessFilterBtn").addEventListener("click", function () {
    var f = $("filter").value.trim();
    if (!f) { log("enter a filter first", "err"); return; }
    api("/api/reprocess", { method: "POST", body: { filter: f } }).then(function (r) { return r.json(); })
      .then(function (d) { log("reprocessed " + d.requeued + " (filter: " + f + ")", "ok"); setTimeout(refreshLedgers, 300); })
      .catch(function () { log("reprocess failed", "err"); });
  });

  $("filterBtn").addEventListener("click", refreshLedgers);
  $("clearFilter").addEventListener("click", function () { $("filter").value = ""; refreshLedgers(); });
  $("refresh").addEventListener("click", tick);
  $("pollToggle").addEventListener("click", togglePoll);

  window.addEventListener("beforeunload", function () { stopPoll(); });
})();
</script>
</body>
</html>
"""

# When the server was started by `ui` (watchdog mode), the watcher exposes its
# pid so the health-check / restart path can find it.
_UI_WATCHDOG_PID_FILE = Path(os.environ.get("SCROLLWRIGHT_WATCHDOG_PID", "")) if os.environ.get("SCROLLWRIGHT_WATCHDOG_PID") else None

_state = {
    "busy": False,
    "current": None,  # shortcode being processed, if any
}
_wake = threading.Event()  # nudges the worker when new URLs arrive
_cfg_lock = threading.Lock()  # config reloads + .env writes


# ---------------------------------------------------------------------------
# .env editing (for the vault-path field in the popup)
# ---------------------------------------------------------------------------

def _env_file() -> Path:
    for p in (core.PROJECT_ROOT.parent / ".env", core.PROJECT_ROOT / ".env"):
        if p.exists():
            return p
    return core.PROJECT_ROOT.parent / ".env"  # default location to create


def _update_env(key: str, value: str) -> None:
    """Set KEY=value in .env, preserving comments and other lines."""
    path = _env_file()
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    hit = False
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith(f"{key}=") or stripped.startswith(f"{key} ="):
            lines[i] = f"{key}={value}"
            hit = True
            break
    if not hit:
        lines.append(f"{key}={value}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.environ[key] = value  # live update for the running process


# ---------------------------------------------------------------------------
# Background worker: processes pending reels as they arrive
# ---------------------------------------------------------------------------

def _worker() -> None:
    while True:
        _wake.wait(timeout=2.0)
        _wake.clear()
        worked = False
        while True:
            rows = core.claim_batch(1)
            if not rows:
                break
            worked = True
            row = rows[0]
            _state["busy"] = True
            _state["current"] = row["shortcode"]
            try:
                pipeline.process_one(row)
            except pipeline.QuotaExhausted:
                # Daily Gemini limit: put it back and stop until tomorrow.
                import sqlite3

                with sqlite3.connect(core.DB_PATH) as con:
                    con.execute(
                        "UPDATE reels SET status='pending' WHERE shortcode=?",
                        (row["shortcode"],),
                    )
                _state["busy"] = False
                _state["current"] = None
                print("[serve] Gemini quota exhausted — paused until tomorrow.", flush=True)
                _wake.clear()
                time.sleep(3600)  # retry in an hour, silently
            except Exception as exc:
                core.mark_failed(row["shortcode"], f"{type(exc).__name__}: {exc}")
                print(f"[serve] reel failed: {type(exc).__name__}: {str(exc)[:120]}", flush=True)
            finally:
                _state["busy"] = False
                _state["current"] = None
        if not worked:
            time.sleep(1.0)


# ---------------------------------------------------------------------------
# HTTP handlers
# ---------------------------------------------------------------------------

def _cors() -> dict[str, str]:
    return {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type",
        # Chrome/Edge Private Network Access preflight (public site → localhost)
        "Access-Control-Allow-Private-Network": "true",
    }


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):  # quieter: one line, not per-request spam
        pass

    def _send(self, code: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        for k, v in _cors().items():
            self.send_header(k, v)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self) -> None:
        body = UI_HTML.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def _send_ledgers(self) -> None:
        import sqlite3
        try:
            with core._connect() as con:
                rows = con.execute(
                    "SELECT shortcode, url, status, note_path, error, added_at FROM reels ORDER BY added_at DESC LIMIT 200"
                ).fetchall()
            ledgers = []
            for r in rows:
                ledgers.append({
                    "shortcode": r["shortcode"],
                    "url": r["url"],
                    "status": r["status"],
                    "note_path": r["note_path"],
                    "error": r["error"],
                    "added_at": r["added_at"],
                })
            self._send(200, {"ledgers": ledgers, "stats": core.stats()})
        except Exception as exc:
            self._send(500, {"error": f"ledger read failed: {exc}"})

    def do_OPTIONS(self):  # CORS preflight for the extension popup
        self._send(204, {})

    def do_GET(self):
        route = urlparse(self.path).path
        if route == "/":
            self._send_html()
        elif route == "/status":
            self._send(200, {"running": True, "vault": str(core.VAULT_NOTES_DIR), "busy": _state["busy"], "current": _state["current"], "stats": core.stats()})
        elif route == "/vault":
            self._send(200, {"path": str(core.VAULT_NOTES_DIR)})
        elif route == "/api/ledgers":
            self._send_ledgers()
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        route = urlparse(self.path).path
        try:
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_BODY:
                self._send(413, {"error": "body too large"})
                return
            raw = self.rfile.read(length) if length else b"{}"
            data = json.loads(raw.decode("utf-8", "ignore") or "{}")
        except Exception:
            self._send(400, {"error": "invalid JSON body"})
            return

        if route == "/add":
            text = str(data.get("text", ""))
            urls = core.extract_reel_urls(text)
            if not urls:
                self._send(400, {"error": "no instagram reel/post URLs found"})
                return
            new, dup = core.add_urls(urls)
            if new:
                _wake.set()
            self._send(200, {"added": new, "duplicates": dup, "stats": core.stats()})

        elif route == "/vault":
            raw_path = str(data.get("path", "")).strip().strip('"')
            if not raw_path:
                self._send(400, {"error": "path is required"})
                return
            vault = Path(unquote(raw_path)).expanduser()
            if not vault.is_dir():
                self._send(400, {"error": f"folder not found: {vault}"})
                return
            with _cfg_lock:
                _update_env("VAULT_PATH", str(vault))
                core.load_config()  # re-resolve + reseed dashboard in the new vault
            self._send(200, {"path": str(core.VAULT_NOTES_DIR)})

        elif route == "/api/ledgers":
            self._send_ledgers()

        elif route == "/api/retry":
            import sqlite3
            with sqlite3.connect(core.DB_PATH) as con:
                n = con.execute(
                    "UPDATE reels SET status='pending', error=NULL WHERE status='failed'"
                ).rowcount
            self._send(200, {"requeued": n, "stats": core.stats()})

        elif route == "/api/reprocess":
            import sqlite3
            filt = str(data.get("filter", "")).strip()
            with sqlite3.connect(core.DB_PATH) as con:
                if filt:
                    n = con.execute(
                        "UPDATE reels SET status='pending', note_path=NULL WHERE status='done' AND (shortcode LIKE ? OR url LIKE ?)",
                        (f"%{filt}%", f"%{filt}%"),
                    ).rowcount
                else:
                    n = con.execute(
                        "UPDATE reels SET status='pending', note_path=NULL WHERE status='done'"
                    ).rowcount
            self._send(200, {"requeued": n, "stats": core.stats()})

        elif route == "/api/process":
            # Wake the worker immediately so it picks up any pending rows now.
            _wake.set()
            self._send(200, {"claimed": True, "stats": core.stats()})

        else:
            self._send(404, {"error": "not found"})


def main(open_ui: bool = False) -> None:
    core.load_config()
    # A previous run (CLI or serve) may have died mid-reel — requeue its rows,
    # otherwise they'd sit in 'processing' forever.
    import sqlite3

    with sqlite3.connect(core.DB_PATH) as con:
        requeued = con.execute(
            "UPDATE reels SET status='pending' WHERE status='processing'"
        ).rowcount
    threading.Thread(target=_worker, daemon=True, name="scrollwright-worker").start()
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"ScrollWright server → http://{HOST}:{PORT}", flush=True)
    print(f"  notes landing in: {core.VAULT_NOTES_DIR}", flush=True)
    if requeued:
        print(f"  requeued {requeued} reel(s) from an interrupted run", flush=True)
    if open_ui:
        import webbrowser
        import time as _time

        def _open():
            for _ in range(30):
                try:
                    import socket
                    s = socket.create_connection((HOST, PORT), timeout=1.0)
                    s.close()
                    webbrowser.open(f"http://{HOST}:{PORT}", new=0, autoraise=True)
                    return
                except OSError:
                    pass
                _time.sleep(0.1)
            print(f"[serve] UI didn't come up in time — open http://{HOST}:{PORT} manually", flush=True)

        threading.Thread(target=_open, daemon=True).start()
        print("  web UI: http://127.0.0.1:8765", flush=True)
    print("  waiting for reels from the extension… (Ctrl+C to stop)", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped. Any reel mid-flight gets requeued on next start.")


if __name__ == "__main__":
    main()
