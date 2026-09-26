"""Shared paths, config loading, and the SQLite dedupe ledger."""

import os
import re
import sqlite3
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent
DATA_DIR = PROJECT_ROOT / "data"
DB_PATH = DATA_DIR / "ledger.sqlite3"
AUDIO_DIR = DATA_DIR / "audio"
STATIC_DIR = PROJECT_ROOT / "static"
PROMPT_PATH = STATIC_DIR / "prompt.txt"

VAULT_PATH = None
VAULT_NOTES_DIR = None

_IG_HOSTS = ("instagram.com", "www.instagram.com", "instagr.am", "ig.me")
_REEL_RE = re.compile(
    r"https?://(?:www\.)?(?:instagram\.com|instagr\.am)/(?:[\w.\-]+/)?(?:reel|reels|p|tv)/([A-Za-z0-9_-]+)",
    re.IGNORECASE,
)


def load_config():
    """Load .env (without a dependency) and resolve vault paths."""
    global VAULT_PATH, VAULT_NOTES_DIR
    env_path = next(
        (
            p
            for p in (PROJECT_ROOT.parent / ".env", PROJECT_ROOT / ".env")
            if p.exists()
        ),
        None,
    )
    if env_path:
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            val = val.strip()
            if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
                val = val[1:-1]
            os.environ.setdefault(key.strip(), val)

    vault = os.environ.get("VAULT_PATH", "").strip()
    if not vault:
        raise SystemExit(
            "VAULT_PATH is not set. Copy .env.example to .env and point it at your vault."
        )
    VAULT_PATH = Path(vault).expanduser()
    VAULT_NOTES_DIR = VAULT_PATH / "ScrollWright"
    VAULT_NOTES_DIR.mkdir(parents=True, exist_ok=True)
    DATA_DIR.mkdir(exist_ok=True)
    AUDIO_DIR.mkdir(exist_ok=True)

    # Seed the Dataview dashboard once.
    dashboard = VAULT_NOTES_DIR / "00 ScrollWright Dashboard.md"
    if not dashboard.exists():
        dashboard.write_text(
            (STATIC_DIR / "dashboard.md").read_text(encoding="utf-8"),
            encoding="utf-8",
        )


# ---------------------------------------------------------------------------
# URL normalization / dedupe
# ---------------------------------------------------------------------------

def extract_reel_urls(text: str) -> dict[str, str]:
    """Return {shortcode: canonical_url} for every reel/post URL in text."""
    found = {}
    for match in _REEL_RE.finditer(text):
        code = match.group(1)
        found[code] = f"https://www.instagram.com/reel/{code}/"
    return found


def _connect() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con


def init_db() -> None:
    with _connect() as con:
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS reels (
                shortcode   TEXT PRIMARY KEY,
                url         TEXT NOT NULL,
                added_at    TEXT NOT NULL DEFAULT (datetime('now')),
                status      TEXT NOT NULL DEFAULT 'pending',
                note_path   TEXT,
                error       TEXT
            )
            """
        )


def add_urls(urls: dict[str, str]) -> tuple[int, int]:
    """Insert URLs. Returns (new, duplicates)."""
    new = dup = 0
    with _connect() as con:
        for code, url in urls.items():
            cur = con.execute(
                "INSERT OR IGNORE INTO reels (shortcode, url) VALUES (?, ?)",
                (code, url),
            )
            if cur.rowcount:
                new += 1
            else:
                dup += 1
    return new, dup


def claim_batch(limit: int) -> list[sqlite3.Row]:
    """Return up to `limit` pending rows, marking them 'processing'."""
    rows = []
    with _connect() as con:
        selected = con.execute(
            "SELECT shortcode, url FROM reels WHERE status = 'pending' ORDER BY added_at LIMIT ?",
            (limit,),
        ).fetchall()
        for r in selected:
            con.execute(
                "UPDATE reels SET status = 'processing' WHERE shortcode = ?",
                (r["shortcode"],),
            )
        rows = selected
    return rows


def mark_done(shortcode: str, note_path: str) -> None:
    with _connect() as con:
        con.execute(
            "UPDATE reels SET status = 'done', note_path = ?, error = NULL WHERE shortcode = ?",
            (note_path, shortcode),
        )


def mark_failed(shortcode: str, error: str) -> None:
    with _connect() as con:
        con.execute(
            "UPDATE reels SET status = 'failed', error = ? WHERE shortcode = ?",
            (error[:500], shortcode),
        )


def stats() -> dict[str, int]:
    with _connect() as con:
        rows = con.execute(
            "SELECT status, COUNT(*) AS n FROM reels GROUP BY status"
        ).fetchall()
    return {r["status"]: r["n"] for r in rows}
