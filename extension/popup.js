/** ScrollWright popup — launcher + light control panel.

- "Start ScrollWright" → opens the web UI (http://127.0.0.1:8765) in a new tab.
  If the server isn't running, the web UI won't load and the popup tells you
  to run `python -m scrollwright ui` once.
- Mode toggle (📝 Note this reel / 📥 Scroll harvest) — stored in
  chrome.storage.local; the floating button on instagram.com follows it.
- Status line: idle / processing <shortcode> / server offline, plus counts
  (pending / processing / done / failed).
- Note this reel / Harvest buttons (enabled when server is online) do the same
  things as before — they just respect the current mode (only the matching one
  is enabled).
- Light styling to match the web UI.
*/

// Accepts both "log" and "#log" — getElementById wants the raw id (no #),
// but call sites use the jQuery-style "#id" form. Stripping here fixes all
// call sites at once.
const $ = (id) => document.getElementById(String(id).replace(/^#/, ""));
const logEl = $("#log");

const MODES = { NOTE: "note", SCROLL: "scroll" };
let currentMode = MODES.NOTE;

// Per-reel video toggle: true = Gemini watches the video too, false =
// transcript+caption only. Stored in chrome.storage.local.sw_watch
// (default true = current WATCH_VIDEOS behavior).
let watchVideo = true;

// Notes folder the server reports (value = <vault>/ScrollWright). The
// configure dialog edits the vault ROOT, so vaultRoot() strips the trailing
// ScrollWright when prefilling.
let currentVaultPath = "";

function log(text, cls = "") {
  const line = document.createElement("div");
  if (cls) line.className = cls;
  line.textContent = text;
  logEl.appendChild(line);
  logEl.scrollTop = logEl.scrollHeight;
  // keep last ~40 lines
  while (logEl.children.length > 40) logEl.removeChild(logEl.firstChild);
}

function send(msg) {
  return new Promise((resolve) => {
    try {
      chrome.runtime.sendMessage(msg, (res) => {
        void chrome.runtime.lastError;
        resolve(res || { ok: false, status: 0, data: {} });
      });
    } catch {
      resolve({ ok: false, status: 0, data: {} });
    }
  });
}

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

// ---- mode toggle ----

async function loadMode() {
  const { mode } = await chrome.storage.local.get("sw_mode");
  if (mode === MODES.SCROLL) setMode(MODES.SCROLL);
  else setMode(MODES.NOTE);
}

function setMode(mode) {
  currentMode = mode;
  $("#modeNote").classList.toggle("active", mode === MODES.NOTE);
  $("#modeScroll").classList.toggle("active", mode === MODES.SCROLL);
  try { chrome.storage.local.set({ sw_mode: mode }); } catch {}
  // floating button on insta follows
  chrome.runtime.sendMessage({ type: "setFloatMode", mode });
  // enable/disble the matching popup button
  $("#noteThis").disabled = mode !== MODES.NOTE;
  $("#harvest").disabled = mode !== MODES.SCROLL;
}

$("#modeNote").addEventListener("click", () => setMode(MODES.NOTE));
$("#modeScroll").addEventListener("click", () => setMode(MODES.SCROLL));

// ---- video toggle (Note content: video+transcript vs transcript only) ----

function renderWatchToggle() {
  $("#watchVideo").classList.toggle("active", watchVideo);
  $("#watchText").classList.toggle("active", !watchVideo);
}

function setWatch(v) {
  watchVideo = v;
  renderWatchToggle();
  chrome.storage.local.set({ sw_watch: v });
}

async function loadWatch() {
  try {
    const { sw_watch } = await chrome.storage.local.get("sw_watch");
    watchVideo = sw_watch !== false; // default true
  } catch { watchVideo = true; }
  renderWatchToggle();
}

$("#watchVideo").addEventListener("click", () => setWatch(true));
$("#watchText").addEventListener("click", () => setWatch(false));

// ---- configurable paths (others: edit these, or use ⚙ Configure paths) ----

// The folder that contains the ScrollWright project (where you run
// `python -m scrollwriter ui` from).
//
// We derive it from the server's /projectRoot endpoint (preferred) or from the
// vault path the server last told us (fallback). Hardcoded here only as a last-
// resort default and to make the popup usable even when the server is down on
// first load (the server endpoint is unreachable before the first run).
//
// OTHERS: change DEFAULT_PROJECT_ROOT and PYTHON_BIN below to your own paths,
// or use the ⚙ Configure paths button (it saves to chrome.storage.local so you
// don't have to edit the file).
const DEFAULT_PROJECT_ROOT = "C:/Users/you/Code/ScrollWright";

// Full path to the python binary that can run `python -m scrollwright ui`.
// Using the full path avoids the Windows "python is from the Store" alias issue.
// OTHERS: point this at your python.exe (a full path avoids the Windows
// "python is from the Store" alias issue). If you change this, the command the
// popup copies changes with it.
const PYTHON_BIN = "python";

const STORAGE_KEY_PROJECT_ROOT = "sw_project_root";
const STORAGE_KEY_PYTHON_BIN = "sw_python_bin";

// Cached user overrides (filled at boot) so the configure dialog can prefill
// synchronously — chrome.storage is async-only, but the dialog must open the
// moment the button is clicked.
let storedProjectRoot = "";
let storedPythonBin = "";

// /vault returns the NOTES dir (<vault>/ScrollWright); the dialog edits the
// vault root, so strip the trailing ScrollWright before prefilling.
function vaultRoot(notesDir) {
  return String(notesDir || "").replace(/\\/g, "/").replace(/\/ScrollWright\/?$/, "");
}

async function projectRoot() {
  // User override (set via ⚙ Configure paths).
  try {
    const stored = await chrome.storage.local.get(STORAGE_KEY_PROJECT_ROOT);
    if (stored && stored[STORAGE_KEY_PROJECT_ROOT]) {
      return String(stored[STORAGE_KEY_PROJECT_ROOT]).replace(/\\/g, "/").replace(/\/+$/, "");
    }
  } catch {}

  // Ask the server for its own project root. That endpoint is reachable only
  // when the server is running; when it isn't, fall back to the vault-parent
  // heuristic (correct whenever the client has a working vault already configured).
  const fetched = await send({ type: "getProjectRoot" });
  if (fetched && fetched.ok && fetched.data && fetched.data.path) {
    return String(fetched.data.path).replace(/\\/g, "/").replace(/\/+$/, "");
  }
  // Fallback heuristic: parent of the vault path the server last told us.
  try {
    const vault = await send({ type: "getVault" });
    if (vault && vault.ok && vault.data && vault.data.path) {
      // vault is <project>/ScrollWright/notes ; drop the trailing two dirs.
      let p = String(vault.data.path).replace(/\\/g, "/").replace(/\/+$/, "");
      const parts = p.split("/").filter(Boolean);
      if (parts.length >= 2) {
        parts.pop(); // notes
        parts.pop(); // ScrollWright
        return parts.join("/");
      }
    }
  } catch {}
  // Absolute last resort — the path is hardcoded for this machine.
  return DEFAULT_PROJECT_ROOT.replace(/\\/g, "/");
}

function pythonBin() {
  try {
    const stored = chrome.storage.local.get(STORAGE_KEY_PYTHON_BIN);
    // chrome.storage.local.get returns a promise when given a string key.
    return stored.then((s) => {
      if (s && s[STORAGE_KEY_PYTHON_BIN]) return String(s[STORAGE_KEY_PYTHON_BIN]);
      return PYTHON_BIN;
    });
  } catch {
    return Promise.resolve(PYTHON_BIN);
  }
}

function copyPathToClipboard(path) {
  return new Promise((resolve) => {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard
        .writeText(path)
        .then(() => resolve(true))
        .catch(() => resolve(false));
    } else {
      resolve(false);
    }
  });
}

function showPathInstructions(path, which) {
  // Windows PowerShell 5.1 syntax (the default Windows terminal):
  // ';' chains statements (not '&&'), and '&' runs a quoted executable.
  const cmd = `cd "${path}"; & "${PYTHON_BIN}" -m scrollwright ui`;
  log(
    `${which} command copied to clipboard: ${cmd}`,
    "ok"
  );
}

$("#startBtn").addEventListener("click", async () => {
  try {
    const root = await projectRoot();
    const python = await pythonBin();
    // PowerShell 5.1-compatible: ';' to chain (not bash '&&'), '&' call
    // operator so the quoted python.exe actually executes instead of being
    // parsed as a string expression.
    const cmd = `cd "${root}"; & "${python}" -m scrollwright ui`;

    // Render the code block with copy button FIRST — before risky calls
    // (window.open can throw in Edge's popup; we don't want that to block
    // the command from appearing).
    const oldBlock = document.getElementById("startCmdBlock");
    if (oldBlock) oldBlock.remove();

    const block = document.createElement("div");
    block.id = "startCmdBlock";
    block.style.cssText = `margin-top:8px;background:#0b1120;border:1px solid #1f2937;border-radius:8px;padding:8px 10px;display:flex;gap:8px;align-items:stretch;font-family:ui-monospace,Consolas,"SF Mono",Menlo,monospace;font-size:11.5px;color:#e5e7eb;overflow:hidden`;

    const code = document.createElement("div");
    code.style.cssText = `color:#a5b4fc;white-space:pre-wrap;word-break:break-word;line-height:1.5;flex:1`;
    code.textContent = cmd;

    const copyBtn = document.createElement("button");
    copyBtn.textContent = "Copy";
    copyBtn.style.cssText = `background:#6366f1;color:#fff;border:none;border-radius:6px;padding:5px 12px;font-weight:600;font-size:11.5px;cursor:pointer;font-family:system-ui,sans-serif;white-space:nowrap`;
    copyBtn.addEventListener("click", async () => {
      const ok = await copyPathToClipboard(cmd);
      copyBtn.textContent = ok ? "Copied!" : "Copy failed";
      copyBtn.style.background = ok ? "#16a34a" : "#dc2626";
      setTimeout(() => { copyBtn.textContent = "Copy"; copyBtn.style.background = "#6366f1"; }, 1500);
    });

    block.appendChild(code);
    block.appendChild(copyBtn);
    $("startBtn").after(block);

    // Check the server BEFORE opening anything. Opening a tab closes this
    // popup (focus loss), so we must never open the UI while the server is
    // down — the user would land on an error page and never see our command.
    let on = false;
    try {
      const st = await send({ type: "getStatus" });
      on = !!(st.ok && st.data && st.data.running);
    } catch { on = false; }

    if (on) {
      // Server is up — the UI will load, safe to open a tab.
      try { window.open("http://127.0.0.1:8765", "_blank"); } catch {}
      log("Server is up. Web UI opened — add reels there or on Instagram.", "ok");
    } else {
      // Server is DOWN — do NOT open a tab. Keep the popup alive so the user
      // can read + copy the command above.
      log("Server not running — run the command above in a terminal, then click ▶ Start again.", "err");
    }

    await refreshStatus();
  } catch (e) {
    log("Error: " + (e && e.message ? e.message : e), "err");
  }
});

// ---- configure paths (for others: set your own project folder + python) ----

function openConfigureDialog() {
  // Build a small inline modal with the current values.
  const currentRoot = storedProjectRoot || DEFAULT_PROJECT_ROOT;
  const currentPython = storedPythonBin || PYTHON_BIN;

  const overlay = document.createElement("div");
  overlay.style.cssText = `
    position: fixed; inset: 0; background: rgba(0,0,0,.35); z-index: 99999;
    display: flex; align-items: center; justify-content: center;
  `;

  const dialog = document.createElement("div");
  dialog.style.cssText = `
    background: #fff; border-radius: 10px; padding: 14px; width: 340px;
    font-family: system-ui, -apple-system, Segoe UI, Roboto, sans-serif;
    font-size: 13px; color: #1f2937; box-shadow: 0 8px 24px rgba(0,0,0,.3);
  `;

  const title = document.createElement("div");
  title.style.cssText = "font-size:14px;font-weight:700;margin-bottom:10px";
  title.textContent = "Configure ScrollWright paths";

  const hint = document.createElement("div");
  hint.style.cssText = "font-size:11px;color:#6b7280;margin-bottom:10px;line-height:1.5";
  hint.innerHTML = `Paths saved in this browser (chrome.storage.local); the notes folder is written to the project <strong>.env</strong>. Notes → &lt;vault&gt;/ScrollWright/.`;

  const rootLabel = document.createElement("label");
  rootLabel.style.cssText = "display:block;font-size:11px;color:#6b7280;margin-bottom:4px";
  rootLabel.textContent = "Project folder (where you run python -m scrollwright ui):";

  const rootInput = document.createElement("input");
  rootInput.type = "text";
  rootInput.value = currentRoot;
  rootInput.style.cssText = "width:100%;padding:7px 9px;border-radius:6px;border:1px solid #d1d5db;font-size:12px;margin-bottom:10px";
  rootInput.setAttribute("spellcheck", "false");

  const pyLabel = document.createElement("label");
  pyLabel.style.cssText = "display:block;font-size:11px;color:#6b7280;margin-bottom:4px";
  pyLabel.textContent = "Python binary (full path, or just 'python' if on PATH):";

  const pyInput = document.createElement("input");
  pyInput.type = "text";
  pyInput.value = currentPython;
  pyInput.style.cssText = "width:100%;padding:7px 9px;border-radius:6px;border:1px solid #d1d5db;font-size:12px;margin-bottom:12px";
  pyInput.setAttribute("spellcheck", "false");

  const vaultLabel = document.createElement("label");
  vaultLabel.style.cssText = "display:block;font-size:11px;color:#6b7280;margin-bottom:4px";
  vaultLabel.textContent = "Notes folder (your Obsidian vault root):";

  const vaultInput = document.createElement("input");
  vaultInput.type = "text";
  vaultInput.value = vaultRoot(currentVaultPath);
  vaultInput.placeholder = "C:/path/to/vault";
  vaultInput.style.cssText = "width:100%;padding:7px 9px;border-radius:6px;border:1px solid #d1d5db;font-size:12px;margin-bottom:12px";
  vaultInput.setAttribute("spellcheck", "false");

  const saveBtn = document.createElement("button");
  saveBtn.className = "btn-primary";
  saveBtn.style.marginBottom = "6px";
  saveBtn.textContent = "Save & close";

  const cancelBtn = document.createElement("button");
  cancelBtn.className = "btn-secondary";
  cancelBtn.textContent = "Cancel";

  dialog.appendChild(title);
  dialog.appendChild(hint);
  dialog.appendChild(rootLabel);
  dialog.appendChild(rootInput);
  dialog.appendChild(pyLabel);
  dialog.appendChild(pyInput);
  dialog.appendChild(vaultLabel);
  dialog.appendChild(vaultInput);
  dialog.appendChild(saveBtn);
  dialog.appendChild(cancelBtn);
  overlay.appendChild(dialog);
  document.body.appendChild(overlay);

  function close() {
    document.body.removeChild(overlay);
  }

  cancelBtn.addEventListener("click", close);
  overlay.addEventListener("click", (e) => { if (e.target === overlay) close(); });

  saveBtn.addEventListener("click", () => {
    const newRoot = rootInput.value.trim();
    const newPython = pyInput.value.trim();
    if (!newRoot) {
      rootInput.style.borderColor = "#dc2626";
      rootInput.focus();
      return;
    }
    // Save must never prevent the dialog from closing (chrome.storage throws
    // if the "storage" permission is missing — always close regardless).
    try {
      chrome.storage.local.set({
        [STORAGE_KEY_PROJECT_ROOT]: newRoot,
        [STORAGE_KEY_PYTHON_BIN]: newPython,
      });
      storedProjectRoot = newRoot;
      storedPythonBin = newPython;
      log(`Paths saved — ▶ Start ScrollWright will use them now.`, "ok");
    } catch (e) {
      log("Could not save paths: " + (e && e.message ? e.message : e), "err");
    }
    close();
    // The vault lives in the project .env (server-side), not browser storage.
    // Sent after close() so a slow/offline server never blocks the dialog.
    const newVault = vaultInput.value.trim();
    if (newVault && newVault !== vaultRoot(currentVaultPath)) {
      send({ type: "setVault", path: newVault }).then((res) => {
        if (res.ok && res.data && res.data.path) {
          currentVaultPath = res.data.path;
          log("Notes folder → " + newVault, "ok");
        } else {
          log((res.data && res.data.error) || "Vault folder not found", "err");
        }
      });
    }
  });

  setTimeout(() => rootInput.focus(), 0);
}

$("#configureBtn").addEventListener("click", openConfigureDialog);

// Load user overrides into the UI (show them in a log line on boot).
(async () => {
  try {
    const stored = await chrome.storage.local.get([STORAGE_KEY_PROJECT_ROOT, STORAGE_KEY_PYTHON_BIN]);
    storedProjectRoot = (stored && stored[STORAGE_KEY_PROJECT_ROOT]) || "";
    storedPythonBin = (stored && stored[STORAGE_KEY_PYTHON_BIN]) || "";
    if (stored && (stored[STORAGE_KEY_PROJECT_ROOT] || stored[STORAGE_KEY_PYTHON_BIN])) {
      const r = stored[STORAGE_KEY_PROJECT_ROOT] ? `project=${stored[STORAGE_KEY_PROJECT_ROOT]}` : "";
      const p = stored[STORAGE_KEY_PYTHON_BIN] ? `python=${stored[STORAGE_KEY_PYTHON_BIN]}` : "";
      const parts = [r, p].filter(Boolean).join(", ");
      if (parts) log(`Configured: ${parts}`, "muted");
    }
  } catch {}
})();

// ---- status + counts ----

async function refreshStatus() {
  const st = await send({ type: "getStatus" });
  const on = st.ok && st.data && st.data.running;
  $("#dot").classList.toggle("on", !!on);
  const srv = $("#srv");
  if (on) {
    srv.textContent = "· running";
    srv.className = "muted";
  } else {
    srv.textContent = "· server offline";
    srv.className = "muted";
  }
  const counts = $("#counts");
  if (on && st.data && st.data.stats) {
    const s = st.data.stats;
    const total = (s.pending || 0) + (s.processing || 0) + (s.done || 0) + (s.failed || 0);
    counts.innerHTML =
      `<span class="pending">pending ${s.pending || 0}</span>` +
      `<span class="processing">doing ${s.processing || 0}</span>` +
      `<span class="done">done ${s.done || 0}</span>` +
      `<span class="failed">failed ${s.failed || 0}</span>` +
      `<span style="color:#9ca3af">· ${total}</span>`;
    $("#statusText").textContent = st.data.busy ? `working on ${st.data.current || "?"}…` : "idle";
  } else {
    counts.innerHTML = "";
    $("#statusText").textContent = "server offline";
  }
  // enable/disable action buttons
  $("#noteThis").disabled = !(on && currentMode === MODES.NOTE);
  $("#harvest").disabled = !(on && currentMode === MODES.SCROLL);
}

// ---- vault (keep existing) ----

async function loadVault() {
  const res = await send({ type: "getVault" });
  if (res.ok && res.data && res.data.path) currentVaultPath = res.data.path;
}

// ---- note this reel (only in note mode) ----

$("#noteThis").addEventListener("click", async () => {
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
        return { text: links };
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
  const res = await send({ type: "addToLedger", text: urls.join("\n"), watch: watchVideo });
  if (res.ok) {
    const d = res.data;
    log(
      d.added > 0
        ? `✓ Queued. Note lands in ~1 min. Check the web UI.`
        : `Already in the ledger (${d.duplicates} duplicate).`,
      d.added > 0 ? "ok" : ""
    );
  } else {
    log(res.data.error || "Server unreachable — run: python -m scrollwright ui", "err");
  }
  await refreshStatus();
});

// ---- harvest saved folder (only in scroll mode) ----

$("#harvest").addEventListener("click", async () => {
  const tab = await ensureInstaTab();
  if (!tab) return;
  if (currentMode !== MODES.SCROLL) {
    log("Switch the floating button to 📥 Scroll / harvest mode first.", "err");
    return;
  }
  if (!/\/saved\//.test(new URL(tab.url).pathname)) {
    log("Open your saved folder first (…/saved/all-posts/ or a collection).", "err");
    return;
  }
  log("Auto-scrolling… keep that tab open.");
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
  // Harvest = bulk queue; pass the toggle too so bulk reels respect it.
  const res = await send({ type: "addToLedger", text, watch: watchVideo });
  if (res.ok) {
    log(
      `✓ ${n} reels: ${res.data.added} new, ${res.data.duplicates} dups. Processing in background.`,
      "ok"
    );
  } else {
    await send({ type: "downloadTxt", text });
    log(`Server offline — downloaded saved-reels.txt (${n} reels) instead.`, "err");
  }
  await refreshStatus();
});

// ---- boot ----
// Only start the status poll when this script is running as the real popup
// (not when it's being smoke-tested in Node). The smoke test imports this
// file via vm.runInContext but never defines window.chrome.runtime.id, so we
// gate the poll behind that check.
if (window && window.chrome && window.chrome.runtime && window.chrome.runtime.id) {
  loadVault();
  loadMode();
  loadWatch();
  refreshStatus();
  const poll = setInterval(refreshStatus, 3000);
  window.addEventListener("unload", () => clearInterval(poll));
}
