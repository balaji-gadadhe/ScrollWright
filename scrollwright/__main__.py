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
    python -m scrollwright serve               # localhost bridge for the extension
    python -m scrollwright serve --ui          # bridge + open the web UI in a browser
    python -m scrollwright ui                  # start the server if needed, open the UI,
                                                # and keep watching it (restarts if it dies)
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


def cmd_serve(args: list[str]) -> None:
    from scrollwright import server

    server.main(open_ui="--ui" in args)


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


def cmd_ui(args: list[str]) -> None:
    """Start the server if it isn't running, open the web UI in a browser, and keep
    watching it -- if the server dies, restart it and re-open the browser.

    One-command entry point for the local GUI: one terminal, one command, the UI
    stays up. Pass --no-open to skip the browser popup.
    """
    import os
    import select
    import subprocess
    import sys
    import threading
    import time

    from . import core

    PORT = int(os.environ.get("SCROLLWRIGHT_PORT", "8765"))
    HOST = "127.0.0.1"
    URL = f"http://{HOST}:{PORT}"
    open_browser = "--no-open" not in args

    def _port_open() -> bool:
        import socket
        try:
            s = socket.create_connection((HOST, PORT), timeout=1.0)
            s.close()
            return True
        except OSError:
            return False

    def _open_browser():
        import webbrowser
        for _ in range(30):  # up to ~3s
            if _port_open():
                webbrowser.open(URL, new=0, autoraise=True)
                return
            time.sleep(0.1)
        print(f"[ui] server didn't come up in time -- open {URL} manually", flush=True)

    server_proc_holder = [None]  # mutable so the watchdog + main both mutate the same object

    def _watchdog():
        while True:
            sp = server_proc_holder[0]
            if sp is not None and sp.poll() is not None:
                print("[ui] server exited unexpectedly -- restarting...", flush=True)
                server_proc_holder[0] = subprocess.Popen(
                    [sys.executable, "-m", "scrollwright", "serve"],
                    cwd=repo_root,
                    env=child_env,
                )
                _open_browser()
            time.sleep(3)

    # If a server is already running on the port, just open the UI and stay alive
    # (so the user has a terminal to watch logs in).
    if _port_open():
        print(f"[ui] server already running on {URL}", flush=True)
        if open_browser:
            import webbrowser
            webbrowser.open(URL, new=0, autoraise=True)
        print("[ui] press Ctrl+C to leave this terminal open", flush=True)
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            print("\n[ui] left running (server stays up)")
        return

    # Start the server as a child process so we can watch it.
    # The child must run from the repo root (parent of the scrollwright/ package)
    # so that `python -m scrollwright serve` can import the package.
    repo_root = str(core.PROJECT_ROOT.parent)
    child_env = os.environ.copy()
    child_env.setdefault("PYTHONPATH", repo_root)
    print(f"[ui] starting server on {URL}...", flush=True)
    server_proc_holder[0] = subprocess.Popen(
        [sys.executable, "-m", "scrollwright", "serve"],
        cwd=repo_root,
        env=child_env,
    )

    threading.Thread(target=_open_browser, daemon=True).start()
    threading.Thread(target=_watchdog, daemon=True).start()

    print("[ui] press Ctrl+C to stop", flush=True)
    try:
        try:
            poll = select.poll()
            poll.register(server_proc_holder[0].stdout, select.POLLIN)
            poll.register(server_proc_holder[0].stderr, select.POLLIN)
        except Exception:
            poll = None

        while server_proc_holder[0].poll() is None:
            if poll is not None:
                while True:
                    events = poll.poll(200)
                    if not events:
                        break
                    for fd, _ in events:
                        line = fd.readline()
                        if line:
                            sp = server_proc_holder[0]
                            stream = sys.stdout if fd is sp.stdout else sys.stderr
                            stream.write(line)
                            if stream is sys.stdout and hasattr(stream, "flush"):
                                stream.flush()
            else:
                time.sleep(0.2)
    except KeyboardInterrupt:
        print("\n[ui] stopping server...", flush=True)
    finally:
        sp = server_proc_holder[0]
        if sp is not None:
            try:
                sp.terminate()
                sp.wait(timeout=5)
            except Exception:
                try:
                    sp.kill()
                except Exception:
                    pass
        print("[ui] stopped")


COMMANDS = {"add": cmd_add, "process": cmd_process, "stats": cmd_stats, "retry": cmd_retry, "reprocess": cmd_reprocess, "serve": cmd_serve, "ui": cmd_ui}


def main() -> None:
    # Windows consoles default to legacy cp1252 — force UTF-8 so emoji/arrows
    # in our output never crash a run mid-command.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        print(__doc__)
        sys.exit(1)
    core.init_db()
    COMMANDS[sys.argv[1]](sys.argv[2:])


if __name__ == "__main__":
    main()
