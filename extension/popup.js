/** ScrollWright popup logic: status, vault path, note-this-reel, harvest. */

const $ = (id) => document.getElementById(id);
const logEl = $("log");

function log(text, cls = "") {
  const line = document.createElement("div");
  if (cls) line.className = cls;
  line.textContent = text;
  logEl.appendChild(line);
  logEl.scrollTop = logEl.scrollHeight;
}

function send(msg) {
  return new Promise((resolve) => {
    try {
      chrome.runtime.sendMessage(msg, (res) => {
        void chrome.runtime.lastError; // popup closed / no listener — resolve empty
        resolve(res || { ok: false, status: 0 });
      });
    } catch {
      resolve({ ok: false, status: 0 });
    }
  });
}

// Reel/post URL matcher used for both the active tab and harvested links.
const REEL_RE = /https:\/\/www\.instagram\.com\/(?:[\w.\-]+\/)?(?:reel|reels|p)\/[A-Za-z0-9_-]+/g;

async function activeTab() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  return tab;
}

async function ensureInstaTab() {
  const tab = await activeTab();
  if (!tab || !tab.url || !/https:\/\/www\.instagram\.com\//.test(tab.url)) {
    log("Open an Instagram tab first.", "err");
    return null;
  }
  return tab;
}

// ---- status + vault ---------------------------------------------------------

async function refreshStatus() {
  const st = await send({ type: "getStatus" });
  const on = st.ok && st.data && st.data.running;
  $("dot").classList.toggle("on", !!on);
  $("srv").textContent = on ? "" : "· server offline";
  $("noteThis").disabled = !on;
  $("harvest").disabled = !on;
  if (on && st.data.stats) {
    const s = st.data.stats;
    const total = Object.values(s).reduce((a, b) => a + b, 0);
    log(
      `server: ${total} reel(s) — pending ${s.pending || 0}, done ${s.done || 0}, failed ${s.failed || 0}` +
        (st.data.busy ? ` · working on ${st.data.current}…` : "")
    );
  }
}

async function loadVault() {
  const res = await send({ type: "getVault" });
  if (res.ok && res.data && res.data.path) $("vault").value = res.data.path;
}

$("vault").addEventListener("change", async () => {
  const p = $("vault").value.trim();
  if (!p) return;
  const res = await send({ type: "setVault", path: p });
  if (res.ok) {
    $("vault").value = res.data.path; // server may normalize it
    log(`Vault set ✓ notes → ${res.data.path}`, "ok");
  } else {
    log(res.data.error || "Could not set vault (server offline?).", "err");
    await loadVault(); // restore the real value
  }
});

// ---- note this reel -----------------------------------------------------------

$("noteThis").addEventListener("click", async () => {
  const tab = await ensureInstaTab();
  if (!tab) return;
  log("Reading this page…");
  let results;
  try {
    results = await chrome.scripting.executeScript({
      target: { tabId: tab.id },
      func: () => {
        const og = document.querySelector('meta[property="og:url"]');
        const links = [location.href, og ? og.content : ""].join("\n");
        const canon = document.querySelectorAll(
          'a[href*="/reel/"], a[href*="/p/"]'
        ).length;
        return { text: links, linksOnPage: canon };
      },
    });
  } catch {
    log("Couldn't read that tab (open a reel/post page).", "err");
    return;
  }
  const r = results && results[0] && results[0].result ? results[0].result : null;
  if (!r) {
    log("Couldn't read that tab.", "err");
    return;
  }
  const urls = [...new Set((r.text.match(REEL_RE) || []).map((u) => u + "/"))];
  if (urls.length === 0) {
    log("No reel/post URL on this page.", "err");
    return;
  }
  log(`Found: ${urls.join(", ")}`);
  const res = await send({ type: "addToLedger", text: urls.join("\n") });
  if (res.ok) {
    const d = res.data;
    log(
      d.added > 0
        ? `✓ Queued. Transcribing + writing the note now — check your vault in ~1 min.`
        : `Already in the ledger (${d.duplicates} duplicate). Reprocess via CLI if needed.`,
      d.added > 0 ? "ok" : ""
    );
  } else {
    log(res.data.error || "Server unreachable — run: python -m scrollwright serve", "err");
  }
  await refreshStatus();
});

// ---- harvest saved folder ------------------------------------------------------

$("harvest").addEventListener("click", async () => {
  const tab = await ensureInstaTab();
  if (!tab) return;
  if (!/\/saved\//.test(new URL(tab.url).pathname)) {
    log("Open your saved folder first (…/saved/all-posts/ or a collection).", "err");
    return;
  }
  log("Auto-scrolling… keep this tab open.");
  let results;
  try {
    results = await chrome.scripting.executeScript({
      target: { tabId: tab.id },
      func: async () => {
        const RE = /https:\/\/www\.instagram\.com\/(?:reel|p)\/[A-Za-z0-9_-]+\//;
        const found = new Set();
        let stall = 0;
        window.scrollTo(0, 0);
        await new Promise((r) => setTimeout(r, 800));
        while (stall < 8) {
          for (const a of document.querySelectorAll('a[href*="/reel/"], a[href*="/p/"]')) {
            const m = a.href.match(RE);
            if (m) found.add(m[0]);
          }
          window.scrollBy(0, 1200);
          await new Promise((r) => setTimeout(r, 450));
          stall = found.size === harvestLast ? stall + 1 : 0;
          var harvestLast = found.size;
        }
        return [...found].sort().join("\n");
      },
    });
  } catch {
    log("Couldn't scroll that tab.", "err");
    return;
  }
  const text = results && results[0] ? results[0].result || "" : "";
  if (!text) {
    log("Found 0 reels — scroll a bit manually, then retry.", "err");
    return;
  }
  const n = text.split("\n").length;
  const res = await send({ type: "addToLedger", text });
  if (res.ok) {
    log(
      `✓ ${n} reels: ${res.data.added} new, ${res.data.duplicates} dups. Processing in background.`,
      "ok"
    );
  } else {
    // Server offline → txt fallback so the trip isn't wasted.
    await send({ type: "downloadTxt", text });
    log(`Server offline — downloaded saved-reels.txt (${n} reels) instead.`, "err");
  }
  await refreshStatus();
});

// ---- boot ----------------------------------------------------------------------

loadVault();
refreshStatus();
const poll = setInterval(refreshStatus, 3000);
window.addEventListener("unload", () => clearInterval(poll));
