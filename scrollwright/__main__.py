#!/usr/bin/env python3
"""ScrollWright - turn saved Instagram Reels into Obsidian notes.

Usage:
    python -m scrollwright add                # read reel URLs from the clipboard
    python -m scrollwright add <file.txt>     # read reel URLs from a file
    python -m scrollwright add -              # read reel URLs from stdin
    python -m scrollwright process [--limit N]
    python -m scrollwright retry               # requeue failed reels
    python -m scrollwright reprocess [filter]  # redo reels with current pipeline
    python -m scrollwright stats
"""

import sys
import tkinter
from pathlib import Path

from scrollwright import core, pipeline


def cmd_add(args: list[str]) -> None:
    if args and Path(args[0]).exists():
        text = Path(args[0]).read_text(encoding="utf-8", errors="ignore")
        src = args[0]
    elif args and args[0] == "-":
        text = sys.stdin.read()
        src = "stdin"
    else:
        try:
            root = tkinter.Tk()
            root.withdraw()
            text = root.clipboard_get()
            root.destroy()
            src = "clipboard"
        except Exception:
            print("Nothing on clipboard. Usage: python -m scrollwright add <file.txt> | -")
            sys.exit(1)

    urls = core.extract_reel_urls(text)
    if not urls:
        print(f"No instagram reel/post URLs found in {src}.")
        sys.exit(1)

    new, dup = core.add_urls(urls)
    print(f"From {src}: {new} new reel(s), {dup} duplicate(s) skipped.")
    if new:
        print("Run:  python -m scrollwright process")


def cmd_process(args: list[str]) -> None:
    limit = None
    if "--limit" in args:
        i = args.index("--limit")
        limit = int(args[i + 1])
    core.load_config()
    pipeline.process_batch(limit)


def cmd_stats(args: list[str]) -> None:
    s = core.stats()
    total = sum(s.values())
    print(f"Ledger: {total} reel(s) total")
    for k, v in sorted(s.items()):
        print(f"  {k:<10} {v}")


def cmd_retry(args: list[str]) -> None:
    import sqlite3
    with sqlite3.connect(core.DB_PATH) as con:
        n = con.execute(
            "UPDATE reels SET status='pending', error=NULL WHERE status='failed'"
        ).rowcount
    print(f"Requeued {n} failed reel(s). Run:  python -m scrollwright process")


def cmd_reprocess(args: list[str]) -> None:
    """Requeue done reels (all, or ones whose URL/shortcode contains the arg)."""
    import sqlite3
    with sqlite3.connect(core.DB_PATH) as con:
        if args:
            n = con.execute(
                "UPDATE reels SET status='pending', note_path=NULL WHERE status='done' AND (shortcode LIKE ? OR url LIKE ?)",
                (f"%{args[0]}%", f"%{args[0]}%"),
            ).rowcount
        else:
            n = con.execute(
                "UPDATE reels SET status='pending', note_path=NULL WHERE status='done'"
            ).rowcount
    print(f"Requeued {n} reel(s) for reprocessing. Run:  python -m scrollwright process")


COMMANDS = {"add": cmd_add, "process": cmd_process, "stats": cmd_stats, "retry": cmd_retry, "reprocess": cmd_reprocess}


def main() -> None:
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        print(__doc__)
        sys.exit(1)
    core.init_db()
    COMMANDS[sys.argv[1]](sys.argv[2:])


if __name__ == "__main__":
    main()
