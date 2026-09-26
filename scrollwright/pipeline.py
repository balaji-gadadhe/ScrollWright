"""The heart: for each pending reel → audio → transcript → Gemini → .md note."""

import json
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path

from . import core
from .core import PROMPT_PATH

_GEMINI_MODELS = (
    "gemini-3.8-flash",   # newest; sometimes congested on free tier
    "gemini-3.5-flash",   # fallbacks, slightly older but usually available
    "gemini-2.5-flash",
)
_GEMINI_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    "{model}:generateContent?key={key}"
)
_META_RE = re.compile(r"(?m)^META:.*$")
_H1_RE = re.compile(r"(?m)^#\s+(.+)$")
_TYPE_RE = re.compile(r"type=([\w-]+)")
_TAGS_RE = re.compile(r"tags=([^\n|]+)")
_SLUG_RE = re.compile(r"[^\w\s-]")
_WS_RE = re.compile(r"[\s_]+")


class QuotaExhausted(Exception):
    """Every Gemini model returned 429 — the daily free quota is spent."""


# ---------------------------------------------------------------------------
# Metadata: caption + author, via Instagram's public embed endpoint (no login)
# ---------------------------------------------------------------------------

def fetch_metadata(url: str, shortcode: str) -> dict:
    """Try scraping the og-tags off the embed page. Best-effort, never fatal."""
    meta = {"caption": None, "author": None}
    endpoints = (
        f"https://www.instagram.com/p/{shortcode}/embed/captioned/",
        f"{url}embed/captioned/",
    )
    for ep in endpoints:
        try:
            req = urllib.request.Request(
                ep, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
            )
            html = urllib.request.urlopen(req, timeout=15).read().decode("utf-8", "ignore")
            m = re.search(r'class="Caption".*?<\/div>', html, re.DOTALL)
            if m:
                block = m.group(0)
                a = re.search(r'class="UsernameText">([^<]+)<', block)
                if a:
                    meta["author"] = a.group(1)
                cap = re.search(r'(?s)<div class="Caption">.*?<\/div>', block)
                text = re.sub(r"<[^>]+>", "", block)
                text = re.sub(r"\s+", " ", text).strip()
                if text:
                    meta["caption"] = text[:2000]
                    break
        except Exception:
            continue
    return meta


# ---------------------------------------------------------------------------
# Gemini
# ---------------------------------------------------------------------------

def upload_to_gemini(video_path: Path, api_key: str) -> str | None:
    """Upload video via the File API; wait until it's ACTIVE. Returns file URI."""
    import time
    size = video_path.stat().st_size
    if size > 20 * 1024 * 1024:
        print(f"    video too large to upload ({size // 10**6}MB), skipping watch")
        return None
    url = (
        "https://generativelanguage.googleapis.com/upload/v1beta/files"
        f"?key={api_key}"
    )
    payload = video_path.read_bytes()
    req = urllib.request.Request(
        url,
        data=payload,
        headers={
            "Content-Type": "video/mp4",
            "X-Goog-Upload-Protocol": "raw",
            "X-Goog-Upload-Command": "upload, finalize",
            "X-Goog-Upload-Header-Content-Length": str(len(payload)),
            "X-Goog-Upload-Header-Content-Type": "video/mp4",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        f = data.get("file", data)  # raw protocol returns the File object directly
        uri = f.get("uri")
    except urllib.error.HTTPError as exc:
        body = ""
        try:
            body = exc.read().decode("utf-8", "ignore")
        except Exception:
            pass
        print(f"    video upload failed: HTTP {exc.code} {body[:200]}")
        return None
    except Exception as exc:
        print(f"    video upload failed: {str(exc)[:150]}")
        return None
    if not uri:
        print(f"    video upload returned no uri: {str(data)[:150]}")
        return None
    # Wait for processing to finish (usually a few seconds)
    for _ in range(12):
        try:
            r = urllib.request.Request(
                f"{uri}?key={api_key}", method="GET"
            )
            with urllib.request.urlopen(r, timeout=30) as resp:
                st = json.loads(resp.read().decode("utf-8")).get("state")
            if st == "ACTIVE":
                return uri
            if st == "FAILED":
                return None
        except Exception:
            pass
        time.sleep(5)
    return None


def write_note_gemini(material: str, url: str, video_uri: str | None = None) -> dict | None:
    """Ask Gemini to draft the note. Returns {'text': str, 'title': str} or None."""
    api_key = core.os.environ.get("GEMINI_API_KEY", "").strip()
    if not api_key:
        print("    GEMINI_API_KEY missing — skipping Gemini (see .env)")
        return None

    system_prompt = PROMPT_PATH.read_text(encoding="utf-8")
    parts = []
    if video_uri:
        parts.append({"file_data": {"mime_type": "video/mp4", "file_uri": video_uri}})
    parts.append({"text": material})
    body = {
        "system_instruction": {"parts": [{"text": system_prompt}]},
        "contents": [{"role": "user", "parts": parts}],
        "generationConfig": {"temperature": 0.4, "maxOutputTokens": 2048},
    }
    # Lower media resolution = ~4x fewer video tokens. Fine for terminal/text reels.
    if core.os.environ.get("GEMINI_MEDIA_RESOLUTION", "LOW").upper() == "LOW":
        body["generationConfig"]["mediaResolution"] = "MEDIA_RESOLUTION_LOW"
    import time

    text = None
    quota_blocked = True  # stays True only if EVERY model ended in 429
    for model in _GEMINI_MODELS:
        model_quota = False
        for attempt in range(3):
            req = urllib.request.Request(
                _GEMINI_URL.format(model=model, key=api_key),
                data=json.dumps(body).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            try:
                with urllib.request.urlopen(req, timeout=60) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                text = data["candidates"][0]["content"]["parts"][0]["text"].strip()
                usage = data.get("usageMetadata", {})
                print(
                    f"    tokens: {usage.get('promptTokenCount', '?')} in / "
                    f"{usage.get('candidatesTokenCount', '?')} out"
                )
                break
            except urllib.error.HTTPError as exc:
                err_body = ""
                try:
                    err_body = exc.read().decode("utf-8", "ignore")
                except Exception:
                    pass
                if exc.code == 400:
                    print(f"    Gemini HTTP 400: {err_body[:200]}")
                    return None  # bad key/request — don't bother retrying
                if exc.code == 429:
                    # Quota errors don't clear in seconds — skip to next model.
                    print(f"    Gemini {model} HTTP 429 (quota)")
                    model_quota = True
                    break
                print(f"    Gemini {model} HTTP {exc.code} (attempt {attempt + 1}/3)")
                if attempt < 2:
                    time.sleep(5 * (attempt + 1))
            except Exception as exc:
                print(f"    Gemini call failed: {exc}")
                if attempt < 2:
                    time.sleep(5 * (attempt + 1))
        if not model_quota:
            quota_blocked = False
        if text:
            break
    if not text:
        if quota_blocked:
            raise QuotaExhausted()
        return None

    # Strip any frontmatter the model was told not to write.
    if text.startswith("---"):
        text = re.sub(r"\A---\n.*?\n---\n?", "", text, flags=re.DOTALL)

    # Pull type + tags out of the META tail line, drop it from the body.
    out = {"type": "other", "tags": []}
    m = _META_RE.search(text)
    if m:
        line = m.group(0)
        t = _TYPE_RE.search(line)
        if t:
            out["type"] = t.group(1)
        tg = _TAGS_RE.search(line)
        if tg:
            out["tags"] = [x.strip() for x in tg.group(1).split(",") if x.strip()]
        text = _META_RE.sub("", text).rstrip()

    # Title = first H1 in the body.
    h1 = _H1_RE.search(text)
    out["title"] = h1.group(1).strip().strip('"') if h1 else None
    out["body"] = text
    return out


# ---------------------------------------------------------------------------
# Note writing
# ---------------------------------------------------------------------------

def slugify(title: str, fallback: str) -> str:
    s = _SLUG_RE.sub("", title.strip()) if title else ""
    s = _WS_RE.sub("-", s).strip("-")
    if len(s) > 60:
        s = s[:60].rsplit("-", 1)[0]
    return s or fallback


def write_note(shortcode: str, out: dict, author: str | None) -> Path:
    """Prepend system-owned frontmatter to the AI body and write the file."""
    title = out.get("title") or f"Reel {shortcode}"
    fname = f"{date.today().isoformat()} {slugify(title, f'reel-{shortcode}')}.md"
    notes_dir = core.VAULT_NOTES_DIR
    path = notes_dir / fname
    if path.exists():
        path = notes_dir / f"{date.today().isoformat()} {slugify(title, f'reel-{shortcode}')}-{shortcode}.md"
    tags = out.get("tags") or ["unsorted"]
    front = (
        "---\n"
        f'title: "{title}"\n'
        "source: instagram\n"
        f'author: "{author or "unknown"}"\n'
        f"saved: {date.today().isoformat()}\n"
        f"type: {out.get('type', 'other')}\n"
        f"tags: [{', '.join(tags)}]\n"
        f"url: https://www.instagram.com/reel/{shortcode}/\n"
        "status: inbox\n"
        "---\n\n"
    )
    path.write_text(front + out["body"] + "\n", encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

def process_batch(limit: int | None = None) -> None:
    from .media import fetch_audio, fetch_video, transcribe

    # A fresh run means any previous run is dead — requeue its stuck rows.
    import sqlite3
    with sqlite3.connect(core.DB_PATH) as con:
        requeued = con.execute(
            "UPDATE reels SET status='pending' WHERE status='processing'"
        ).rowcount
    if requeued:
        print(f"Requeued {requeued} reel(s) from an interrupted run.")

    rows = core.claim_batch(limit or 10**9)
    if not rows:
        print("Nothing pending. Add URLs first:  python -m scrollwright add")
        return

    done = failed = 0
    try:
        for i, row in enumerate(rows, 1):
            sc, url = row["shortcode"], row["url"]
            print(f"[{i}/{len(rows)}] {url}")
            try:
                api_key = core.os.environ.get("GEMINI_API_KEY", "").strip()

                audio = fetch_audio(url, sc)
                transcript = transcribe(audio) if audio else None
                if audio and audio.exists():
                    audio.unlink(missing_ok=True)

                # Let Gemini watch the reel itself (screen content, demos, code).
                # Disable in .env with WATCH_VIDEOS=false to save tokens/time.
                watch = core.os.environ.get("WATCH_VIDEOS", "true").lower() != "false"
                video = fetch_video(url, sc) if watch else None
                video_uri = None
                if video and api_key:
                    video_uri = upload_to_gemini(video, api_key)
                if video and video.exists():
                    video.unlink(missing_ok=True)

                meta = fetch_metadata(url, sc)
                material = (
                    f"INSTAGRAM REEL MATERIAL\n"
                    f"URL: {url}\n"
                    f"AUTHOR: {meta['author'] or 'unknown'}\n"
                    f"CAPTION: {meta['caption'] or '(none available)'}\n"
                    f"TRANSCRIPT:\n{transcript or '(transcription unavailable)'}\n"
                )
                print(f"    transcript: {len(transcript or '')} chars | caption: {len(meta['caption'] or '')} chars | watching: {'yes' if video_uri else 'no'}")

                out = write_note_gemini(material, url, video_uri)
                if out is None:
                    core.mark_failed(sc, "gemini returned nothing")
                    failed += 1
                    print("    [FAIL] no note produced")
                    continue

                path = write_note(sc, out, meta["author"])
                core.mark_done(sc, str(path))
                done += 1
                print(f"    [OK] {path.name}")
            except QuotaExhausted:
                print(f"\n[QUOTA] Daily Gemini limit reached at reel {i}/{len(rows)}.")
                print(f"   {len(rows) - i + 1} reel(s) stay pending — run process again tomorrow.")
                print("   Everything processed so far is safe.")
                break
            except Exception as exc:
                core.mark_failed(sc, f"{type(exc).__name__}: {exc}")
                failed += 1
                print(f"    [FAIL] {type(exc).__name__}: {str(exc)[:150]}")
    except KeyboardInterrupt:
        print("\nInterrupted — remaining reels stay 'pending', just run process again.")
    finally:
        # Anything still marked 'processing' (crashed run) goes back to pending.
        import sqlite3
        with sqlite3.connect(core.DB_PATH) as con:
            con.execute("UPDATE reels SET status='pending' WHERE status='processing'")
        print(f"\nDone. {done} noted, {failed} failed.")
