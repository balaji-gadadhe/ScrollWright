# 🎬 ScrollWright

**Turn your saved Instagram Reels into searchable Obsidian notes — free, local, deduped.**

> You save 200 reels. You watch 3. The Scrollwright handles the other 197.

A *wright* is a maker — shipwright, playwright, wheelwright. ScrollWright is
the one who writes down what you scroll: it listens to what's said, *looks at*
what's on screen, reads the caption, and files every reel into your Obsidian
vault as a proper markdown note — with the actual commands, tools, and numbers
that made you save it in the first place.

Built for people whose "saved" folder is a graveyard. 100% free-tier friendly.

## Why

- Instagram's saved folder has **no search, no tags, no export** — it's a dump
- Reels are where tool recommendations actually live, but you never revisit them
- LLM APIs cost pennies, local Whisper is free — there was no reason this shouldn't exist

## How it works

```
reel URLs (clipboard / file / Chrome extension)
   → dedupe (SQLite ledger keyed by reel shortcode — same reel saved N times = 1 note)
   → yt-dlp grabs the audio            → faster-whisper transcribes locally (free, offline)
   → yt-dlp grabs the video            → Gemini "watches" it: code, demos, UI, text on screen
   → caption + author scraped from IG's public embed page
   → Gemini writes a structured note   → .md lands in <your vault>/ScrollWright/
```

Resilience built in: per-reel error isolation, model fallback chain with
retries on 429/503, resumable runs (Ctrl+C anytime), `retry` / `reprocess`
commands. A reel with broken audio still gets noted from video + caption.

## Example output

````markdown
---
title: "Self-Host Your Analytics in 5 Minutes"
source: instagram
author: "somecreator"
saved: 2026-09-26
type: tutorial
tags: [self-hosted, docker, analytics, privacy]
url: https://www.instagram.com/reel/CxYzAbCdEfG/
status: inbox
---

# Self-Host Your Analytics in 5 Minutes

> Swap Google Analytics for an open-source, cookieless alternative on a $5 VPS.

## Key points
- **Tool**: open-source web analytics, self-hosted, ~200 MB RAM
- **Why**: no cookies, GDPR-friendly, your data never leaves your server

## On screen
- Dashboard demo: live visitor counter, pageviews chart, referrer table

## Snippets / Commands
```bash
docker run -d -p 3000:3000 ghcr.io/selfhosted/analytics:latest
```

## Details / Steps
1. Point a subdomain at your VPS
2. Run the container with a Postgres URL
3. Add your site, paste the tracking snippet
````

## Setup

```bash
# 1. Python 3.10+
pip install -r requirements.txt

# 2. Configure: copy .env.example → .env and fill in:
#    VAULT_PATH     your Obsidian vault (notes go to <vault>/ScrollWright/)
#    GEMINI_API_KEY free from https://aistudio.google.com/apikey

# 3. First run downloads the whisper model (~460MB, one-time)
```

No ffmpeg needed (optional; install it if you want audio re-encoding).
No database server, no Docker, no VPS — one folder, runs anywhere.

## Usage

```bash
python -m scrollwright add                  # reel URLs from clipboard
python -m scrollwright add links.txt        # from a file (one URL per line, any mess OK)
python -m scrollwright add -                # from stdin
python -m scrollwright process              # process the backlog (resumable)
python -m scrollwright process --limit 5    # just 5
python -m scrollwright retry                # requeue failed reels
python -m scrollwright reprocess [filter]   # re-run notes with the latest pipeline
python -m scrollwright stats                # ledger status
```

### Bulk import: your ENTIRE saved folder

Bundled Chrome extension (unpacked, no store):

1. Chrome → `chrome://extensions/` → enable **Developer mode**
2. **Load unpacked** → select the `extension/` folder
3. Open `instagram.com/<your-username>/saved/all-posts/`
4. Click the purple **📥 Export saved reels** button — it auto-scrolls and
   downloads `saved-reels.txt`
5. `python -m scrollwright add saved-reels.txt && python -m scrollwright process`

Re-run the export anytime — the ledger dedupes, so only new reels get processed.

## Configuration (`.env`)

| Var | Default | What it does |
|---|---|---|
| `VAULT_PATH` | — | **required** — your Obsidian vault root |
| `GEMINI_API_KEY` | — | **required** — free tier is plenty (~100+ reels/day) |
| `WATCH_VIDEOS` | `true` | `false` = audio+caption only (≈3× fewer tokens, no upload) |
| `WHISPER_MODEL` | `small` | `tiny`/`base`/`small`/`medium` — bigger = better brand-name accuracy |
| `GEMINI_MEDIA_RESOLUTION` | `LOW` | `HIGH` only for visually dense reels |
| `IG_USERNAME` | — | reserved for "skip my own reels" |

### Cost / tokens

Measured with video watching: **~1.6k–9k tokens per reel** (video ≈ 2.6k at
low res, scales with reel length, not size). The Gemini free tier handles
100+ reels/day; a 300-reel backlog costs **$0**. Even paid Flash would put
that entire backlog under a dollar.

### Privacy

- Transcription is 100% local (faster-whisper, CPU)
- Watching requires uploading the video to Google's Gemini File API
  (auto-deleted after 48h; local copy deleted immediately after)
- Your ledger, notes, and API key never leave your machine (except to Gemini)
- Set `WATCH_VIDEOS=false` for audio+caption-only mode

## Notes land like this

- One `.md` per reel in `<vault>/ScrollWright/`, `status:: inbox` for your triage
- `00 ScrollWright Dashboard.md` (auto-created) gives Dataview inbox/done tables —
  install Obsidian's **Dataview** plugin to see them live
- Edit the note template in `scrollwright/static/prompt.txt` to change structure,
  tone, tags, language of your notes

## Roadmap / ideas

- [ ] YouTube support (yt-dlp already speaks it — widen the URL regex)
- [ ] Extension auto-send to a local server (skip the txt file)
- [ ] RAG chat over your notes ("what was that color-palette tool?")
- [ ] Repost detector via perceptual frame hashing
- [ ] Weekly auto-MOC note linking the week's reels

## Stack

Python + yt-dlp + faster-whisper (CTranslate2) + Gemini free tier + vanilla
Chrome MV3 extension. That's it.

## License

MIT — do whatever you want. If you build something cool on top, a star or a
shoutout is appreciated.
