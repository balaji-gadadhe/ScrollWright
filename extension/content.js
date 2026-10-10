/**
 * ScrollWright — content script.
 * Adds a floating button on instagram.com. Its text/behavior follows the mode
 * set in the popup (chrome.storage.local.sw_mode):
 *   "note"   → 📝 Note this reel  (single reel on the current page)
 *   "scroll" → 📥 Scroll / harvest (auto-scroll saved folder)
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

  function postToServer(text, watch) {
    return new Promise((resolve) => {
      try {
        chrome.runtime.sendMessage({ type: "addToLedger", text, watch }, (res) => {
          void chrome.runtime.lastError;
          resolve(res || null);
        });
      } catch {
        resolve(null);
      }
    });
  }

  // Read the popup's "Note content" toggle (default: video ON).
  function getWatch() {
    return new Promise((resolve) => {
      try {
        chrome.storage.local.get("sw_watch", ({ sw_watch }) => {
          resolve(sw_watch !== false);
        });
      } catch {
        resolve(true);
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

  async function noteThisReel(btn) {
    // ONLY the current page's reel — never scrape every reel link on the
    // page. Instagram renders ~20 recommendation links per page; scraping
    // them all queued 21 reels from a single click.
    const found = new Set();
    if (REEL_RE.test(location.href)) found.add(location.href);
    try {
      const og = document.querySelector('meta[property="og:url"]');
      if (og && REEL_RE.test(og.content)) found.add(og.content);
    } catch (_) {}

    const urls = [...new Set([...found].map((u) => (u.endsWith("/") ? u : u + "/")))];
    if (urls.length === 0) {
      btn.textContent = "❌ no reel on this page";
      setTimeout(() => syncButtonText(btn), 3000);
      return;
    }
    btn.textContent = `📝 noting ${urls.length}…`;
    const res = await postToServer(urls.join("\n"), await getWatch());
    if (res && res.ok) {
      btn.textContent = res.data.added > 0
        ? `✅ ${res.data.added} queued → note in ~1 min`
        : `already in ledger (${res.data.duplicates} dups)`;
    } else {
      btn.textContent = "⚠ server off";
    }
    setTimeout(() => syncButtonText(btn), 5000);
  }

  async function harvest(btn) {
    if (!/\/saved\//.test(location.pathname)) {
      alert("ScrollWright: open your saved folder first,\ninstagram.com/<your-name>/saved/all-posts/\n\n(For a single reel, switch the popup to 📝 Note this reel.)");
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
      setTimeout(() => syncButtonText(btn), 3500);
      return;
    }

    btn.textContent = `🛰 sending ${found.size}…`;
    const res = await postToServer([...found].sort().join("\n"), await getWatch());
    if (res && res.ok) {
      btn.textContent = `✅ ${res.data.added} new, ${res.data.duplicates} dups → notes incoming`;
    } else {
      downloadTxt([...found].sort().join("\n") + "\n");
      btn.textContent = "⚠ server off — saved-reels.txt downloaded";
    }
    setTimeout(() => syncButtonText(btn), 6000);
  }

  function syncButtonText(btn) {
    chrome.storage.local.get("sw_mode", ({ sw_mode }) => {
      const mode = sw_mode || "note";
      if (mode === "scroll") {
        btn.textContent = "📥 Scroll / harvest";
        btn.onclick = () => harvest(btn);
      } else {
        btn.textContent = "📝 Note this reel";
        btn.onclick = () => noteThisReel(btn);
      }
    });
  }

  function makeButton() {
    const btn = document.createElement("button");
    btn.id = "scrollwright-export-btn";
    Object.assign(btn.style, {
      position: "fixed", right: "20px", bottom: "20px", zIndex: "99999",
      padding: "12px 18px", borderRadius: "24px", border: "none",
      background: "#6366f1", color: "#fff", fontWeight: "600",
      fontFamily: "system-ui, sans-serif", fontSize: "14px",
      cursor: "pointer", boxShadow: "0 4px 12px rgba(0,0,0,.35)",
    });
    chrome.storage.local.get("sw_mode", ({ sw_mode }) => {
      const mode = sw_mode || "note";
      if (mode === "scroll") {
        btn.textContent = "📥 Scroll / harvest";
        btn.onclick = () => harvest(btn);
      } else {
        btn.textContent = "📝 Note this reel";
        btn.onclick = () => noteThisReel(btn);
      }
    });
    chrome.runtime.onMessage.addListener((msg) => {
      if (msg.type === "setFloatMode") {
        syncButtonText(btn);
      }
    });
    document.body.appendChild(btn);
    return btn;
  }

  if (!document.getElementById("scrollwright-export-btn")) {
    makeButton();
  }

  let lastUrl = location.href;
  new MutationObserver(() => {
    if (location.href !== lastUrl) {
      lastUrl = location.href;
      if (!document.getElementById("scrollwright-export-btn")) makeButton();
    }
  }).observe(document.documentElement, { childList: true, subtree: true });
})();
