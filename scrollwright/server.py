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
    POST /api/process → {"claimed": true}  (wake the worker now)
    GET  /projectRoot → {"path": "..."}   (project folder, where the .bat files are)

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
  :root { color-scheme: light; }
  * { box-sizing: border-box; }
  body {
    margin: 0; font-family: system-ui, -apple-system, Segoe UI, Roboto, sans-serif;
    font-size: 13px; color: #1f2937; background: #eef1f6; line-height: 1.5;
  }
  body.dark { color-scheme: dark; color: #e5e7eb; background: #070b16; }

  /* ---------------------------------------------------------------- header */
  .top {
    display: flex; align-items: center; gap: 14px; flex-wrap: wrap;
    padding: 14px 20px; background: #ffffff; border-bottom: 1px solid #e5e7eb;
    position: sticky; top: 0; z-index: 20;
  }
  body.dark .top { background: #0b1120; border-bottom-color: #1f2937; }
  .brand { display: flex; align-items: center; gap: 11px; }
  .mark {
    width: 34px; height: 34px; border-radius: 9px; flex: none;
    background: linear-gradient(135deg, #6366f1, #8b5cf6);
    display: grid; place-items: center; color: #fff; font-size: 17px;
    box-shadow: 0 2px 8px rgba(99,102,241,.35);
  }
  .brand h1 { font-size: 15px; margin: 0; letter-spacing: -.01em; display: flex; align-items: center; gap: 8px; }
  .brand .sub { font-size: 11.5px; color: #6b7280; margin: 1px 0 0; }
  body.dark .brand .sub { color: #9ca3af; }
  .dot { width: 8px; height: 8px; border-radius: 50%; background: #9ca3af; display: inline-block; }
  body.dark .dot { background: #6b7280; }
  .dot.on { background: #22c55e; box-shadow: 0 0 8px #22c55e; }
  .top .spacer { flex: 1; }
  .top .vaultpill {
    display: flex; align-items: center; gap: 6px; max-width: 46vw;
    background: #f9fafb; border: 1px solid #e5e7eb; border-radius: 20px;
    padding: 4px 6px 4px 11px; font-size: 11.5px; color: #6b7280;
  }
  body.dark .vaultpill { background: #111827; border-color: #1f2937; color: #9ca3af; }
  .vaultpill b { color: #111827; font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  body.dark .vaultpill b { color: #e5e7eb; }
  .vaultpill button { padding: 2px 8px; font-size: 11px; }

  /* ---------------------------------------------------------------- layout */
  .wrap { padding: 16px 20px 28px; }
  .grid { display: grid; gap: 16px; grid-template-columns: 300px minmax(0,1fr) 320px; align-items: start; }
  @media (max-width: 1180px) { .grid { grid-template-columns: 290px minmax(0,1fr); } .colLog { grid-column: 1 / -1; } }
  @media (max-width: 820px)  { .grid { grid-template-columns: minmax(0,1fr); } }

  .card {
    background: #ffffff; border: 1px solid #e5e7eb; border-radius: 12px;
    padding: 13px 14px; margin-bottom: 16px; box-shadow: 0 1px 2px rgba(0,0,0,.04);
  }
  body.dark .card { background: #0e1526; border-color: #1f2937; box-shadow: none; }
  .card > h2 {
    font-size: 10.5px; margin: 0 0 10px; color: #6b7280; text-transform: uppercase;
    letter-spacing: .06em; font-weight: 700; display: flex; align-items: center; gap: 6px;
  }
  body.dark .card > h2 { color: #9ca3af; }
  .card > h2 .hintk { margin-left: auto; font-weight: 500; text-transform: none; letter-spacing: 0; font-size: 10.5px; color: #9ca3af; }

  /* ---------------------------------------------------------------- controls */
  textarea, input[type=text] {
    width: 100%; padding: 7px 10px; border-radius: 7px; border: 1px solid #d1d5db;
    background: #ffffff; color: #1f2937; font-size: 12.5px; font-family: inherit;
  }
  body.dark textarea, body.dark input[type=text] { background: #111827; color: #e5e7eb; border-color: #293548; }
  textarea { resize: vertical; min-height: 62px; }
  textarea:focus, input[type=text]:focus { outline: none; border-color: #6366f1; box-shadow: 0 0 0 3px rgba(99,102,241,.14); }
  body.dark textarea:focus, body.dark input[type=text]:focus { box-shadow: 0 0 0 3px rgba(139,92,246,.28); }

  button {
    padding: 7px 11px; border: none; border-radius: 7px; font-weight: 600; font-size: 12px;
    cursor: pointer; background: #6366f1; color: #fff; font-family: inherit; transition: filter .1s, transform .05s;
  }
  body.dark button { background: #7c5cf5; }
  button:hover { filter: brightness(1.07); }
  button:active { transform: translateY(1px); }
  button:disabled { opacity: .45; cursor: not-allowed; }
  button.secondary { background: #f3f4f6; color: #374151; border: 1px solid #e5e7eb; }
  body.dark button.secondary { background: #172033; color: #d1d5db; border-color: #293548; }
  button.danger { background: #fee2e2; color: #b91c1c; border: 1px solid #fecaca; }
  body.dark button.danger { background: #3b1518; color: #fca5a5; border-color: #7f1d1d; }
  button.block { width: 100%; }
  button.small { padding: 4px 9px; font-size: 11.5px; border-radius: 6px; }

  .btnrow { display: flex; gap: 6px; flex-wrap: wrap; margin-top: 8px; align-items: center; }
  .stack { display: grid; gap: 6px; }
  .kbdhint { font-size: 10.5px; color: #9ca3af; margin-top: 6px; }
  kbd {
    background: #f3f4f6; border: 1px solid #d1d5db; border-bottom-width: 2px; border-radius: 4px;
    padding: 0 4px; font-size: 10px; font-family: inherit; color: #374151;
  }
  body.dark kbd { background: #172033; border-color: #293548; color: #d1d5db; }

  /* ---------------------------------------------------------------- stats */
  .stats { display: grid; grid-template-columns: repeat(4, 1fr); gap: 8px; }
  .stat { background: #f9fafb; border: 1px solid #e5e7eb; border-radius: 9px; padding: 8px 6px; text-align: center; }
  body.dark .stat { background: #111827; border-color: #1f2937; }
  .stat .n { font-size: 19px; font-weight: 700; display: block; line-height: 1.15; color: #111827; }
  body.dark .stat .n { color: #f3f4f6; }
  .stat .l { font-size: 9.5px; color: #6b7280; text-transform: uppercase; letter-spacing: .05em; }
  body.dark .stat .l { color: #9ca3af; }
  .stat.pending   .n { color: #b45309; }
  .stat.done      .n { color: #15803d; }
  .stat.failed    .n { color: #b91c1c; }
  .stat.processing .n { color: #1d4ed8; }
  body.dark .stat.pending   .n { color: #fbbf24; }
  body.dark .stat.done      .n { color: #4ade80; }
  body.dark .stat.failed    .n { color: #f87171; }
  body.dark .stat.processing .n { color: #60a5fa; }

  /* ---------------------------------------------------------------- table */
  .listhead { display: flex; gap: 8px; align-items: center; margin-bottom: 10px; flex-wrap: wrap; }
  .listhead input[type=text] { flex: 1; min-width: 130px; width: auto; }
  .count { font-size: 11px; color: #9ca3af; white-space: nowrap; }

  .tablewrap { overflow-x: auto; }
  table { width: 100%; border-collapse: collapse; font-size: 12.5px; }
  th, td { text-align: left; padding: 8px; border-bottom: 1px solid #f3f4f6; vertical-align: middle; }
  body.dark th, body.dark td { border-bottom-color: #1a2338; }
  th { color: #6b7280; font-weight: 600; font-size: 10px; text-transform: uppercase; letter-spacing: .05em; position: sticky; top: 0; background: #fff; }
  body.dark th { color: #9ca3af; background: #0e1526; }
  tbody tr:hover { background: #f9fafb; }
  body.dark tbody tr:hover { background: #111827; }
  td a { color: #4f46e5; text-decoration: none; font-weight: 600; font-family: ui-monospace, Consolas, monospace; font-size: 12px; }
  body.dark td a { color: #a5b4fc; }
  td a:hover { text-decoration: underline; }
  td .notelink { font-family: inherit; font-weight: 500; font-size: 11.5px; max-width: 190px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; display: inline-block; vertical-align: bottom; }
  td.when { color: #9ca3af; font-size: 11.5px; white-space: nowrap; }
  td.actions { white-space: nowrap; }

  .badge { display: inline-block; padding: 2px 8px; border-radius: 20px; font-size: 10px; font-weight: 700; text-transform: uppercase; letter-spacing: .04em; }
  .badge.pending   { background: #fef3c7; color: #92400e; }
  .badge.processing { background: #dbeafe; color: #1e40af; }
  .badge.done      { background: #d1fae5; color: #065f46; }
  .badge.failed    { background: #fee2e2; color: #991b1b; }
  body.dark .badge.pending   { background: #422006; color: #fcd34d; }
  body.dark .badge.processing { background: #172554; color: #93c5fd; }
  body.dark .badge.done      { background: #052e1b; color: #6ee7b7; }
  body.dark .badge.failed    { background: #450a0a; color: #fca5a5; }
  .badge.processing { animation: pulse 1.4s ease-in-out infinite; }
  @keyframes pulse { 50% { opacity: .5; } }

  .empty { color: #9ca3af; font-style: italic; padding: 22px 0; text-align: center; font-size: 12.5px; }
  body.dark .empty { color: #6b7280; }

  /* ---------------------------------------------------------------- log */
  .logbar { display: flex; align-items: center; gap: 6px; margin-bottom: 8px; }
  .logbar .grow { flex: 1; }
  .log {
    background: #0b1220; border: 1px solid #1f2937; border-radius: 8px; padding: 9px 10px;
    min-height: 120px; height: calc(100vh - 300px); max-height: 620px; overflow-y: auto;
    font-size: 11.5px; line-height: 1.6; color: #9ca3af; white-space: pre-wrap; word-break: break-word;
    font-family: ui-monospace, Consolas, "SF Mono", monospace;
  }
  @media (max-width: 820px) { .log { height: 220px; } }
  .log .ok { color: #4ade80; }
  .log .err { color: #f87171; }
  .log .muted { color: #6b7280; }

  /* ---------------------------------------------------------------- misc */
  .theme-toggle {
    border: 1px solid #d1d5db; background: #fff; color: #1f2937; border-radius: 20px;
    padding: 5px 11px; font-size: 11.5px; font-weight: 600; cursor: pointer; white-space: nowrap;
  }
  body.dark .theme-toggle { border-color: #293548; background: #111827; color: #e5e7eb; }
  .muted { color: #6b7280; font-size: 11.5px; }
  body.dark .muted { color: #9ca3af; }
  .err { color: #dc2626; } body.dark .err { color: #f87171; }
  .ok  { color: #16a34a; } body.dark .ok  { color: #4ade80; }

  .hint {
    background: #eef2ff; border: 1px solid #e0e7ff; border-left: 3px solid #6366f1;
    border-radius: 9px; padding: 9px 12px; font-size: 12px; color: #3730a3; margin-bottom: 16px;
  }
  body.dark .hint { background: #171538; border-color: #2b2560; border-left-color: #8b5cf6; color: #c7d2fe; }
  .hint .title { font-weight: 700; text-transform: uppercase; font-size: 10px; letter-spacing: .05em; color: #4338ca; cursor: pointer; float: right; opacity: .7; }
  body.dark .hint .title { color: #a5b4fc; }
  .hint .title:hover { opacity: 1; }
</style>
</head>
<body>
  <div class="top">
    <div class="brand">
      <div class="mark">🎬</div>
      <div>
        <h1>ScrollWright <span class="dot" id="dot"></span><span class="muted" id="srv">checking…</span></h1>
        <p class="sub">Saved reels → searchable Obsidian notes · free, local, deduped</p>
      </div>
    </div>
    <div class="spacer"></div>
    <div class="vaultpill" title="Notes land in &lt;vault&gt;/ScrollWright/">
      📁 <b id="hintVaultTarget">vault</b>
      <button id="openVault" class="secondary small" title="Open this folder in Explorer/Finder">Open</button>
    </div>
    <button class="theme-toggle" id="themeToggle" title="Toggle light / dark">🌙 Dark</button>
  </div>

  <div class="wrap">
    <div class="hint" id="hint">
      <span class="title" title="Dismiss">✕ hide</span>
      <b>Paste reel links on the left</b> → <b>Add + queue</b>. The server transcribes the
      audio locally, lets Gemini read the video + caption, and writes a note into
      <b>ScrollWright/</b> in your vault. One reel at a time, in the background —
      add as many as you like and watch the middle column. Failed reels can be requeued.
    </div>

    <div class="grid">

      <!-- ============================================================ LEFT -->
      <div class="colCtrl">

        <div class="card">
          <h2>Add reels</h2>
          <textarea id="addText" placeholder="Paste instagram reel/post URLs — one or many, any mess OK&#10;e.g. https://www.instagram.com/reel/ABC123/"></textarea>
          <div class="stack" style="margin-top:8px">
            <button id="addBtn" class="block">＋ Add &amp; queue</button>
            <button id="addClipboard" class="secondary block">📋 Paste from clipboard</button>
          </div>
          <div class="kbdhint"><kbd>Ctrl</kbd>+<kbd>Enter</kbd> to queue · <span id="addResult"></span></div>
        </div>

        <div class="card">
          <h2>Queue</h2>
          <div class="stack">
            <button id="processNow" class="block">▶ Process pending now</button>
            <button id="retryBtn" class="secondary block">↻ Requeue failed</button>
            <button id="reprocessAllBtn" class="secondary block">⟲ Reprocess all done</button>
            <button id="reprocessFilterBtn" class="secondary block">⟲ Reprocess filtered</button>
          </div>
          <div class="kbdhint">Reprocess uses the filter box in the middle column.</div>
        </div>

        <div class="card">
          <h2>Notes folder</h2>
          <input type="text" id="vault" placeholder="C:/path/to/vault" spellcheck="false" />
          <div class="kbdhint">Press <kbd>Enter</kbd> to save. Notes land in <b>&lt;vault&gt;/ScrollWright/</b>.</div>
        </div>

      </div>

      <!-- =========================================================== CENTER -->
      <div class="colMain">

        <div class="card">
          <h2>Ledger</h2>
          <div class="stats" id="stats"></div>

          <div class="listhead" style="margin-top:12px">
            <input type="text" id="filter" placeholder="Filter by shortcode or URL…" />
            <button id="filterBtn" class="secondary small">Filter</button>
            <button id="clearFilter" class="secondary small">Clear</button>
            <span class="count" id="count"></span>
          </div>

          <div class="tablewrap">
            <table>
              <thead><tr><th>Reel</th><th>Status</th><th>Added</th><th>Note</th><th></th></tr></thead>
              <tbody id="rows"></tbody>
            </table>
          </div>
          <div class="empty" id="empty" style="display:none">No reels yet — paste some links on the left to get started.</div>
        </div>

      </div>

      <!-- ============================================================ RIGHT -->
      <div class="colLog">

        <div class="card">
          <h2>Activity <span class="hintk">live tail</span></h2>
          <div class="logbar">
            <span class="muted" id="pollState">auto-refresh on</span>
            <span class="grow"></span>
            <button id="pollToggle" class="secondary small">Pause</button>
            <button id="refresh" class="secondary small">↻</button>
          </div>
          <div class="log" id="log"></div>
        </div>

      </div>

    </div>
  </div>

<script>
(function () {
  "use strict";
  var $ = function (id) { return document.getElementById(id); };
  var logEl = $("log");
  var POLL_MS = 2500;

  // ---- theme toggle (light by default; dark is the old look) ----
  (function () {
    var t = $("themeToggle");
    if (!t) return;
    t.addEventListener("click", function () {
      document.body.classList.toggle("dark");
      t.textContent = document.body.classList.contains("dark") ? "☀️ Light" : "🌙 Dark";
    });
  })();

  // ---- hint: fill the vault target line + dismiss ----
  (function () {
    var hint = $("hint");
    if (!hint) return;
    var vaultTarget = $("hintVaultTarget");
    function fillVaultTarget(path) {
      if (vaultTarget) {
        var short = path ? path.split("/").pop() : "vault";
        if (!short) short = "vault";
        vaultTarget.textContent = short;
        vaultTarget.title = path || "";
      }
    }
    fillVaultTarget(localStorage.getItem("sw_vault") || "");
    var dismiss = hint.querySelector(".title");
    if (dismiss) {
      dismiss.addEventListener("click", function () {
        hint.style.display = "none";
        try { localStorage.setItem("sw_hintDismissed", "1"); } catch (e) {}
      });
    }
    try {
      if (localStorage.getItem("sw_hintDismissed") === "1") hint.style.display = "none";
    } catch (e) {}
  })();

  function log(msg, cls) {
    var line = document.createElement("div");
    if (cls) line.className = cls;
    line.textContent = msg;
    logEl.appendChild(line);
    while (logEl.childNodes.length > 300) logEl.removeChild(logEl.firstChild);
    logEl.scrollTop = logEl.scrollHeight;
  }

  function api(path, opts) {
    opts = opts || {};
    var body = opts.body ? JSON.stringify(opts.body) : null;
    var headers = opts.headers || {};
    if (body) headers["Content-Type"] = "application/json";
    return fetch(path, { method: opts.method || "GET", headers: headers, body: body });
  }

  function badge(status) {
    var b = document.createElement("span");
    b.className = "badge " + (status || "pending");
    b.textContent = status || "pending";
    return b;
  }

  var pollTimer = null;
  var polling = true;

  function setPollLabel() {
    var lab = $("pollState");
    var btn = $("pollToggle");
    if (lab) lab.textContent = polling ? "auto-refresh on" : "auto-refresh paused";
    if (btn) btn.textContent = polling ? "Pause" : "Resume";
  }
  function startPoll() { stopPoll(); polling = true; setPollLabel(); tick(); pollTimer = setInterval(tick, POLL_MS); }
  function stopPoll() { if (pollTimer) { clearInterval(pollTimer); pollTimer = null; } polling = false; setPollLabel(); }
  function togglePoll() { polling ? stopPoll() : startPoll(); }

  function fillStats(s) {
    var el = $("stats");
    el.innerHTML = "";
    var order = [
      { k: "processing", l: "Working" },
      { k: "pending", l: "Pending" },
      { k: "done", l: "Done" },
      { k: "failed", l: "Failed" }
    ];
    order.forEach(function (o) {
      var n = (s && s[o.k]) || 0;
      var d = document.createElement("div");
      d.className = "stat " + o.k;
      d.innerHTML = "<span class='n'>" + n + "</span><span class='l'>" + o.l + "</span>";
      el.appendChild(d);
    });
  }

  function renderLedgers(data) {
    var rows = (data && data.ledgers) || [];
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
      a.href = r.url; a.target = "_blank"; a.textContent = r.shortcode;
      td1.appendChild(a);

      var td2 = document.createElement("td");
      td2.appendChild(badge(r.status));

      var td3 = document.createElement("td");
      td3.className = "when";
      td3.textContent = (r.added_at || "").slice(0, 16).replace("T", " ");

      var td4 = document.createElement("td");
      if (r.note_path) {
        var p = document.createElement("a");
        p.href = "file://" + r.note_path;
        p.className = "notelink";
        p.textContent = r.note_path.split(/[\\/]/).pop();
        p.target = "_blank";
        p.title = r.note_path;
        td4.appendChild(p);
      } else {
        td4.innerHTML = "<span class='muted'>—</span>";
      }

      var td5 = document.createElement("td");
      td5.className = "actions";
      if (r.status === "done") {
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
      } else {
        td5.innerHTML = "<span class='muted'>…</span>";
      }

      tr.appendChild(td1); tr.appendChild(td2); tr.appendChild(td3); tr.appendChild(td4); tr.appendChild(td5);
      tbody.appendChild(tr);
    });
    $("count").textContent = shown + " / " + rows.length + " shown";
  }

  var lastBusy = null;
  function refreshStatus() {
    api("/status").then(function (r) { return r.json(); }).then(function (s) {
      window.__lastStatus = s;
      var on = s && s.running;
      $("dot").classList.toggle("on", !!on);
      $("srv").textContent = on ? "online" : "offline";
      $("srv").className = "muted" + (on ? " ok" : " err");
      if (on && s.stats) fillStats(s.stats);

      // log busy transitions only (not every poll) so the log stays readable
      if (on && s.busy && s.current && s.current !== lastBusy) {
        log("working on " + s.current + "…", "muted");
        lastBusy = s.current;
      } else if (!s.busy && lastBusy) {
        log("idle — queue drained", "muted");
        lastBusy = null;
      }

      var vt = $("hintVaultTarget");
      if (vt && s && s.vault) {
        vt.textContent = s.vault.split(/[\\/]/).pop() || "vault";
        vt.title = s.vault;
        try { localStorage.setItem("sw_vault", s.vault); } catch (e) {}
      }
    }).catch(function () {
      $("dot").classList.remove("on");
      $("srv").textContent = "offline";
      $("srv").className = "muted err";
      window.__lastStatus = null;
    });
  }

  function refreshLedgers() {
    api("/api/ledgers").then(function (r) { return r.json(); })
      .then(function (d) { renderLedgers(d); })
      .catch(function () { log("ledger read failed", "err"); });
  }

  function tick() { refreshStatus(); refreshLedgers(); }

  function setAddResult(msg, cls) {
    $("addResult").textContent = msg;
    $("addResult").className = (cls || "");
  }

  function addText(text, src) {
    if (!text || !text.trim()) { setAddResult("nothing to add", "err"); return; }
    api("/add", { method: "POST", body: { text: text } }).then(function (r) { return r.json(); })
      .then(function (d) {
        if (d && d.added !== undefined) {
          setAddResult("added " + d.added + ", " + d.duplicates + " dup" + (d.duplicates === 1 ? "" : "s"), "ok");
          log(src + ": queued " + d.added + " new", "ok");
          $("addText").value = "";
          setTimeout(refreshLedgers, 400);
        } else {
          setAddResult((d && d.error) || "add failed", "err");
          log((d && d.error) || "add failed", "err");
        }
      }).catch(function () { setAddResult("server unreachable", "err"); });
  }

  function retryOne(sc) {
    api("/api/retry", { method: "POST", body: { shortcode: sc } }).then(function (r) { return r.json(); })
      .then(function () { log("requeued " + sc, "ok"); setTimeout(refreshLedgers, 300); })
      .catch(function () { log("retry failed", "err"); });
  }

  function reprocessOne(sc) {
    api("/api/reprocess", { method: "POST", body: { filter: sc } }).then(function (r) { return r.json(); })
      .then(function () { log("reprocessed " + sc, "ok"); setTimeout(refreshLedgers, 300); })
      .catch(function () { log("reprocess failed", "err"); });
  }

  function setVault(path) {
    api("/vault", { method: "POST", body: { path: path } }).then(function (r) { return r.json(); })
      .then(function (d) {
        if (d && d.path) {
          $("vault").value = d.path;
          log("vault → " + d.path, "ok");
          var vt = $("hintVaultTarget");
          if (vt) { vt.textContent = d.path.split(/[\\/]/).pop() || "vault"; vt.title = d.path; }
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
    var p = ($("vault").value || "").trim();
    if (!p) { log("no vault set", "err"); return; }
    window.location.href = "file://" + p;
  });

  $("addBtn").addEventListener("click", function () { addText($("addText").value, "added"); });
  $("addClipboard").addEventListener("click", function () {
    addText((navigator.clipboard && navigator.clipboard.readText) ? navigator.clipboard.readText() : "", "clipboard");
  });
  $("addText").addEventListener("keydown", function (e) {
    if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); addText($("addText").value, "added"); }
  });

  $("processNow").addEventListener("click", function () {
    api("/api/process", { method: "POST" }).then(function (r) { return r.json(); })
      .then(function () { log("processing kicked off", "ok"); setTimeout(refreshLedgers, 300); })
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
        elif route == "/projectRoot":
            self._send(200, {"path": str(core.PROJECT_ROOT.parent)})
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
            # Optional per-reel video toggle from the extension:
            # {"text": ..., "watch": true|false} — absent = env default.
            watch = data.get("watch")
            watch = None if watch is None else bool(watch)
            urls = core.extract_reel_urls(text)
            if not urls:
                self._send(400, {"error": "no instagram reel/post URLs found"})
                return
            new, dup = core.add_urls(urls, watch=watch)
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
