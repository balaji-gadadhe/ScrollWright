/**
 * ScrollWright Exporter — content script.
 * Adds a floating button on instagram.com. On a saved folder it auto-scrolls,
 * collects every reel/post URL, and POSTs them to the local ScrollWright
 * server (python -m scrollwright serve) which dedupes + writes notes.
 * Falls back to a txt download if the server isn't running.
 */
(function () {
  "use strict";

  const REEL_RE = /https:\/\/www\.instagram\.com\/(?:reel|p)\/[A-Za-z0-9_-]+\//;

  function sleep(ms) {
    return new Promise((r) => setTimeout(r, ms));
  }

  function collectUrls(found) {
    for (const a of document.querySelectorAll('a[href*="/reel/"], a[href*="/p/"]')) {
      const m = a.href.match(REEL_RE);
      if (m) found.add(m[0]);
    }
  }

  // Routed through the background service worker — a public https page
  // fetching http://127.0.0.1 directly can trip Private Network Access rules.
  function postToServer(text) {
    return new Promise((resolve) => {
      try {
        chrome.runtime.sendMessage({ type: "addToLedger", text }, (res) => {
          void chrome.runtime.lastError;
          resolve(res || null);
        });
      } catch {
        resolve(null);
      }
    });
  }

  function downloadTxt(text) {
    const url = URL.createObjectURL(new Blob([text], { type: "text/plain" }));
    const a = document.createElement("a");
    a.href = url;
    a.download = "saved-reels.txt";
    a.click();
  }

  async function harvest(btn) {
    if (!/\/saved\//.test(location.pathname)) {
      alert("ScrollWright: open your saved folder first, e.g.\ninstagram.com/<your-name>/saved/all-posts/\n\n(For a single reel, use the extension popup → Note this reel.)");
      return;
    }
    const found = new Set();
    let stallCount = 0;
    let lastCount = 0;
    let stopped = false;

    btn.dataset.stopped = "false";
    btn.addEventListener("click", () => { btn.dataset.stopped = "true"; }, { once: true });

    window.scrollTo(0, 0);
    await sleep(800);

    while (!stopped && stallCount < 8) {
      collectUrls(found);
      window.scrollBy(0, 1200);
      await sleep(450);
      if (found.size === lastCount) stallCount++; else stallCount = 0;
      lastCount = found.size;
      btn.textContent = `⏳ ${found.size} reels… (click to stop)`;
      stopped = btn.dataset.stopped === "true";
    }

    if (found.size === 0) {
      btn.textContent = "❌ none found — scroll a bit then retry";
      setTimeout(() => (btn.textContent = "📥 Export saved reels"), 3500);
      return;
    }

    btn.textContent = `🛰 sending ${found.size}…`;
    const res = await postToServer([...found].sort().join("\n"));
    if (res && res.ok) {
      btn.textContent = `✅ ${res.data.added} new, ${res.data.duplicates} dups → notes incoming`;
    } else {
      downloadTxt([...found].sort().join("\n") + "\n");
      btn.textContent = "⚠ server off — saved-reels.txt downloaded";
    }
    setTimeout(() => (btn.textContent = "📥 Export saved reels"), 6000);
  }

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
    btn.addEventListener("click", () => harvest(btn));
    document.body.appendChild(btn);
    return btn;
  }

  if (!document.getElementById("scrollwright-export-btn")) {
    makeButton();
  }

  // SPA navigation: Instagram swaps pages without reloading, so re-check
  // for our button on every URL change.
  let lastUrl = location.href;
  new MutationObserver(() => {
    if (location.href !== lastUrl) {
      lastUrl = location.href;
      if (!document.getElementById("scrollwright-export-btn")) makeButton();
    }
  }).observe(document.documentElement, { childList: true, subtree: true });
})();
