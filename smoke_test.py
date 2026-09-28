"""One-off smoke tests for ScrollWright (no test framework — just run me).

Run:  python smoke_test.py            # fast checks (unit + network probes)
      python smoke_test.py --media    # also download a real reel + transcribe (slow)

Safe: ledger tests use a throwaway SQLite file, notes go to a temp dir,
the real vault and the real ledger are never written to.
"""
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import urllib.request
from pathlib import Path

# Windows consoles default to cp1252 — keep emoji/unicode output from crashing.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).parent))

from scrollwright import core, pipeline  # noqa: E402

failures = []


def check(name, cond, extra=""):
    status = "PASS" if cond else "FAIL"
    print(f"  [{status}] {name}" + (f" — {extra}" if extra else ""))
    if not cond:
        failures.append(name)


def section(title):
    print(f"\n== {title} ==")


# --- 1. URL extraction ------------------------------------------------------
section("URL extraction (core.extract_reel_urls)")
messy = """
https://www.instagram.com/reel/CxYzAbCdEfG/
https://www.instagram.com/reels/Dabc123_-/  some junk around it
http://instagr.am/p/DbCdEfGhIjK/
https://www.instagram.com/someuser/reel/DefGhiJklMn/?utm_source=x
not a link: instagram.com/reel/ (no scheme, no code)
"""
urls = core.extract_reel_urls(messy)
check("finds 4 urls", len(urls) == 4, str(sorted(urls)))
check(
    "canonicalizes to /reel/<code>/",
    urls.get("CxYzAbCdEfG") == "https://www.instagram.com/reel/CxYzAbCdEfG/",
)
check("handles /p/ posts", "DbCdEfGhIjK" in urls)
check("handles usernames in path", "DefGhiJklMn" in urls)

# --- 2. slugify ---------------------------------------------------------------
section("Slugify (pipeline.slugify)")
check(
    "keeps words + caps",
    pipeline.slugify("Self-Host Your Analytics in 5 Minutes", "fb")
    == "Self-Host-Your-Analytics-in-5-Minutes",
)
check(
    "strips punctuation",
    pipeline.slugify("Docker: 90% of devs get this *wrong*!", "fb")
    == "Docker-90-of-devs-get-this-wrong",
)
check("caps length at 60", len(pipeline.slugify("x" * 200, "fb")) <= 60)
check("empty title falls back", pipeline.slugify("", "reel-abc") == "reel-abc")

# --- 3. ledger ops (throwaway DB) --------------------------------------------
section("Ledger ops (throwaway SQLite)")
tmp = Path(tempfile.mkdtemp(prefix="scrollwright-test-"))
orig_db = core.DB_PATH
core.DB_PATH = tmp / "ledger.sqlite3"
core.init_db()
new, dup = core.add_urls(
    {
        "abc123": "https://www.instagram.com/reel/abc123/",
        "def456": "https://www.instagram.com/reel/def456/",
    }
)
check("adds 2 new", (new, dup) == (2, 0), f"new={new} dup={dup}")
new2, dup2 = core.add_urls(
    {
        "abc123": "https://www.instagram.com/reel/abc123/",
        "xyz789": "https://www.instagram.com/reel/xyz789/",
    }
)
check("dedupes on re-add", (new2, dup2) == (1, 1), f"new={new2} dup={dup2}")
rows = core.claim_batch(1)
check(
    "claim_batch claims oldest pending",
    len(rows) == 1 and rows[0]["shortcode"] == "abc123",
)
core.mark_done("abc123", "/tmp/note.md")
core.mark_failed("def456", "boom")
s = core.stats()
check(
    "stats reflect done/failed/pending",
    s.get("done") == 1 and s.get("failed") == 1 and s.get("pending") == 1,
    str(s),
)
core.DB_PATH = orig_db

# --- 4. config + note writing (temp vault) ------------------------------------
section("Config + note writing (temp vault)")
os.environ["VAULT_PATH"] = str(tmp / "vault")
core.load_config()
check("creates <vault>/ScrollWright/", core.VAULT_NOTES_DIR.exists())
check(
    "seeds dashboard",
    (core.VAULT_NOTES_DIR / "00 ScrollWright Dashboard.md").exists(),
)
out = {
    "title": "Test Note: Hello World",
    "body": "# Test Note: Hello World\n\n> tl;dr\n\n## My thoughts\n- \n",
    "type": "tutorial",
    "tags": ["docker", "self-hosted"],
}
p = pipeline.write_note("tst123", out, "somecreator")
check("note file written", p.exists(), p.name)
text = p.read_text(encoding="utf-8")
check("frontmatter has author", 'author: "somecreator"' in text)
check("frontmatter has status inbox", "status: inbox" in text)
check("frontmatter has url", "url: https://www.instagram.com/reel/tst123/" in text)
p2 = pipeline.write_note("tst456", out, None)
check(
    "same-title collision gets shortcode suffix",
    p2 != p and p2.exists(),
    p2.name,
)
check('missing author becomes "unknown"', 'author: "unknown"' in p2.read_text(encoding="utf-8"))

# --- 5. Instagram embed metadata (network) -------------------------------------
section("Instagram embed metadata (network)")
shortcode = None
try:
    with sqlite3.connect(orig_db) as con:
        row = con.execute(
            "SELECT shortcode, url FROM reels WHERE status='done' LIMIT 1"
        ).fetchone()
    if row:
        shortcode, _url = row
except Exception as exc:
    print(f"  (couldn't read ledger for a test shortcode: {exc})")

if shortcode:
    meta = pipeline.fetch_metadata(f"https://www.instagram.com/reel/{shortcode}/", shortcode)
    check(
        "embed reachable (caption or author)",
        meta.get("caption") is not None or meta.get("author") is not None,
        f"author={meta.get('author')} caption_len={len(meta.get('caption') or '')}",
    )
else:
    print("  [SKIP] no reel in the ledger to test with")

# --- 6. Gemini key + model availability (network) --------------------------------
section("Gemini API key + models (network)")
key = os.environ.get("GEMINI_API_KEY", "").strip()
if not key:
    print("  [SKIP] no GEMINI_API_KEY found in env/.env")
else:
    print(f"  key loaded: {key[:6]}…{key[-4:]}")
    try:
        req = urllib.request.Request(
            "https://generativelanguage.googleapis.com/v1beta/models"
            f"?key={key}&pageSize=1000"
        )
        with urllib.request.urlopen(req, timeout=30) as r:
            models = json.loads(r.read().decode())["models"]
        names = {m["name"].split("/")[-1] for m in models}
        check("key valid (models list OK)", True, f"{len(names)} models visible")
        for m in pipeline._GEMINI_MODELS:
            check(f"model {m} available", m in names)
    except Exception as exc:
        check("Gemini API reachable", False, str(exc)[:150])

# --- 7. media end-to-end (opt-in: --media) ---------------------------------------
if "--media" in sys.argv and shortcode:
    section("Media: yt-dlp audio fetch + whisper (network, slow)")
    from scrollwright.media import fetch_audio, transcribe

    test_sc = f"{shortcode}smoke"
    audio = fetch_audio(f"https://www.instagram.com/reel/{shortcode}/", test_sc)
    if audio and audio.exists():
        check("audio downloaded", audio.stat().st_size > 10_000, f"{audio.stat().st_size} bytes")
        transcript = transcribe(audio)
        check("transcription returned text", transcript is not None, f"{len(transcript or '')} chars")
        if transcript:
            print(f"    transcript preview: {transcript[:120]}")
        audio.unlink(missing_ok=True)
    else:
        check("audio downloaded", False, "yt-dlp returned no file (IG may require login)")
elif "--media" in sys.argv:
    print("\n[SKIP] media test — no shortcode available from ledger")

# --- cleanup + verdict ------------------------------------------------------------
shutil.rmtree(tmp, ignore_errors=True)
print(f"\n{'=' * 50}")
if failures:
    print(f"FAILED ({len(failures)}): {failures}")
    sys.exit(1)
print("ALL CHECKS PASSED ✅")
