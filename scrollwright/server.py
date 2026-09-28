"""Local bridge between the browser extension and the pipeline.

Run:  python -m scrollwright serve

Endpoints (127.0.0.1 only):
    GET  /status   → {"running": true, "vault": "...", "stats": {...}, "busy": bool}
    POST /add      → {"text": "<any text containing reel URLs>"} — dedupes into
                     the ledger and processes new reels in the background.
    GET  /vault    → {"path": "..."}
    POST /vault    → {"path": "<new vault folder>"} — validates, updates .env.

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
PORT = 8765
MAX_BODY = 2 * 1024 * 1024  # 2MB of pasted text is plenty

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

    def do_OPTIONS(self):  # CORS preflight for the extension popup
        self._send(204, {})

    def do_GET(self):
        route = urlparse(self.path).path
        if route == "/status":
            self._send(200, {"running": True, "vault": str(core.VAULT_NOTES_DIR), "busy": _state["busy"], "current": _state["current"], "stats": core.stats()})
        elif route == "/vault":
            self._send(200, {"path": str(core.VAULT_NOTES_DIR)})
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

        else:
            self._send(404, {"error": "not found"})


def main() -> None:
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
    print("  waiting for reels from the extension… (Ctrl+C to stop)", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped. Any reel mid-flight gets requeued on next start.")


if __name__ == "__main__":
    main()
