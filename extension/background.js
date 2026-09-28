/**
 * ScrollWright background service worker.
 * Talks to the local Python server (127.0.0.1:8765), bridges messages between
 * the popup and Instagram tabs, and falls back to txt download when offline.
 */

const SERVER = "http://127.0.0.1:8765";

async function serverFetch(path, opts = {}) {
  try {
    const res = await fetch(SERVER + path, opts);
    return { ok: res.ok, status: res.status, data: await res.json().catch(() => ({})) };
  } catch {
    return { ok: false, status: 0, data: {} }; // server not running
  }
}

// ---- message bridge -------------------------------------------------------
chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  (async () => {
    if (msg.type === "addToLedger") {
      sendResponse(await serverFetch("/add", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text: msg.text }),
      }));
    } else if (msg.type === "getStatus") {
      sendResponse(await serverFetch("/status"));
    } else if (msg.type === "getVault") {
      sendResponse(await serverFetch("/vault"));
    } else if (msg.type === "setVault") {
      sendResponse(await serverFetch("/vault", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ path: msg.path }),
      }));
    } else if (msg.type === "downloadTxt") {
      const url = URL.createObjectURL(new Blob([msg.text], { type: "text/plain" }));
      const dl = chrome.downloads
        ? await chrome.downloads.download({ url, filename: "saved-reels.txt", saveAs: false })
        : null;
      sendResponse({ ok: true, downloadId: dl });
    }
  })();
  return true; // async sendResponse
});
