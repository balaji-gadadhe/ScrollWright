"""yt-dlp audio fetch + faster-whisper local transcription."""

import os
import shutil
import subprocess
import sys
import urllib.parse
from pathlib import Path

from .core import AUDIO_DIR
VIDEO_DIR = AUDIO_DIR.parent / "video"

# Override in .env with WHISPER_MODEL=medium for better accuracy (slower, ~1.5GB)
WHISPER_MODEL = os.environ.get("WHISPER_MODEL", "small")


def fetch_audio(url: str, shortcode: str) -> Path | None:
    """Download audio-only via yt-dlp. Returns the file path, or None."""
    AUDIO_DIR.mkdir(exist_ok=True)
    out_base = AUDIO_DIR / shortcode
    cmd = [
        sys.executable, "-m", "yt_dlp",
        "-f", "bestaudio/best",
        "-o", str(out_base) + ".%(ext)s",
        "--no-playlist",
        "--no-warnings",
        "--quiet",
        url,
    ]
    # -x (audio extraction) needs ffmpeg; without it we keep the native stream,
    # which for Instagram is already m4a — whisper handles that fine.
    if shutil.which("ffmpeg"):
        cmd[4:4] = ["-x", "--audio-format", "m4a"]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"    yt-dlp failed: {result.stderr.strip()[:200]}")
        return None
    for ext in ("m4a", "mp3", "opus", "ogg", "wav", "webm", "mp4", "aac", "m4v"):
        candidate = AUDIO_DIR / f"{shortcode}.{ext}"
        if candidate.exists():
            return candidate
    print(f"    yt-dlp finished but no audio file found; dir: {[p.name for p in AUDIO_DIR.glob(shortcode + '*')]}" )
    return None


def fetch_video(url: str, shortcode: str) -> Path | None:
    """Download the full (small) reel video so Gemini can watch it. Returns path or None."""
    VIDEO_DIR.mkdir(parents=True, exist_ok=True)
    out_base = VIDEO_DIR / shortcode
    cmd = [
        sys.executable, "-m", "yt_dlp",
        "-f", "best[height<=720]/best",
        "-o", str(out_base) + ".%(ext)s",
        "--no-playlist", "--no-warnings", "--quiet",
        url,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"    video fetch failed: {result.stderr.strip()[:150]}")
        return None
    for ext in ("mp4", "webm", "mkv", "mov"):
        candidate = VIDEO_DIR / f"{shortcode}.{ext}"
        if candidate.exists():
            return candidate
    return None


def transcribe(audio_path: Path) -> str | None:
    """Transcribe locally with faster-whisper. Returns text, or None on failure."""
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        print("    faster-whisper not installed; skipping transcription.")
        return None

    try:
        model = WhisperModel(WHISPER_MODEL, device="cpu", compute_type="int8")
        segments, _info = model.transcribe(str(audio_path), vad_filter=True)
        return " ".join(seg.text.strip() for seg in segments).strip()
    except Exception as exc:
        print(f"    transcription failed: {exc}")
        return None
