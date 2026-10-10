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

**Tags:** #self-hosted #docker #analytics #privacy

## Raw source
### Caption
…the reel's caption, verbatim, untouched by Gemini…
### Transcript
…the full spoken transcript, verbatim…

## Setup

```bash
# 1. Python 3.10+
pip install -r requirements.txt

# 2. Copy .env.example → .env and fill in:
#    VAULT_PATH      your Obsidian vault (notes go to <vault>/ScrollWright/)
#    GEMINI_API_KEY  free from https://aistudio.google.com/apikey (no card)

# 3. First run downloads the whisper model (~460 MB, one-time, offline)
```

No ffmpeg needed (optional). No database server, no Docker, no VPS — one folder, runs anywhere.

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
python -m scrollwright serve                # localhost bridge for the extension + API
python -m scrollwright serve --ui           # same, and open the web UI
python -m scrollwright ui                   # start if needed + open web UI + keep it alive
```

### Live mode: one click → one note

Don't want the txt file round-trip at all? Run the local server and use the
extension's popup:

```bash
python -m scrollwright serve        # leave it running while you browse
```

- Popup → **📝 Note this reel**: on any reel/post page, queues it instantly —
  transcript + Gemini + note lands in your vault in ~1 minute.
- Popup → **📥 Harvest saved folder**: auto-scrolls your saved folder (works on
  `all-posts` *and* individual collections like `saved/dev-quests/`) and queues
  everything new. The ledger dedupes, so re-harvests only add what's new.
- Popup → **Notes folder**: point the pipeline at any vault folder on the fly
  (updates `.env`).
- **Turn on / off ScrollWright:** the popup's **▶ Start ScrollWright** button
  shows the command you need to run (a small code block with a **Copy** button
  next to it). Click **Copy**, paste into a terminal, hit Enter. That's
  `cd <project>; & <python> -m scrollwright ui` — **PowerShell syntax** (`;`
  chains, `&` runs the exe), which is Windows' default terminal. The extension
  can't run commands directly, so this is the handoff.
  
  There's also a **⚙ Configure paths** button in the popup for anyone who isn't
  on the machine this was built on: set your project folder and python binary
  path there (saved in your browser's local storage), and the command updates
  with your values.
- Server offline? The extension falls back to the txt download automatically.

The server binds `127.0.0.1` only and speaks to nothing but your browser.

### Porting the extension to another machine

The popup ships with placeholder defaults you'll need to change:

- `DEFAULT_PROJECT_ROOT` (`extension/popup.js`) — the folder containing the
  ScrollWright project, used as a last-resort fallback.
- `PYTHON_BIN` (`extension/popup.js`) — the python binary that runs
  `python -m scrollwright ui` (set it to your `python.exe` full path if
  `python` isn't on your PATH — that avoids the Windows Store alias issue).

You have two ways to set these for your own machine:

1. **Easier:** click **⚙ Configure paths** in the popup. Set your project folder
   and python path, click Save. The values are stored in your browser's
   `chrome.storage.local`, so they persist across popup opens and don't touch the
   extension files.
2. **Direct:** edit `DEFAULT_PROJECT_ROOT` and `PYTHON_BIN` at the top of
   `extension/popup.js` — or set `PYTHON_BIN = "python"` if `python` is on your
   PATH and you don't need the full path.

After changing either, click **▶ Start ScrollWright** and the copied command will
use your values.

### Web UI: 3-panel inbox to manage your reels

`python -m scrollwright ui` starts the server (if it isn't already running), opens
`http://127.0.0.1:8765/` in your browser, and keeps watching it — if the server
crashes it restarts automatically. One command, one terminal, the UI stays up.

The UI is a **3-panel app**: a control panel on the left, the reel list in the
middle, and a live activity log on the right.

```text
┌─────────────────────────────────────────────────────────────────┐
│ 🎬 ScrollWright ● online   Saved reels → searchable notes      │
├──────────────┬──────────────────────────────┬───────────────────┤
│ ADD REELS    │   LEDGER                     │ ACTIVITY (live)   │
│ [textarea]   │   [0][0][41][0] stats tiles  │ auto-refresh ●    │
│ [+ Add+queue]│   filter + filter/clear btns │ Pause   ↻         │
│ [paste from  │   replay | status | added |  │ ┌───────────────┐ │
│  clipboard]  │   note (click opens .md) |   │ │ queued 3 new  │ │
│              │   actions (retry/reprocess)  │ │ working on … │ │
│ QUEUE        │   ┌─────────────────────────┐ │ │ idle          │ │
│ [Process now]│   │ reel  shortcode         │ │ └───────────────┘ │
│ [Requeue fail│   │        status badge      │ │ │               │ │
│  ed]         │   │        when added        │ │                 │ │
│ [Reprocess…] │   │        note file name    |   │                 │ │
│              │   └─────────────────────────┘ │                 │ │
│ NOTES FOLDER │                                  │                 │ │
│ [vault path] │                                  │                 │ │
└──────────────┴───────────────────────────────────────────────────┘
```

What the three panels do:
- **Top header**: server status dot (online/offline), your vault path pill
  with an **Open** button that launches Explorer, and a **🌙 Dark / ☀️ Light**
  theme toggle.
- **Left — Controls**: paste reel URLs, add them as a batch, pull them from
  your clipboard, kick off processing, requeue failed reels, reprocess gone-
  stale notes (all or filtered), and change the vault path (Enter saves).
- **Middle — Ledger**: the stats tiles at a glance, a text filter for the
  shortcode/URL, and a scrollable table of every reel — status badge, when it
  was added, a click-to-open note link, and retry/reprocess buttons.
- **Right — Activity log**: a live tail of what the server is doing — adding
  batches, picking up work, finishing reels. It pauses/resumes and deduplicates
  log noise so it stays readable while a reel is processing.

**Two things to know:**
- The log only prints when something *changes* — when the server starts working
  on a reel and when the queue drains to idle — so it doesn't spam "working on…"
  every poll tick.
- **Reprocess filtered** uses the filter you typed in the middle panel. If the
  filter is empty it asks you to fill it in first.

`python -m scrollwright serve --ui` does the same thing (start server + open
the UI) if you prefer starting with that command.

### Bulk import: your ENTIRE saved folder

> Prefer no txt files at all? Run `python -m scrollwright serve` and use the
> extension popup's **Harvest** button — see *Live mode* above.

Bundled Chrome extension (unpacked, no store):

1. Chrome → `chrome://extensions/` → enable **Developer mode**
2. **Load unpacked** → select the `extension/` folder
3. Open `instagram.com/<your-username>/saved/all-posts/`
4. Click the purple **📥 Export saved reels** button — it auto-scrolls and
   downloads `saved-reels.txt`
5. `python -m scrollwright add saved-reels.txt` then `python -m scrollwright process`

Re-run the export anytime — the ledger dedupes, so only new reels get processed.

#### Turn the server on/off from the extension

The Chrome extension can't run a command directly, so the popup's **▶ Start
ScrollWright** button copies a ready-to-run command to your clipboard:

```
cd "<project-root>"; & "<python>" -m scrollwright ui
```

PowerShell syntax — paste straight into Windows PowerShell / Windows Terminal.
For **cmd.exe** use `cd /d "<project-root>" && "<python>" -m scrollwright ui`
instead; for bash/zsh use `cd "<project-root>" && <python> -m scrollwright ui`.

Paste it into a terminal and hit Enter. The server starts, the web UI opens,
and the popup's status light goes green. One terminal, stays up until you close
it (Ctrl+C).

To stop: close the terminal (Ctrl+C), or run `taskkill /F /IM python.exe`.
The copy-to-clipboard command above is the recommended way; a `.bat` launcher
in the project root works too if you prefer the Win+R path (the `.bat` files
aren't shipped in the repo — see `.gitignore`).

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
- [x] Extension auto-send to a local server (skip the txt file) — shipped: `python -m scrollwright serve` + popup
- [x] Local web UI to view/manage the ledger — shipped: `python -m scrollwright ui`
- [x] Auto-start + watchdog for the server — shipped: `python -m scrollwright ui` restarts if it dies
- [ ] RAG chat over your notes ("what was that color-palette tool?")
- [ ] Repost detector via perceptual frame hashing
- [ ] Weekly auto-MOC note linking the week's reels

## Stack

Python + yt-dlp + faster-whisper (CTranslate2) + Gemini free tier + vanilla
Chrome MV3 extension. That's it.

## License

MIT — do whatever you want. If you build something cool on top, a star or a
shoutout is appreciated.
