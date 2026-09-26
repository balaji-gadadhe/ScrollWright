/**
 * ScrollWright Exporter — content script.
 * Adds a floating button on instagram.com. When on a saved-posts page it
 * auto-scrolls, collects every reel/post URL, and downloads them as a txt
 * that you feed straight into:  python -m scrollwright add <file>
 */
(function () {
  "use strict";

  const REEL_RE = /https:\/\/www\.instagram\.com\/(?:reel|p)\/[A-Za-z0-9_-]+\/?/g;

  function makeButton() {
    const btn = document.createElement("button");
    btn.id = "scrollwright-export-btn";
    btn.textContent = "📥 Export saved reels";
    Object.assign(btn.style, {
      position: "fixed", right: "20px", bottom: "20px", zIndex: "99999",
      padding: "12px 18px", borderRadius: "24px", border: "none",
      background: "#8b5cf6", color: "#fff", fontWeight: "600",
      fontFamily: "system-ui, sans-serif", fontSize: "14px",
      cursor: "pointer", boxShadow: "0 4px 12px rgba(0,0,0,.35)",
    });
    document.body.appendChild(btn);
    return btn;
  }

  function setHud(btn, text) {
    btn.textContent = text;
  }

  function sleep(ms) {
    return new Promise((r) => setTimeout(r, ms));
  }

  function collectUrls(found) {
    // Grid items are anchors; scanning hrefs is much cheaper than full HTML.
    for (const a of document.querySelectorAll('a[href*="/reel/"], a[href*="/p/"]')) {
      const m = a.href.match(/https:\/\/www\.instagram\.com\/(?:reel|p)\/[A-Za-z0-9_-]+\//);
      if (m) found.add(m[0]);
    }
  }

  async function harvest(btn) {
    if (!/\/saved\//.test(location.pathname)) {
      alert("ScrollWright: open your saved folder first, e.g.\ninstagram.com/<your-name>/saved/all-posts/");
      return;
    }
    const found = new Set();
    let stallCount = 0;
    let lastCount = 0;
    let stopped = false;

    btn.dataset.stopped = "false";
    btn.addEventListener("click", () => { btn.dataset.stopped = "true"; }, { once: true });

    // Jump to top so we scroll the whole collection
    window.scrollTo(0, 0);
    await sleep(800);

    while (!stopped && stallCount < 8) {
      collectUrls(found);
      window.scrollBy(0, 1200);
      await sleep(450);
      if (found.size === lastCount) stallCount++; else stallCount = 0;
      lastCount = found.size;
      setHud(btn, `⏳ ${found.size} reels… (click to stop)`);
      stopped = btn.dataset.stopped === "true";
    }

    if (found.size === 0) {
      setHud(btn, "❌ none found — scroll a bit manually then retry");
      setTimeout(() => setHud(btn, "📥 Export saved reels"), 3500);
      return;
    }

    const text = [...found].sort().join("\n") + "\n";
    const blob = new Blob([text], { type: "text/plain" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = "saved-reels.txt";
    a.click();
    setHud(btn, `✅ ${found.size} reels → saved-reels.txt`);
    setTimeout(() => setHud(btn, "📥 Export saved reels"), 5000);
  }

  // Only inject the button once
  if (!document.getElementById("scrollwright-export-btn")) {
    const btn = makeButton();
    btn.addEventListener("click", () => harvest(btn), { once: false });
  }
})();
