const vscode = require("vscode");
const path = require("path");
const fs = require("fs");
const cp = require("child_process");
const os = require("os");
const https = require("https");

// ╔══════════════════════════════════════════════════════════════════════════╗
// ║  🚫 VERSION PROTECTION: Do NOT bump any version number (package.json,   ║
// ║     patcher_version.txt, stable_version.txt, STABLE_CLAUDE_VERSION, or  ║
// ║     any other version pin) without Jacob's explicit permission.         ║
// ║     Rebuilding the VSIX for local testing does NOT require a bump.      ║
// ╚══════════════════════════════════════════════════════════════════════════╝

const STOCK_ID = "anthropic.claude-code";
// ╔══════════════════════════════════════════════════════════════════════════╗
// ║  STABLE VERSION: The canonical source of truth is STABLE_VERSION.txt   ║
// ║  shipped inside this VSIX. Do NOT modify the hardcoded fallback below  ║
// ║  without explicit permission — update the .txt file instead.           ║
// ║  Stable mode never uses OTA; it uses the bundled stable/ folder only.  ║
// ╚══════════════════════════════════════════════════════════════════════════╝
// Hardcoded fallback — only used if the bundled STABLE_VERSION.txt is
// somehow missing or unreadable. The OTA txt is the source of truth.
const STABLE_CLAUDE_VERSION_FALLBACK = "2.1.152";

// OTA: normal Enable fetches the latest working patcher from this public repo.
// Stable mode does not use OTA; it runs the bundled production stable/ files.
const OTA_BASE = "https://raw.githubusercontent.com/Lunarwerx/claude-code-orbit/main";
const OTA_PATCHER_URL = OTA_BASE + "/Claude%20Code/patch_claude_vsix_v147.py";
const OTA_STABLE_VERSION_URL = OTA_BASE + "/stable/stable_version.txt";
const OTA_WRAPPER_VERSION_URL = OTA_BASE + "/wrapper_version.txt";
const OTA_WRAPPER_VSIX_URL = OTA_BASE + "/latest/claude-code-orbit.vsix";
const OTA_TIMEOUT_MS = 8000;

// VS Code Marketplace gallery query — the SAME endpoint the patcher uses to
// find/download Claude Code. We POST a by-name filter and read the newest
// published version so Orbit can detect "a newer Claude Code exists" even when
// our own patcher hasn't changed (the patcher is version-agnostic and re-applies
// to whatever's newest).
const MARKETPLACE_QUERY_URL =
  "https://marketplace.visualstudio.com/_apis/public/gallery/extensionquery?api-version=7.2-preview.1";

// OTA patcher-version pin — separate from stable_version.txt (which pins the
// Claude Code version). This file contains just the patcher version (e.g. "1.1.4").
// Orbit polls this in the background so users get notified the moment a new
// patcher lands on GitHub — no VSIX reinstall needed.
const OTA_PATCHER_VERSION_URL = OTA_BASE + "/patcher_version.txt";

// OTA rollback registry — patchers/manifest.json lists every archived patcher
// version + the Claude Code version each was verified against. Backs the "Use
// previous version" button: a rollback re-installs an archived patcher pinned to
// its recorded Claude, so it's always a known-good (patcher, Claude) pair — never
// an old patcher fired blind at whatever Claude is installed. Patcher files live
// beside it at /patchers/<file>. Writer: tools/archive_patcher.py.
const OTA_PATCHERS_BASE = OTA_BASE + "/patchers";
const OTA_PATCHERS_MANIFEST_URL = OTA_PATCHERS_BASE + "/manifest.json";

// Background polling: how often to check GitHub for a new patcher version.
const POLL_INTERVAL_MS = 4 * 60 * 60 * 1000; // 4 hours
const STARTUP_DELAY_MS = 30 * 1000;           // wait 30s before first check

// globalState keys for cross-session persistence of the remote version and
// notification deduplication.
const GS_REMOTE_PATCHER_VERSION = "claudeCodeOrbit.remotePatcherVersion";
const GS_LAST_NOTIFIED_VERSION = "claudeCodeOrbit.lastNotifiedVersion";
// Newest Claude Code version available on the Marketplace (cached by the poller
// so the synchronous detectState() can compare without a network call), plus
// dedup of the "new Claude Code" notification.
const GS_LATEST_CLAUDE_VERSION = "claudeCodeOrbit.latestClaudeVersion";
const GS_LAST_NOTIFIED_CLAUDE = "claudeCodeOrbit.lastNotifiedClaudeVersion";
// Cached rollback registry (patchers/manifest.json), refreshed by the poller so
// the synchronous detectState() can offer "Use previous version" with no network.
const GS_PATCHER_MANIFEST = "claudeCodeOrbit.patcherManifest";

function activate(context) {
  const provider = new SidebarProvider(context);
  context.subscriptions.push(
    vscode.window.registerWebviewViewProvider("claudeCodeOrbit.sidebar", provider, {
      webviewOptions: { retainContextWhenHidden: true },
    })
  );
  // Re-detect Claude Code whenever the user installs/uninstalls any extension,
  // so manual install/uninstall is reflected without a window reload.
  context.subscriptions.push(
    vscode.extensions.onDidChange(() => provider.pushState())
  );

  // --- Status bar item ---
  // Shows a subtle indicator in the bottom bar. When an update is available
  // it turns into a highlighted "Orbit Update" badge; when everything is
  // current it shows a quiet checkmark. Click opens the Orbit sidebar.
  const statusBarItem = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Right, 99);
  statusBarItem.command = "claudeCodeOrbit.focusSidebar";
  statusBarItem.tooltip = "Claude Code Orbit";
  statusBarItem.hide();
  context.subscriptions.push(statusBarItem);

  // Register a command so the status bar item can open the Orbit sidebar.
  context.subscriptions.push(
    vscode.commands.registerCommand("claudeCodeOrbit.focusSidebar", () => {
      try { vscode.commands.executeCommand("workbench.view.extension.claudeCodeOrbit"); } catch (_) {}
    })
  );

  // --- Background patcher-version polling ---
  startBackgroundPolling(context, provider, statusBarItem);
}

// ---------------------------------------------------------------------------
//  Background patcher-version polling
// ---------------------------------------------------------------------------

/**
 * Fetch the latest patcher version from the OTA repo on GitHub.
 * Returns the version string on success, or null on any failure (offline,
 * GitHub down, file not found). This is intentionally silent — the caller
 * handles user-visible state.
 */
async function fetchRemotePatcherVersion(log) {
  try {
    const body = await httpsGet(OTA_PATCHER_VERSION_URL + "?t=" + Date.now(), OTA_TIMEOUT_MS);
    const v = body.trim().split(/\s+/)[0];
    if (!/^\d+\.\d+\.\d+/.test(v)) throw new Error("not a version: " + JSON.stringify(v));
    if (log) log("Remote patcher version: " + v);
    return v;
  } catch (err) {
    if (log) log("Remote patcher version check failed (" + (err && err.message ? err.message : err) + ")");
    return null;
  }
}

/**
 * Fetch the rollback registry (patchers/manifest.json) from the OTA repo.
 * Returns the parsed object {schema, patchers:[...]} on success, or null on any
 * failure (offline, 404, malformed JSON). Silent — rollback is simply not
 * offered when this is unavailable.
 */
async function fetchPatcherManifest(log) {
  try {
    const body = await httpsGet(OTA_PATCHERS_MANIFEST_URL + "?t=" + Date.now(), OTA_TIMEOUT_MS);
    const data = JSON.parse(body);
    if (!data || !Array.isArray(data.patchers)) throw new Error("malformed manifest");
    if (log) log("Rollback registry: " + data.patchers.length + " archived version(s)");
    return data;
  } catch (err) {
    if (log) log("Rollback registry fetch failed (" + (err && err.message ? err.message : err) + ")");
    return null;
  }
}

/**
 * From a cached manifest, pick the newest archived patcher STRICTLY OLDER than
 * the installed one — the target a "Use previous version" click rolls back to.
 * Returns the entry {version, claude, file, ...} or null when there's nothing
 * older. Compatibility is guaranteed by construction: each rollback re-installs
 * its patcher pinned to that entry's recorded Claude version.
 */
function pickPreviousPatcher(manifest, installedVersion) {
  if (!manifest || !Array.isArray(manifest.patchers) || !installedVersion) return null;
  let best = null;
  for (const p of manifest.patchers) {
    if (!p || !p.version || !p.file || !p.claude) continue;
    if (cmpVer(p.version, installedVersion) >= 0) continue;      // not older than installed
    if (!best || cmpVer(p.version, best.version) > 0) best = p;  // newest of the older ones
  }
  return best;
}

/**
 * Read the rollback registry bundled inside the Orbit VSIX
 * (extension/patchers/manifest.json). This is the always-available baseline —
 * the picker works the moment Orbit is installed, with no push and no network.
 * Returns the parsed object or null when absent/malformed.
 */
function readBundledManifest(context) {
  try {
    const p = path.join(context.extensionUri.fsPath, "patchers", "manifest.json");
    if (!fs.existsSync(p)) return null;
    const data = JSON.parse(fs.readFileSync(p, "utf8"));
    if (!data || !Array.isArray(data.patchers)) return null;
    return data;
  } catch (_) {
    return null;
  }
}

/**
 * The registry detectState/enablePrevious actually use: the OTA-cached manifest
 * when the poller has fetched a non-empty one (it's the superset pushed to
 * orbit), otherwise the copy bundled in the VSIX. So rollback works offline /
 * pre-push from the bundle, and picks up newer versions once they're pushed.
 */
function getEffectiveManifest(context) {
  const ota = context.globalState.get(GS_PATCHER_MANIFEST);
  if (ota && Array.isArray(ota.patchers) && ota.patchers.length) return ota;
  return readBundledManifest(context);
}

/**
 * Flatten a cached manifest into the version list the "Previous versions" picker
 * renders: newest-first, each {version, claude, build}. Empty array when no
 * registry is cached. The webview shows version · Claude target · build #.
 */
function patcherHistoryFromManifest(manifest) {
  if (!manifest || !Array.isArray(manifest.patchers)) return [];
  return manifest.patchers
    .filter((p) => p && p.version && p.file && p.claude)
    .slice()
    .sort((a, b) => cmpVer(b.version, a.version))   // newest first
    .map((p) => ({ version: p.version, claude: p.claude, build: (p.build != null ? p.build : null) }));
}

/**
 * Read the currently-installed patcher version from the patched Claude Code
 * webview. Returns the version string or null if Claude Code isn't installed
 * or isn't patched.
 */
function readInstalledPatcherVersion() {
  try {
    const ext = vscode.extensions.getExtension(STOCK_ID);
    if (!ext) return null;
    const jsPath = path.join(ext.extensionUri.fsPath, "webview", "index.js");
    if (!fs.existsSync(jsPath)) return null;
    const text = fs.readFileSync(jsPath, "utf8");
    const m = text.match(/ccPatchBuildVersion="([^"]+)"/);
    return m ? m[1] : null;
  } catch (_) {
    return null;
  }
}

function readBundledWrapperVersion(context) {
  try {
    const p = path.join(context.extensionUri.fsPath, "package.json");
    const manifest = JSON.parse(fs.readFileSync(p, "utf8"));
    return manifest.version || "unknown";
  } catch (_) {
    return "unknown";
  }
}

async function fetchRemoteWrapperVersion(log) {
  try {
    const body = await httpsGet(OTA_WRAPPER_VERSION_URL + "?t=" + Date.now(), OTA_TIMEOUT_MS);
    const v = body.trim().split(/\s+/)[0];
    if (!/^\d+\.\d+\.\d+/.test(v)) throw new Error("not a version: " + JSON.stringify(v));
    if (log) log("GitHub Orbit wrapper version: " + v);
    return v;
  } catch (err) {
    if (log) log("GitHub Orbit wrapper version unavailable (" + (err && err.message ? err.message : err) + ")");
    return null;
  }
}

/**
 * Read the version of the Claude Code extension currently installed in this
 * VS Code, straight from its manifest. Returns the version string or null.
 */
function readInstalledClaudeVersion() {
  try {
    const ext = vscode.extensions.getExtension(STOCK_ID);
    return (ext && ext.packageJSON && ext.packageJSON.version) || null;
  } catch (_) {
    return null;
  }
}

/**
 * Query the VS Code Marketplace for the NEWEST published anthropic.claude-code
 * version. The gallery returns the full version history (one entry per
 * platform), so we take the max by semver rather than trusting array order.
 * Returns the version string, or null on any failure — callers treat null as
 * "unknown, don't prompt" so a flaky network never produces a false alarm.
 */
function fetchLatestClaudeVersion(log) {
  return new Promise((resolve) => {
    let settled = false;
    const done = (v) => { if (!settled) { settled = true; resolve(v); } };
    try {
      const payload = JSON.stringify({
        filters: [{ criteria: [{ filterType: 7, value: STOCK_ID }] }],
        flags: 0x1, // IncludeVersions — version list only, no file assets needed
      });
      const u = new URL(MARKETPLACE_QUERY_URL);
      const req = https.request(
        {
          hostname: u.hostname,
          path: u.pathname + u.search,
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "Accept": "application/json;api-version=7.2-preview.1",
            "User-Agent": "claude-code-orbit-vscode",
            "Content-Length": Buffer.byteLength(payload),
          },
        },
        (res) => {
          if (res.statusCode !== 200) {
            res.resume();
            if (log) log("Marketplace query HTTP " + res.statusCode);
            return done(null);
          }
          const chunks = [];
          res.on("data", (c) => chunks.push(c));
          res.on("end", () => {
            try {
              const data = JSON.parse(Buffer.concat(chunks).toString("utf8"));
              const ext = data.results && data.results[0] && data.results[0].extensions && data.results[0].extensions[0];
              const versions = ext && ext.versions;
              if (!versions || !versions.length) throw new Error("no versions in response");
              let latest = null;
              for (const entry of versions) {
                const ver = entry && entry.version;
                if (ver && /^\d+\.\d+\.\d+/.test(ver) && (!latest || cmpVer(ver, latest) > 0)) latest = ver;
              }
              if (!latest) throw new Error("no parseable version");
              if (log) log("Latest Claude Code on Marketplace: v" + latest);
              done(latest);
            } catch (err) {
              if (log) log("Marketplace parse failed (" + (err && err.message ? err.message : err) + ")");
              done(null);
            }
          });
          res.on("error", () => done(null));
        }
      );
      req.on("error", (err) => { if (log) log("Marketplace query failed (" + (err && err.message ? err.message : err) + ")"); done(null); });
      req.setTimeout(OTA_TIMEOUT_MS, () => { req.destroy(new Error("timeout")); done(null); });
      req.write(payload);
      req.end();
    } catch (err) {
      if (log) log("Marketplace query error (" + (err && err.message ? err.message : err) + ")");
      done(null);
    }
  });
}

/**
 * Core update check: fetch the remote patcher version from GitHub, compare
 * against what's installed, update status bar, and show a VS Code notification
 * when the remote is newer. Runs silently on failure (offline, etc.).
 */
async function checkForPatcherUpdate(context, provider, statusBarItem) {
  try {
    const devMode = vscode.workspace.getConfiguration("claudeCodeOrbit").get("devMode", false);
    if (devMode) {
      // In dev mode, still fetch the remote version so you can test
      // the notification flow, but mark the status bar clearly.
      statusBarItem.text = "$(beaker) Orbit Dev";
      statusBarItem.tooltip = "Claude Code Orbit — DEV MODE (bundled patcher, fast polling)";
      statusBarItem.backgroundColor = undefined;
      statusBarItem.show();
    }

    const remoteVersion = await fetchRemotePatcherVersion();
    if (!remoteVersion) {
      statusBarItem.hide();
      return;
    }

    // Persist the latest remote version so detectState() can use it without
    // making its own network call (detectState is synchronous).
    await context.globalState.update(GS_REMOTE_PATCHER_VERSION, remoteVersion);

    // Cache the rollback registry too, so detectState() can offer "Use previous
    // version" with no network call. Silent on failure — rollback is optional.
    try {
      const manifest = await fetchPatcherManifest();
      if (manifest) await context.globalState.update(GS_PATCHER_MANIFEST, manifest);
    } catch (_) {}

    // Also cache the newest Claude Code on the Marketplace, so detectState() and
    // the "Check updates" button can flag "a newer Claude Code exists — re-patch"
    // even when our (version-agnostic) patcher itself hasn't changed. Refresh the
    // sidebar when the value actually changes so the hero updates without a reload.
    const prevLatestClaude = context.globalState.get(GS_LATEST_CLAUDE_VERSION);
    const latestClaude = await fetchLatestClaudeVersion();
    if (latestClaude) {
      await context.globalState.update(GS_LATEST_CLAUDE_VERSION, latestClaude);
      if (latestClaude !== prevLatestClaude && provider && !provider.busy) provider.pushState();
    }

    const installedVersion = readInstalledPatcherVersion();
    if (!installedVersion) {
      // Claude Code isn't patched yet — nothing to compare against.
      statusBarItem.text = "$(check) Orbit";
      statusBarItem.backgroundColor = undefined;
      statusBarItem.show();
      return;
    }

    // A newer Claude Code is "outdated" only for experimental installs — stable
    // is a deliberate frozen pin, so we never nag stable users to move off it.
    const claudeCodeVersion = readInstalledClaudeVersion();
    const onStable = !!claudeCodeVersion && claudeCodeVersion === readBundledStableVersion(context);
    const claudeOutdated = !onStable && !!claudeCodeVersion && !!latestClaude && cmpVer(claudeCodeVersion, latestClaude) < 0;

    if (cmpVer(installedVersion, remoteVersion) < 0) {
      // --- Update available ---
      statusBarItem.text = "$(cloud-download) Orbit Update";
      statusBarItem.backgroundColor = new vscode.ThemeColor("statusBarItem.warningBackground");
      statusBarItem.show();

      // Only notify once per version (dedup via globalState).
      const lastNotified = context.globalState.get(GS_LAST_NOTIFIED_VERSION);
      if (lastNotified !== remoteVersion) {
        await context.globalState.update(GS_LAST_NOTIFIED_VERSION, remoteVersion);
        const action = await vscode.window.showInformationMessage(
          `Claude Code Orbit: Experimental patcher v${remoteVersion} is available (you have v${installedVersion}). Update now?`,
          "Update", "Later"
        );
        if (action === "Update") {
          provider.triggerEnable();
        }
      }
    } else if (claudeOutdated) {
      // --- Patcher is current, but a newer Claude Code shipped. Re-patching
      //     pulls the newest Claude and re-applies the same experimental patcher. ---
      statusBarItem.text = "$(cloud-download) Orbit Update";
      statusBarItem.backgroundColor = new vscode.ThemeColor("statusBarItem.warningBackground");
      statusBarItem.show();

      const lastNotifiedClaude = context.globalState.get(GS_LAST_NOTIFIED_CLAUDE);
      if (lastNotifiedClaude !== latestClaude) {
        await context.globalState.update(GS_LAST_NOTIFIED_CLAUDE, latestClaude);
        const stablePin = readBundledStableVersion(context);
        const verified = cmpVer(latestClaude, stablePin) <= 0;
        const msg = verified
          ? `Claude Code Orbit: Claude Code v${latestClaude} is available (verified, you're on v${claudeCodeVersion}). Re-patch to update?`
          : `Claude Code Orbit: Claude Code v${latestClaude} shipped but isn't verified yet (verified up to v${stablePin}). Try the experimental patch, or stay on stable?`;
        const action = await vscode.window.showInformationMessage(msg, "Update", "Later");
        if (action === "Update") {
          provider.triggerEnable();
        }
      }
    } else {
      // --- Up to date ---
      statusBarItem.text = "$(check) Orbit";
      statusBarItem.backgroundColor = undefined;
      statusBarItem.show();
    }
  } catch (_) {
    // Offline / unexpected error — silently skip this cycle.
    statusBarItem.hide();
  }
}

/**
 * Start periodic background checks for patcher updates.
 * First check runs after STARTUP_DELAY_MS so VS Code finishes loading.
 * Subsequent checks run every POLL_INTERVAL_MS.
 */
function startBackgroundPolling(context, provider, statusBarItem) {
  const devMode = vscode.workspace.getConfiguration("claudeCodeOrbit").get("devMode", false);
  const interval = devMode ? 60 * 1000 : POLL_INTERVAL_MS;     // 1 min vs 4 hours
  const delay = devMode ? 5 * 1000 : STARTUP_DELAY_MS;          // 5 sec vs 30 sec

  const initialTimer = setTimeout(() => {
    checkForPatcherUpdate(context, provider, statusBarItem);
  }, delay);

  const intervalTimer = setInterval(() => {
    checkForPatcherUpdate(context, provider, statusBarItem);
  }, interval);

  context.subscriptions.push({
    dispose: () => { clearTimeout(initialTimer); clearInterval(intervalTimer); }
  });
}

// ---------------------------------------------------------------------------

class SidebarProvider {
  constructor(context) {
    this.context = context;
    this.view = null;
    this.busy = false;
  }

  resolveWebviewView(view) {
    this.view = view;
    view.webview.options = { enableScripts: true, localResourceRoots: [this.context.extensionUri] };
    view.webview.html = this.renderHTML();
    view.webview.onDidReceiveMessage((msg) => this.onMessage(msg));
    view.onDidChangeVisibility(() => { if (view.visible) this.pushState(); });
    this.pushState();
  }

  send(type, payload) {
    if (this.view) this.view.webview.postMessage(Object.assign({ type }, payload || {}));
  }

  log(line) { this.send("log", { line }); }

  pushState() {
    if (this.view) this.send("state", detectState(this.context));
  }

  /**
   * Called by the background poller when the user clicks "Update" in the
   * VS Code notification. Routes through the same enable flow as the
   * sidebar button so the webview stays in sync.
   */
  triggerEnable() {
    this.onMessage({ type: "action", action: "enable" });
  }

  async onMessage(msg) {
    if (msg.type === "refresh") return this.pushState();
    if (msg.type === "restart") {
      try {
        await vscode.commands.executeCommand("workbench.extensions.action.restartExtensions");
      } catch (_) {
        try { await vscode.commands.executeCommand("workbench.action.reloadWindow"); } catch (__) {}
      }
      return;
    }
    if (msg.type === "openExtension" && msg.id) {
      try { await vscode.commands.executeCommand("extension.open", msg.id); } catch (_) {}
      return;
    }
    if (msg.type === "openUrl" && msg.url) {
      try { await vscode.env.openExternal(vscode.Uri.parse(msg.url)); } catch (_) {}
      return;
    }
    if (msg.type === "cancel") {
      // Honor cancel only while we're still in a safe (pre-uninstall) step and
      // haven't already requested it. Once we've committed to swapping the
      // extension, ignore it so we never leave Claude Code half-installed.
      if (this.busy && !this.committing && !this.cancelRequested) {
        this.cancelRequested = true;
        this.log("Cancellation requested — stopping at the next safe point…");
        if (this.activeProc) { try { this.activeProc.kill(); } catch (_) {} }
      }
      return;
    }
    if (msg.type === "action" && !this.busy) {
      this.busy = true;
      this.cancelRequested = false;
      this.committing = false;
      this.activeProc = null;
      this.send("phase", { phase: "working", action: msg.action });
      try {
        if (msg.action === "enable") await this.enable(false);
        else if (msg.action === "enableStable") await this.enable(true);
        else if (msg.action === "enablePrevious") await this.enablePrevious(msg.version);
        else if (msg.action === "disable") await this.disable();
        else if (msg.action === "updateWrapper") await this.updateWrapper();
        else if (msg.action === "checkUpdates") {
          let resultMsg = "";
          let resultSub = "";
          let resultSubHtml = "";   // optional rich breakdown for the "up to date" case
          let updateAvailable = false;
          let updateAction = "enable";
          try {
            this.log("Checking GitHub experimental patcher version");
            const remoteVersion = await fetchRemotePatcherVersion((l) => this.log(l));
            if (!remoteVersion) {
              throw new Error("Could not reach GitHub (HTTP 404). Check your connection or the repository URL.");
            }
            this.log("Checking GitHub Orbit wrapper version");
            const remoteWrapperVersion = await fetchRemoteWrapperVersion((l) => this.log(l));
            await this.context.globalState.update(GS_REMOTE_PATCHER_VERSION, remoteVersion);
            this.log("Checking newest Claude Code on the Marketplace");
            const latestClaude = await fetchLatestClaudeVersion((l) => this.log(l));
            if (latestClaude) await this.context.globalState.update(GS_LATEST_CLAUDE_VERSION, latestClaude);
            const installedVersion = readInstalledPatcherVersion();
            const wrapperVersion = readBundledWrapperVersion(this.context);
            const claudeCodeVersion = readInstalledClaudeVersion();
            const onStable = !!claudeCodeVersion && claudeCodeVersion === readBundledStableVersion(this.context);
            const claudeOutdated = !onStable && !!installedVersion && !!claudeCodeVersion && !!latestClaude && cmpVer(claudeCodeVersion, latestClaude) < 0;
            this.log("Reading installed Claude Code patcher version: " + (installedVersion || "not patched"));
            this.log("Comparing installed patcher against GitHub experimental");
            if (remoteWrapperVersion && cmpVer(wrapperVersion, remoteWrapperVersion) < 0) {
              updateAvailable = true;
              updateAction = "updateWrapper";
              resultMsg = "Orbit wrapper v" + remoteWrapperVersion + " is available.";
              resultSub = "Installed Orbit wrapper is v" + wrapperVersion + ". This updates the sidebar/updater itself from GitHub. Install wrapper update now?";
            } else if (!installedVersion) {
              updateAvailable = true;
              resultMsg = "Claude Code is not patched yet.";
              resultSub = "GitHub experimental patcher is v" + remoteVersion + ". Orbit wrapper UI is v" + wrapperVersion + ". Install experimental now?";
            } else if (cmpVer(installedVersion, remoteVersion) < 0) {
              updateAvailable = true;
              resultMsg = "Experimental patcher v" + remoteVersion + " is available.";
              resultSub = "Installed Claude Code has patcher v" + installedVersion + ". Orbit wrapper UI is v" + wrapperVersion + ". Install experimental now?";
            } else if (claudeOutdated) {
              updateAvailable = true;
              updateAction = "enable";
              resultMsg = "Claude Code v" + latestClaude + " is available.";
              resultSub = "You're patched on Claude Code v" + claudeCodeVersion + " (patch #" + installedVersion + "). Install the newest to move up — or use Previous versions if it misbehaves.";
            } else {
              // "Tool & target" framing: the patch tool is shown as its own build
              // NUMBER (#57) aimed at the Claude Code version it was built/verified
              // for (the stable pin) — never as a peer "version" that competes with
              // the Claude Code number. A dynamic ✓/△ shows whether the Claude you
              // are actually running is the one the patch tool was tested against.
              // (All version strings are server-validated semver — safe as HTML.)
              const stablePin = readBundledStableVersion(this.context);
              const claudeIsNewest = !!latestClaude && !!claudeCodeVersion && cmpVer(claudeCodeVersion, latestClaude) >= 0;
              // Build number = trailing segment of the patcher version (1.2.57 -> 57),
              // so it reads as a build counter, not a version. Fall back to the whole
              // string if it isn't dotted (e.g. "dev").
              const patchNum = (function () {
                const v = installedVersion || "";
                const seg = v.split(".").pop();
                return (v.indexOf(".") !== -1 && /^\d+$/.test(seg)) ? seg : (v || "?");
              })();
              // Is the running Claude one the patch tool was verified against? The
              // stable pin is the newest Claude Orbit test-ran the patcher on.
              const builtForOk = !claudeCodeVersion || !stablePin || cmpVer(claudeCodeVersion, stablePin) <= 0;
              const exactMatch = !!claudeCodeVersion && claudeCodeVersion === stablePin;

              resultMsg = "You're patched and current.";

              const claudeNote = claudeIsNewest ? "✓ newest" : (onStable ? "✓ verified" : "✓");
              const patchNote = (builtForOk ? "✓ built for v" : "△ built for v") + (stablePin || "?");
              const patchNoteCls = builtForOk ? "ok" : "warn";
              const row = (label, val, note, cls) =>
                "<span class=\"verLabel\">" + label + "</span>" +
                "<span class=\"verVal\">" + val + "</span>" +
                "<span class=\"verNote " + cls + "\">" + note + "</span>";

              const foot = builtForOk
                ? "“#" + patchNum + "” is the patch tool’s own build number — not a Claude version. It was built &amp; tested for Claude Code v" + (stablePin || "?") + (exactMatch ? " — exactly the version you’re running." : ".")
                : "“#" + patchNum + "” is the patch tool’s own build number — not a Claude version. It was built &amp; tested for Claude Code v" + (stablePin || "?") + "; you’re on v" + (claudeCodeVersion || "?") + ", which is newer — Orbit patched it cleanly and it’s working, but this exact build isn’t verified yet.";

              resultSubHtml =
                "<div class=\"verTable\">" +
                row("Claude Code", "v" + (claudeCodeVersion || "?"), claudeNote, "ok") +
                row("Patch tool", "#" + patchNum, patchNote, patchNoteCls) +
                row("Orbit app", "v" + wrapperVersion, "✓", "ok") +
                "</div>" +
                "<span class=\"subNote\">" + foot + "</span>";

              resultSub = "Patched and current — Claude Code v" + (claudeCodeVersion || "?") + ", Orbit patch tool #" + patchNum + " (built for v" + (stablePin || "?") + "), Orbit app v" + wrapperVersion + ".";
            }
          } catch (err) {
            this.send("phase", {
              phase: "error",
              message: "Check failed: " + (err && err.message ? err.message : err),
            });
            this.send("log", { line: "Check failed: " + (err && err.message ? err.message : err) });
            this.busy = false;
            this.pushState();
            return;
          }
          this.send("phase", {
            phase: "done",
            action: msg.action,
            message: resultMsg,
            subMessage: resultSub,
            subHtml: resultSubHtml,
            updateAvailable,
            updateAction,
          });
          this.busy = false;
          this.pushState();
          return;
        }
        this.send("phase", {
          phase: "done",
          action: msg.action,
          message: msg.action === "disable"
            ? "Original Claude Code restored."
            : msg.action === "updateWrapper"
              ? "Orbit wrapper updated."
            : msg.action === "enableStable"
              ? "Verified build installed."
            : msg.action === "enablePrevious"
              ? "Installed patcher v" + (msg.version || "?") + "."
              : "Experimental Orbit installed.",
          subMessage: msg.action === "updateWrapper"
            ? "Reload VS Code to start the updated Orbit sidebar."
            : msg.action === "enableStable"
            ? "Reload VS Code to start Claude Code from the verified, known-good bundle."
            : msg.action === "enablePrevious"
            ? "Reload VS Code to start Claude Code on patcher v" + (msg.version || "?") + "."
            : "Reload VS Code for the change to take effect.",
        });
      } catch (err) {
        if (this.cancelRequested && !this.committing) {
          this.log("Cancelled. No changes were applied.");
          this.send("phase", { phase: "cancelled" });
        } else {
          const baseMsg = String(err && err.message ? err.message : err);
          // If we already crossed the point of no return, the extension swap
          // failed partway — be explicit so the user knows Claude Code may be
          // uninstalled and can reinstall via the buttons on the error pane.
          this.send("phase", {
            phase: "error",
            message: this.committing
              ? "The swap failed partway, so Claude Code may currently be uninstalled. Use the button below to reinstall it (Install newest).\n\n" + baseMsg
              : baseMsg,
          });
        }
      } finally {
        this.busy = false;
        this.cancelRequested = false;
        this.committing = false;
        this.activeProc = null;
        this.pushState();
      }
    }
  }

  // Throws if the user asked to cancel. Call only at SAFE points — before any
  // uninstall/install has started — so a cancel never leaves Claude Code broken.
  checkCancelled() {
    if (this.cancelRequested) {
      const e = new Error("Cancelled by user.");
      e.cancelled = true;
      throw e;
    }
  }

  // Cross the point of no return: from here on the extension is being swapped,
  // so we lock out cancellation and hide the Cancel button in the webview.
  commit() {
    this.committing = true;
    this.activeProc = null;
    this.send("lockCancel");
  }

  async enable(useStable) {
    const python = await findPython();
    if (!python) throw new Error("Python not found on PATH. Install Python 3 and retry.");
    this.log("Using Python: " + python);

    const devMode = vscode.workspace.getConfiguration("claudeCodeOrbit").get("devMode", false);
    const work = fs.mkdtempSync(path.join(os.tmpdir(), "claude-orbit-"));
    const bundledPatcher = path.join(this.context.extensionUri.fsPath, "stable", "patch_claude.py");
    const otaPatcher = (devMode || useStable) ? null : await fetchOtaPatcher(this.context, (l) => this.log(l));
    if (devMode) this.log("[DEV] Skipping OTA patcher — using bundled");
    if (useStable) this.log("[STABLE] Using bundled production patcher only");
    const patcher = useStable ? bundledPatcher : (otaPatcher || bundledPatcher);
    const patcherSource = otaPatcher ? "OTA" : "bundled";
    const out = path.join(work, "patched.vsix");

    // Pass the active patcher version so the patcher stamps it as ccPatchBuildVersion
    // in the patched webview. detectState() reads it back later to know whether
    // an installed patch is current or behind a newer Orbit release.
    let patcherVersion = readBundledPatcherVersion(this.context) || "dev";
    if (!useStable && otaPatcher) {
      const remoteVersion = await fetchRemotePatcherVersion((l) => this.log(l));
      if (remoteVersion) {
        patcherVersion = remoteVersion;
        await this.context.globalState.update(GS_REMOTE_PATCHER_VERSION, remoteVersion);
      }
    }
    const args = [STOCK_ID, "--out", out, "--download-dir", work, "--patcher-version", patcherVersion];
    if (useStable) {
      const stable = readBundledStableVersion(this.context);
      args.push("--version", stable);
      this.log("Downloading + patching stable " + STOCK_ID + " v" + stable + " (patcher v" + patcherVersion + ", " + patcherSource + ")");
    } else {
      this.log("Downloading + patching experimental " + STOCK_ID + " (patcher v" + patcherVersion + ", " + patcherSource + ")");
    }
    this.checkCancelled();
    await runPython(python, patcher, args, (line) => this.log(line), (proc) => { this.activeProc = proc; });
    this.activeProc = null;

    // Last safe checkpoint, then commit — nothing below here is cancellable.
    this.checkCancelled();
    this.commit();
    this.log("Uninstalling current " + STOCK_ID);
    if (vscode.extensions.getExtension(STOCK_ID)) {
      await vscode.commands.executeCommand("workbench.extensions.uninstallExtension", STOCK_ID);
    }
    this.log("Installing patched VSIX");
    await vscode.commands.executeCommand("workbench.extensions.installExtension", vscode.Uri.file(out));
  }

  // Roll back to an archived patcher version from the registry. Mirrors enable(),
  // but the patcher script is DOWNLOADED from /patchers/<file> and Claude Code is
  // pinned to that entry's verified version (same --version pin "Use verified
  // build" uses) — so a rollback is always a known-good (patcher, Claude) pair.
  async enablePrevious(version) {
    const manifest = getEffectiveManifest(this.context);
    const entry = manifest && Array.isArray(manifest.patchers)
      ? manifest.patchers.find((p) => p && p.version === version) : null;
    if (!entry || !entry.file || !entry.claude) {
      throw new Error("Version v" + version + " is no longer in the registry. Run “Check for updates” and try again.");
    }
    const python = await findPython();
    if (!python) throw new Error("Python not found on PATH. Install Python 3 and retry.");
    this.log("Using Python: " + python);

    const work = fs.mkdtempSync(path.join(os.tmpdir(), "claude-orbit-prev-"));
    // Prefer the patcher bundled in the VSIX (offline, instant); only fetch from
    // GitHub when this version isn't one we shipped with.
    const bundled = path.join(this.context.extensionUri.fsPath, "patchers", entry.file);
    let patcherPath;
    if (fs.existsSync(bundled)) {
      patcherPath = bundled;
      this.log("Using bundled patcher v" + entry.version);
    } else {
      patcherPath = path.join(work, entry.file);
      this.log("Downloading archived patcher v" + entry.version + " from GitHub");
      await httpsDownload(OTA_PATCHERS_BASE + "/" + encodeURIComponent(entry.file) + "?t=" + Date.now(), patcherPath, OTA_TIMEOUT_MS * 4);
    }
    this.checkCancelled();

    const out = path.join(work, "patched.vsix");
    const args = [STOCK_ID, "--out", out, "--download-dir", work,
                  "--patcher-version", entry.version, "--version", entry.claude];
    this.log("Downloading + patching Claude Code v" + entry.claude + " with patcher v" + entry.version + " (rollback)");
    this.checkCancelled();
    await runPython(python, patcherPath, args, (line) => this.log(line), (proc) => { this.activeProc = proc; });
    this.activeProc = null;

    // Last safe checkpoint, then commit — the swap below is irreversible.
    this.checkCancelled();
    this.commit();
    this.log("Uninstalling current " + STOCK_ID);
    if (vscode.extensions.getExtension(STOCK_ID)) {
      await vscode.commands.executeCommand("workbench.extensions.uninstallExtension", STOCK_ID);
    }
    this.log("Installing rolled-back VSIX");
    await vscode.commands.executeCommand("workbench.extensions.installExtension", vscode.Uri.file(out));
  }

  async updateWrapper() {
    const work = fs.mkdtempSync(path.join(os.tmpdir(), "claude-orbit-wrapper-"));
    const out = path.join(work, "claude-code-orbit-latest.vsix");
    this.log("Downloading Orbit wrapper VSIX from GitHub");
    await httpsDownload(OTA_WRAPPER_VSIX_URL + "?t=" + Date.now(), out, OTA_TIMEOUT_MS * 4);
    this.checkCancelled();
    this.commit();
    this.log("Installing Orbit wrapper VSIX");
    await vscode.commands.executeCommand("workbench.extensions.installExtension", vscode.Uri.file(out));
  }

  async disable() {
    const python = await findPython();
    if (!python) throw new Error("Python not found on PATH. Install Python 3 and retry.");
    this.log("Using Python: " + python);

    const devMode = vscode.workspace.getConfiguration("claudeCodeOrbit").get("devMode", false);
    const work = fs.mkdtempSync(path.join(os.tmpdir(), "claude-orbit-"));
    // For disable we only need the marketplace download logic; both OTA and
    // bundled patchers do that identically. In dev mode skip OTA.
    const bundledPatcher = path.join(this.context.extensionUri.fsPath, "stable", "patch_claude.py");
    const otaPatcher = devMode ? null : await fetchOtaPatcher(this.context, (l) => this.log(l));
    const patcher = otaPatcher || bundledPatcher;

    this.checkCancelled();
    this.log("Downloading original " + STOCK_ID);
    let downloadedPath = null;
    await runPython(
      python,
      patcher,
      [STOCK_ID, "--download-only", "--download-dir", work],
      (line) => {
        this.log(line);
        const m = line.match(/STOCK_VSIX_PATH:\s*(.+)$/);
        if (m) downloadedPath = m[1].trim();
      },
      (proc) => { this.activeProc = proc; }
    );
    this.activeProc = null;

    // Last safe checkpoint, then commit — the uninstall below is irreversible,
    // so cancellation is locked out from here on.
    this.checkCancelled();
    this.commit();
    this.log("Uninstalling current " + STOCK_ID);
    if (vscode.extensions.getExtension(STOCK_ID)) {
      await vscode.commands.executeCommand("workbench.extensions.uninstallExtension", STOCK_ID);
    }

    if (downloadedPath && fs.existsSync(downloadedPath)) {
      this.log("Installing original VSIX from " + path.basename(downloadedPath));
      await vscode.commands.executeCommand("workbench.extensions.installExtension", vscode.Uri.file(downloadedPath));
    } else {
      this.log("Falling back to marketplace install");
      await vscode.commands.executeCommand("workbench.extensions.installExtension", STOCK_ID);
    }
  }

  renderHTML() {
    const nonce = String(Math.random()).slice(2);
    const logo = this.view.webview.asWebviewUri(
      vscode.Uri.joinPath(this.context.extensionUri, "media", "claude-code-orbit.png")
    );
    const recIcon = (file) => this.view.webview.asWebviewUri(
      vscode.Uri.joinPath(this.context.extensionUri, "media", file)
    );
    const recs = [
      { id: "lunarwerx.saydeploy",     name: "SayDeploy",                 tag: "Ship from VS Code by telling Copilot what to do.",       icon: recIcon("rec-saydeploy.png") },
      { id: "lunarwerx.copilot-suite", name: "Copilot AI Productivity Suite", tag: "Turn your snippets into Copilot superpowers.",        icon: recIcon("rec-copilot-suite.png") },
      { id: "lunarwerx.paramount-docs", name: "Paramount Chat",           tag: "Customer, payment, and analytics context in Copilot.",   icon: recIcon("rec-paramount.png") },
      { url: "https://connections.icu/", name: "Connexions",              tag: "Relationship intelligence workspace.",                  icon: recIcon("rec-connexions.png"), company: true },
    ];
    const renderRec = (r) => {
      const attr = r.company ? `data-url="${r.url}"` : `data-ext-id="${r.id}"`;
      const title = r.company ? `Open ${r.name} website` : `Open ${r.name} in Extensions`;
      return `
      <button class="recItem" ${attr} title="${title}">
        <img class="recIcon" src="${r.icon}" alt=""/>
        <div class="recBody">
          <div class="recName">${r.name}</div>
          <div class="recTag">${r.tag}</div>
        </div>
        <span class="recArrow">›</span>
      </button>`;
    };
    // Split into two labelled sections: VS Code extensions vs companies.
    const recExtHtml = recs.filter(r => !r.company).map(renderRec).join("");
    const recCompanyHtml = recs.filter(r => r.company).map(renderRec).join("");
    let version = "";
    try {
      version = JSON.parse(fs.readFileSync(
        path.join(this.context.extensionUri.fsPath, "package.json"), "utf8"
      )).version || "";
    } catch (_) {}
    // Read stable version from bundled file for display in the HTML.
    // Falls back to the hardcoded constant if the bundled file is missing.
    const stableVersion = readBundledStableVersion(this.context);
    return `<!doctype html>
<html><head>
<meta charset="utf-8"/>
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src ${this.view.webview.cspSource}; style-src 'unsafe-inline'; font-src 'self' https://*.vscode-cdn.net; script-src 'nonce-${nonce}';"/>
<style>
*{box-sizing:border-box}
html,body{height:100%;margin:0;padding:0}
body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;font-size:13px;
  color:var(--vscode-foreground);background:transparent;overflow:hidden}
.wrap{display:flex;flex-direction:column;height:100%;padding:28px 22px 18px;
  overflow:auto}
.hero{display:flex;flex-direction:column;align-items:center;text-align:center;
  margin-bottom:26px;flex-shrink:0}
.logo{width:48px;height:48px;margin-bottom:14px;
  filter:drop-shadow(0 2px 8px rgba(0,0,0,.25));transition:transform .4s ease}
.title{margin:0;font-size:13px;font-weight:700;letter-spacing:.16em;
  text-transform:uppercase;line-height:1.2}
.subtitle{margin:6px 0 0;font-size:10px;opacity:.5;text-transform:uppercase;
  letter-spacing:.18em;font-weight:500}
.card{flex:1;display:flex;flex-direction:column;justify-content:flex-start;
  min-height:0}
.statePane{display:none;flex-direction:column;animation:fadeIn .25s ease}
.statePane.active{display:flex}
/* every pane uses the same vertical layout: content is centered in the card,
   then the visual center is shifted up ~10% of panel height via padding-bottom */
.statePane[data-pane="idle"],
.statePane[data-pane="working"],
.statePane[data-pane="done"],
.statePane[data-pane="confirm"],
.statePane[data-pane="error"]{flex:1;justify-content:center;align-items:stretch;
  padding-bottom:20vh}
/* Versions picker fills the card top-aligned (it's a scrollable list, not a
   centered single message) so the list + Back sit inside the card box. */
.statePane[data-pane="versions"]{flex:1;justify-content:flex-start;align-items:stretch}
@keyframes fadeIn{from{opacity:0;transform:translateY(4px)}to{opacity:1;transform:none}}

/* status pill */
.status{display:inline-flex;align-items:center;gap:8px;align-self:center;
  padding:5px 12px;border-radius:999px;background:rgba(127,127,127,.1);
  font-size:11px;margin:0 auto 18px}
.dot{width:7px;height:7px;border-radius:50%;background:#888;flex-shrink:0}
.dot.patched{background:#f97316;box-shadow:0 0 8px rgba(249,115,22,.6)}
.dot.outdated{background:#3b82f6;box-shadow:0 0 8px rgba(59,130,246,.55);animation:dotPulse 1.6s ease-in-out infinite}
.dot.stock{background:#3b82f6;box-shadow:0 0 8px rgba(59,130,246,.4)}
.dot.none{background:#6b7280}
@keyframes dotPulse{0%,100%{opacity:1}50%{opacity:.4}}

/* buttons */
.btn{display:flex;align-items:center;justify-content:center;gap:8px;width:100%;
  padding:11px 14px;margin-bottom:8px;border:1px solid transparent;border-radius:7px;
  background:var(--vscode-button-secondaryBackground,rgba(127,127,127,.12));
  color:var(--vscode-button-secondaryForeground,var(--vscode-foreground));
  font-size:13px;font-weight:500;cursor:pointer;
  transition:background .14s ease,transform .08s ease,border-color .14s ease}
.btn:hover{background:var(--vscode-button-secondaryHoverBackground,rgba(127,127,127,.2))}
.btn:active{transform:translateY(1px)}
.btn.primary{background:var(--vscode-button-background);
  color:var(--vscode-button-foreground)}
.btn.primary:hover{background:var(--vscode-button-hoverBackground)}
.btn[disabled]{opacity:.4;cursor:not-allowed}
.btn[hidden],.status[hidden]{display:none}
.btn svg{width:14px;height:14px;flex-shrink:0}

/* Alt-action: small text link styled like "Use verified build v2.1.158" */
.altAction{display:block;margin:6px auto 0;background:none;border:0;cursor:pointer;
  font-size:11px;opacity:.55;color:inherit;padding:6px 10px;text-decoration:underline;
  text-underline-offset:2px;text-align:center;font-family:inherit;width:100%}
.altAction:hover{opacity:.9}
.altAction[hidden]{display:none}

.hint{font-size:11px;opacity:.55;line-height:1.55;margin-top:14px;text-align:center}
.hint code{font-size:10.5px;opacity:.85;background:rgba(127,127,127,.12);
  padding:1px 5px;border-radius:3px;font-family:ui-monospace,Consolas,monospace}

/* Patched hero — celebratory block shown only when Orbit is active */
.patchedHero{display:flex;flex-direction:column;align-items:center;text-align:center;
  margin:6px 0 22px;padding:14px 12px;border-radius:9px;
  background:linear-gradient(180deg,rgba(249,115,22,.08),rgba(249,115,22,.02));
  border:1px solid rgba(249,115,22,.18);animation:fadeIn .3s ease}
.patchedHero[hidden]{display:none}
/* updateAvailable variant — same hero shape, blue accent instead of orange,
   signals "your patched build is behind the bundled patcher" */
.patchedHero.updateAvailable{
  background:linear-gradient(180deg,rgba(59,130,246,.10),rgba(59,130,246,.02));
  border:1px solid rgba(59,130,246,.28)}
.patchedTitle{font-size:14px;font-weight:600;margin:0 0 4px;letter-spacing:.01em}
.patchedSub{font-size:11.5px;opacity:.62;margin:0;line-height:1.5}
/* Quiet secondary line for the Orbit patch build number — deliberately dimmer
   and smaller than the Claude version above it, so it never reads as a version
   that's "behind" the Claude Code release. */
.patchedMeta{font-size:10px;opacity:.38;margin:5px 0 0;letter-spacing:.02em}
.patchedMeta[hidden]{display:none}
/* Green "✓ Latest" badge — confirms you're on the newest Claude Code. */
.latestBadge{color:#3fb950;font-weight:600;opacity:.95}

/* Stock hero — same shape as patched hero, blue accent for the stock state */
.stockHero{display:flex;flex-direction:column;align-items:center;text-align:center;
  margin:6px 0 22px;padding:14px 12px;border-radius:9px;
  background:linear-gradient(180deg,rgba(59,130,246,.08),rgba(59,130,246,.02));
  border:1px solid rgba(59,130,246,.18);animation:fadeIn .3s ease}
.stockHero[hidden]{display:none}

/* working pane — sized up 20% from the original cramped defaults */
.workingHero{display:flex;flex-direction:column;align-items:center;
  margin-top:18px;margin-bottom:24px}
.spinner{width:44px;height:44px;border:3px solid rgba(127,127,127,.18);
  border-top-color:var(--vscode-button-background,#3b82f6);border-radius:50%;
  animation:spin 1s linear infinite;margin-bottom:22px}
@keyframes spin{to{transform:rotate(360deg)}}
.stepLabel{font-size:16px;font-weight:500;text-align:center;margin:0 0 6px;
  min-height:22px;letter-spacing:.01em}
.stepSub{font-size:11.5px;opacity:.55;text-align:center;margin:0;
  text-transform:uppercase;letter-spacing:.08em;font-weight:500}
.progressBar{margin:22px auto 4px;height:4px;width:80%;
  background:rgba(127,127,127,.14);border-radius:2px;overflow:hidden}
.progressFill{height:100%;background:var(--vscode-button-background,#3b82f6);
  width:0%;transition:width .4s ease;border-radius:2px}
.workingCancel{margin-top:24px;opacity:.85}
.workingCancel:hover{opacity:1}
.workingCancel[hidden]{display:none}

/* done / error visual treatments — sized up to match the bumped working pane */
.iconCircle{align-self:center;width:50px;height:50px;border-radius:50%;
  display:flex;align-items:center;justify-content:center;margin-bottom:18px}
.iconCircle.error{background:rgba(239,68,68,.14);color:#ef4444}
.iconCircle svg{width:26px;height:26px}
.doneMsg,.errorMsg{text-align:center;font-size:17px;font-weight:600;margin:0 0 10px;
  letter-spacing:.01em}
.doneSub,.errorSub{text-align:center;font-size:13px;opacity:.6;line-height:1.55;
  margin:0 0 26px;padding:0 8px}
/* Explanatory footnote inside the "up to date" breakdown — a subtle divider sets
   the "these counters are independent" note apart from the version list above. */
.subNote{display:block;margin-top:12px;padding-top:10px;
  border-top:1px solid rgba(255,255,255,.09);font-style:italic;font-size:11.5px;line-height:1.5}
/* "Tool & target" version grid in the up-to-date breakdown. One shared grid so
   all three rows align on the same columns; centered as a block inside the
   otherwise-centered .doneSub. The per-row note carries the dynamic ✓ (built for
   your version) / △ (working, but newer than the tested build) state. */
.verTable{display:grid;grid-template-columns:auto auto auto;gap:7px 14px;
  justify-content:center;text-align:left;margin:4px auto 2px}
.verLabel{opacity:.6;font-size:12.5px}
.verVal{font-weight:600;font-size:12.5px;font-variant-numeric:tabular-nums}
.verNote{font-size:11.5px;opacity:.72;white-space:nowrap}
.verNote.ok{color:#3fb950;opacity:.95}
.verNote.warn{color:#d29922;opacity:.95}
/* Version picker list (the "Previous versions" pane) — scrollable rows, each
   showing version + Claude target + build number, with an Install action. */
.verList{display:flex;flex-direction:column;gap:6px;max-height:46vh;overflow:auto;
  margin:4px 0 16px;padding-right:2px}
.verItem{display:flex;align-items:center;gap:10px;padding:9px 11px;border-radius:7px;
  border:1px solid rgba(127,127,127,.16);background:rgba(127,127,127,.05)}
.verItem.current{border-color:rgba(63,185,80,.4);background:rgba(63,185,80,.07)}
.verItemInfo{flex:1;min-width:0}
.verItemVer{font-size:12.5px;font-weight:600;display:flex;align-items:center;gap:6px}
.verItemBadge{font-size:9px;font-weight:700;letter-spacing:.08em;text-transform:uppercase;
  color:#3fb950;border:1px solid rgba(63,185,80,.5);border-radius:4px;padding:1px 5px}
.verItemMeta{font-size:10.5px;opacity:.55;margin-top:2px;
  font-variant-numeric:tabular-nums}
.verItemInstall{flex:0 0 auto;border:1px solid var(--vscode-button-background,#3b82f6);
  background:transparent;color:var(--vscode-foreground);border-radius:6px;
  padding:5px 12px;font-size:11.5px;cursor:pointer;font-family:inherit;
  transition:background .12s,opacity .12s}
.verItemInstall:hover{background:var(--vscode-button-background,#3b82f6);
  color:var(--vscode-button-foreground,#fff)}
.verItemInstall[disabled]{opacity:.4;cursor:default;border-color:transparent}
.verEmpty{font-size:12px;opacity:.55;text-align:center;padding:18px 4px}
.errorSub{font-family:ui-monospace,Consolas,monospace;font-size:11.5px;opacity:.85;
  background:rgba(239,68,68,.06);padding:9px 11px;border-radius:5px;text-align:left;
  max-height:140px;overflow:auto;border-left:2px solid rgba(239,68,68,.4)}

/* recommended extensions block — grows to fill the panel's free space (the
   patched state has little else to show, so the ads get the real estate) and
   centers its enlarged cards vertically between the buttons and the footer. */
.recommended{flex:1 1 auto;display:flex;flex-direction:column;justify-content:center;
  padding-top:22px;min-height:0}
.recHeader{font-size:11px;font-weight:600;letter-spacing:.16em;text-transform:uppercase;
  opacity:.5;text-align:center;margin:0 0 14px}
.recHeaderCompany{margin-top:20px}
.recList{display:flex;flex-direction:column;gap:10px}
.recItem{display:flex;align-items:center;gap:14px;width:100%;text-align:left;
  padding:14px 16px;border:1px solid rgba(127,127,127,.16);border-radius:10px;
  background:rgba(127,127,127,.05);color:var(--vscode-foreground);cursor:pointer;
  font:inherit;transition:background .12s ease,border-color .12s ease,transform .08s ease}
.recItem:hover{background:rgba(127,127,127,.12);border-color:rgba(127,127,127,.28)}
.recItem:active{transform:translateY(1px)}
.recIcon{width:44px;height:44px;flex-shrink:0;border-radius:9px;object-fit:contain}
.recBody{flex:1;min-width:0}
.recName{font-size:14px;font-weight:600;line-height:1.25;
  overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.recEyebrow{font-size:9px;font-weight:700;letter-spacing:.13em;text-transform:uppercase;
  opacity:.6;margin-bottom:2px;color:var(--vscode-textLink-foreground,#1B6EF3)}
.recTag{font-size:11.5px;opacity:.6;line-height:1.4;margin-top:3px;
  overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.recArrow{opacity:.4;font-size:18px;line-height:1;flex-shrink:0}

/* footer: subtle "show details" + brand */
.footer{padding-top:14px;text-align:center;flex-shrink:0}
.detailsToggle{display:inline-block;font-size:10.5px;opacity:.4;cursor:pointer;
  user-select:none;padding:4px 8px}
.detailsToggle:hover{opacity:.7}
.brand{font-size:10px;opacity:.32;margin-top:6px;letter-spacing:.06em}
.log{display:none;font-family:ui-monospace,Consolas,monospace;font-size:10.5px;
  opacity:.7;background:rgba(0,0,0,.22);border-radius:5px;padding:8px 10px;
  max-height:160px;overflow:auto;white-space:pre-wrap;margin-top:8px;text-align:left}
.log.visible{display:block}
</style></head>
<body>
<div class="wrap">

  <div class="hero">
    <img class="logo" id="logo" src="${logo}" alt=""/>
    <div class="title">Claude Code Orbit</div>
    <div class="subtitle">Patch Companion</div>
  </div>

  <div class="card">

    <!-- IDLE -->
    <div class="statePane active" data-pane="idle">
      <div class="status" id="status">
        <span class="dot none"></span>
        <span class="label">Detecting…</span>
      </div>
      <div class="patchedHero" id="patchedHero" hidden>
        <p class="patchedTitle" id="patchedTitle">Orbit is enabled</p>
        <p class="patchedSub" id="patchedSub">Claude Code is running with the patches applied.</p>
        <p class="patchedMeta" id="patchedMeta" hidden></p>
      </div>
      <div class="stockHero" id="stockHero" hidden>
        <p class="patchedTitle">Original Claude Code</p>
        <p class="patchedSub" id="stockSub">Claude Code is installed without Orbit patches.</p>
      </div>
      <button class="btn" id="enableBtn" data-action="enable">
        <span class="btnIcon" id="enableBtnIcon"></span>
        <span class="btnLabel" id="enableBtnLabel">Install newest</span>
      </button>
      <button class="btn" id="disableBtn" data-action="disable"
              title="Uninstall Orbit and restore the original, unpatched Claude Code.">
        <svg viewBox="0 0 14 14" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"><circle cx="7" cy="7" r="5"/></svg>
        Remove Orbit
      </button>
      <button class="btn" id="checkUpdatesBtn" data-action="checkUpdates">
        <svg viewBox="0 0 14 14" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M12.5 7a5.5 5.5 0 1 1-1.7-3.95"/><polyline points="13,1 13,4.2 9.8,4.2"/></svg>
        Check for updates
      </button>
      <button class="altAction" id="versionsBtn" hidden
              title="Pick an earlier version to install — handy if the newest one misbehaves. Each shows the Claude Code it's built for and its build number.">
        Previous versions
      </button>
      <div class="hint" id="idleHint"></div>
    </div>

    <!-- WORKING -->
    <div class="statePane" data-pane="working">
      <div class="workingHero">
        <div class="spinner"></div>
        <p class="stepLabel" id="stepLabel">Preparing…</p>
        <p class="stepSub" id="stepSub">Step 1 of 5</p>
      </div>
      <div class="progressBar"><div class="progressFill" id="progressFill"></div></div>
      <button class="btn workingCancel" id="cancelBtn" data-action="cancel" hidden>Cancel</button>
    </div>

    <!-- DONE -->
    <div class="statePane" data-pane="done">
      <p class="doneMsg" id="doneMsg">All set.</p>
      <p class="doneSub" id="doneSub">Restart Claude Code for the change to take effect.</p>
      <button class="btn primary" id="donePrimaryBtn" data-action="restart">
        <svg viewBox="0 0 14 14" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M12.5 7a5.5 5.5 0 1 1-1.7-3.95"/><polyline points="13,1 13,4.2 9.8,4.2"/></svg>
        Restart Claude Code
      </button>
      <button class="btn" data-action="back">Back</button>
    </div>

    <!-- VERSIONS (picker) -->
    <div class="statePane" data-pane="versions">
      <p class="doneMsg">Choose a version</p>
      <p class="doneSub">Each row shows the patcher version, the Claude Code it’s built for, and its build number. If the newest misbehaves, install an earlier one.</p>
      <div class="verList" id="verList"></div>
      <button class="btn" data-action="back">Back</button>
    </div>

    <!-- CONFIRM (install a chosen version) -->
    <div class="statePane" data-pane="confirm">
      <p class="doneMsg" id="confirmMsg">Install this version?</p>
      <p class="doneSub" id="confirmSub"></p>
      <button class="btn primary" id="confirmInstallBtn">Install</button>
      <button class="btn" id="confirmBackBtn">Cancel</button>
    </div>

    <!-- ERROR -->
    <div class="statePane" data-pane="error">
      <div class="iconCircle error">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><line x1="12" y1="8" x2="12" y2="13"/><line x1="12" y1="16.5" x2="12" y2="16.5"/><circle cx="12" cy="12" r="9"/></svg>
      </div>
      <p class="errorMsg">Something went wrong.</p>
      <p class="errorSub" id="errorSub"></p>
      <button class="btn primary" data-action="enable">Install newest</button>
      <button class="btn" data-action="back">Back</button>
    </div>

  </div>

  <div class="recommended">
    <div class="recHeader">Recommended Extensions</div>
    <div class="recList">${recExtHtml}</div>
    <div class="recHeader recHeaderCompany">Recommended Companies</div>
    <div class="recList">${recCompanyHtml}</div>
  </div>

  <div class="footer">
    <span class="detailsToggle" id="detailsToggle">Show details</span>
    <pre class="log" id="log"></pre>
    <div class="brand">CLAUDE CODE ORBIT${version ? " v" + version : ""}</div>
  </div>

</div>

<script nonce="${nonce}">
const vscode = acquireVsCodeApi();
const STABLE_CLAUDE_VERSION = "${stableVersion}";
const panes = {
  idle: document.querySelector('[data-pane="idle"]'),
  working: document.querySelector('[data-pane="working"]'),
  done: document.querySelector('[data-pane="done"]'),
  versions: document.querySelector('[data-pane="versions"]'),
  confirm: document.querySelector('[data-pane="confirm"]'),
  error: document.querySelector('[data-pane="error"]'),
};
const recommendedEl = document.querySelector(".recommended");
const statusEl = document.getElementById("status");
const stepLabelEl = document.getElementById("stepLabel");
const stepSubEl = document.getElementById("stepSub");
const progressFillEl = document.getElementById("progressFill");
const doneMsgEl = document.getElementById("doneMsg");
const donePrimaryBtn = document.getElementById("donePrimaryBtn");
const errorSubEl = document.getElementById("errorSub");
const logEl = document.getElementById("log");
const detailsToggle = document.getElementById("detailsToggle");
const logoEl = document.getElementById("logo");
const enableBtn = document.getElementById("enableBtn");
const enableBtnIcon = document.getElementById("enableBtnIcon");
const enableBtnLabel = document.getElementById("enableBtnLabel");
const disableBtn = document.getElementById("disableBtn");
const checkUpdatesBtn = document.getElementById("checkUpdatesBtn");
const idleHint = document.getElementById("idleHint");
const patchedHero = document.getElementById("patchedHero");
const patchedTitle = document.getElementById("patchedTitle");
const patchedSub = document.getElementById("patchedSub");
const patchedMeta = document.getElementById("patchedMeta");
const stockHero = document.getElementById("stockHero");
const stockSub = document.getElementById("stockSub");
const versionsBtn = document.getElementById("versionsBtn");
const verList = document.getElementById("verList");
const confirmMsgEl = document.getElementById("confirmMsg");
const confirmSubEl = document.getElementById("confirmSub");
const confirmInstallBtn = document.getElementById("confirmInstallBtn");
const confirmBackBtn = document.getElementById("confirmBackBtn");
let pendingEntry = null;        // version chosen in the picker, awaiting confirm

let logBuf = "";
let lastIdleState = null;        // tracks most recent state from "state" message
let actionStartState = null;     // snapshot of state when working phase begins
let lastHistory = [];            // patcher version list from the latest state msg
let lastInstalledVersion = null; // currently-installed patcher version (for "Installed" badge)

const ICON_CHECK = '<svg viewBox="0 0 14 14" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M2 7l3 3 7-7"/></svg>';
const ICON_REFRESH = '<svg viewBox="0 0 14 14" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M12.5 7a5.5 5.5 0 1 1-1.7-3.95"/><polyline points="13,1 13,4.2 9.8,4.2"/></svg>';

function applyIdleState(state, info) {
  const installedVersion = info && info.installedVersion;
  const bundledVersion = info && info.bundledVersion;
  const claudeCodeVersion = info && info.claudeCodeVersion;
  const latestClaudeVersion = info && info.latestClaudeVersion;
  const onLatestClaude = info && info.onLatestClaude;
  const claudeUpdateAvailable = info && info.claudeUpdateAvailable;
  const patcherHistory = (info && info.patcherHistory) || [];

  // "Install newest" is the always-present primary verb (was "Use experimental").
  enableBtnIcon.innerHTML = ICON_CHECK;
  enableBtnLabel.textContent = "Install newest";

  // "Previous versions" picker is offered whenever the registry holds a version
  // other than the one installed — the only safety net now that there's no
  // separate "stable": if the newest misbehaves, install an earlier one.
  const hasOtherVersions = patcherHistory.some(v => v && v.version && v.version !== installedVersion);
  versionsBtn.hidden = true;

  // Reset buttons + hero variant to neutral, then apply per-state styling.
  enableBtn.classList.remove("primary");
  disableBtn.classList.remove("primary");
  enableBtn.disabled = false;
  disableBtn.disabled = false;
  enableBtn.hidden = false;
  disableBtn.hidden = false;
  checkUpdatesBtn.hidden = true;
  statusEl.hidden = false;
  patchedHero.classList.remove("updateAvailable");
  patchedMeta.hidden = true;   // quiet patch-# line; only the normal patched view shows it

  if (state === "patched") {
    patchedHero.hidden = false;
    stockHero.hidden = true;
    statusEl.hidden = true;                // hide redundant pill — hero card says it
    checkUpdatesBtn.hidden = false;
    versionsBtn.hidden = !hasOtherVersions;
    versionsBtn.textContent = "Previous versions";
    if (claudeUpdateAvailable) {
      // A newer Claude Code shipped — surface "install newest" right in the hero.
      patchedHero.classList.add("updateAvailable");
      enableBtn.hidden = false;
      enableBtn.classList.add("primary");
      enableBtnIcon.innerHTML = ICON_REFRESH;
      disableBtn.title = "Uninstall Orbit and restore the original, unpatched Claude Code.";
      patchedTitle.textContent = "Claude Code update available";
      patchedSub.textContent = "Patched on Claude Code v" + claudeCodeVersion + " (patch #" + installedVersion
        + "). Claude Code v" + latestClaudeVersion + " is available — install the newest to update.";
      enableBtnLabel.textContent = "Install newest (v" + latestClaudeVersion + ")";
      enableBtn.title = "Download the newest Claude Code and re-apply the latest patcher.";
      idleHint.innerHTML = '';
    } else {
      patchedTitle.textContent = "Orbit is enabled";
      // Claude version is the headline (+ a ✓ Latest badge when on the newest);
      // the Orbit patch # drops to a quiet meta line. Version strings are
      // server-validated semver, so innerHTML here is safe.
      if (claudeCodeVersion) {
        let head = "Claude Code v" + claudeCodeVersion;
        if (onLatestClaude) head += ' · <span class="latestBadge">✓ Latest</span>';
        patchedSub.innerHTML = head;
      } else {
        patchedSub.textContent = "Claude Code is running with the patches applied.";
      }
      if (installedVersion) {
        patchedMeta.textContent = "Orbit patch #" + installedVersion;
        patchedMeta.hidden = false;
      }
      enableBtn.hidden = true;               // hide entirely — patched hero already says "enabled"
      disableBtn.classList.add("primary");
      disableBtn.title = "Uninstall Orbit and restore the original, unpatched Claude Code.";
      idleHint.innerHTML = '';
    }
  } else if (state === "outdated") {
    patchedHero.hidden = false;
    patchedHero.classList.add("updateAvailable");
    stockHero.hidden = true;
    statusEl.hidden = true;
    patchedTitle.textContent = "Update available";
    {
      let line = (installedVersion
        ? "Patcher v" + installedVersion + " -> v" + (bundledVersion || "?")
        : "Legacy patches -> v" + (bundledVersion || "?"))
        + (claudeCodeVersion ? " · Claude Code v" + claudeCodeVersion : "");
      patchedSub.textContent = line + ".";
    }
    enableBtnIcon.innerHTML = ICON_REFRESH;
    enableBtnLabel.textContent = "Install newest";
    enableBtn.classList.add("primary");
    enableBtn.title = "Download Claude Code and patch it with the newest patcher (v" + (bundledVersion || "?") + ").";
    checkUpdatesBtn.hidden = false;
    versionsBtn.hidden = !hasOtherVersions;
    versionsBtn.textContent = "Previous versions";
    disableBtn.title = "Uninstall Orbit and restore the original, unpatched Claude Code.";
    idleHint.innerHTML = '';
  } else if (state === "stock") {
    patchedHero.hidden = true;
    stockHero.hidden = false;
    statusEl.hidden = true;                // hide pill — stock hero card says it
    stockSub.textContent = claudeCodeVersion
      ? "Claude Code v" + claudeCodeVersion + " — no Orbit patches yet."
      : "Claude Code is installed without Orbit patches.";
    enableBtn.classList.add("primary");
    enableBtn.title = "";
    disableBtn.hidden = true;              // hide entirely — nothing to restore
    versionsBtn.hidden = !hasOtherVersions;
    versionsBtn.textContent = "Install specific version";
    idleHint.innerHTML = 'Install newest downloads <code>anthropic.claude-code</code>, pulls the latest patcher, and installs it.';
  } else {
    // "none" — Claude Code is not installed.  Offer both "Install newest"
    // (downloads newest Claude + newest patcher) and "Install specific version"
    // (picker of archived patcher → Claude pairs from the bundled registry).
    patchedHero.hidden = true;
    stockHero.hidden = true;
    statusEl.hidden = true;
    enableBtn.disabled = false;
    enableBtn.classList.add("primary");
    enableBtn.title = "Download the newest Claude Code, pull the latest patcher, and install.";
    disableBtn.hidden = true;
    checkUpdatesBtn.hidden = true;
    versionsBtn.hidden = !hasOtherVersions;
    versionsBtn.textContent = "Install specific version";
    idleHint.innerHTML = '<b>Install newest</b> downloads <code>anthropic.claude-code</code>, pulls the latest patcher, and installs it.<br><b>Install specific version</b> picks an archived patcher + its verified Claude Code from the registry.';
  }
}

// Build the "Previous versions" picker rows from the latest history snapshot.
// Each row shows version · Claude target · build #, with an Install action
// (disabled for the version that's already installed).
function renderVersions() {
  verList.innerHTML = "";
  const list = (lastHistory || []).filter(v => v && v.version);
  if (!list.length) {
    const e = document.createElement("div");
    e.className = "verEmpty";
    e.textContent = "No versions available yet — try Check for updates first.";
    verList.appendChild(e);
    return;
  }
  list.forEach(function (v) {
    const isCurrent = v.version === lastInstalledVersion;
    const row = document.createElement("div");
    row.className = "verItem" + (isCurrent ? " current" : "");
    const info = document.createElement("div");
    info.className = "verItemInfo";
    const ver = document.createElement("div");
    ver.className = "verItemVer";
    ver.textContent = "v" + v.version;
    if (isCurrent) {
      const badge = document.createElement("span");
      badge.className = "verItemBadge";
      badge.textContent = "Installed";
      ver.appendChild(badge);
    }
    const meta = document.createElement("div");
    meta.className = "verItemMeta";
    meta.textContent = "Claude " + (v.claude || "?") + (v.build != null ? " · build " + v.build : "");
    info.appendChild(ver);
    info.appendChild(meta);
    const btn = document.createElement("button");
    btn.className = "verItemInstall";
    btn.textContent = isCurrent ? "Installed" : "Install";
    btn.disabled = isCurrent;
    if (!isCurrent) {
      // Don't install on click — show a confirm step first (a deliberate
      // downgrade shouldn't fire on a stray click).
      btn.addEventListener("click", function () {
        pendingEntry = v;
        confirmMsgEl.textContent = "Install patcher v" + v.version + "?";
        confirmSubEl.textContent = "Built for Claude " + (v.claude || "?")
          + (v.build != null ? " · build " + v.build : "")
          + ". This replaces your current patch — you can switch back anytime from Previous versions.";
        confirmInstallBtn.textContent = "Install v" + v.version;
        setPane("confirm");
      });
    }
    row.appendChild(info);
    row.appendChild(btn);
    verList.appendChild(row);
  });
}

versionsBtn.addEventListener("click", function () {
  renderVersions();
  setPane("versions");
});

// Confirm step → kick off the real install (working pane has the Cancel button).
confirmInstallBtn.addEventListener("click", function () {
  if (!pendingEntry) return;
  logBuf = ""; logEl.textContent = ""; logEl.classList.remove("visible");
  detailsToggle.textContent = "Show details";
  resetWorkingState();
  setPane("working");
  vscode.postMessage({ type: "action", action: "enablePrevious", version: pendingEntry.version });
});
// Cancel the confirm → back to the picker so they can choose another.
confirmBackBtn.addEventListener("click", function () {
  pendingEntry = null;
  renderVersions();
  setPane("versions");
});

function setPane(name) {
  Object.keys(panes).forEach(k => panes[k].classList.toggle("active", k === name));
  // Ads live on the idle screen only — hiding them elsewhere keeps the picker,
  // confirm, working and done panes uncluttered AND stops the (grown) ad block
  // from competing for height and overlapping pane buttons (e.g. Back).
  if (recommendedEl) recommendedEl.style.display = (name === "idle") ? "" : "none";
}

document.querySelectorAll("[data-action]").forEach(btn => {
  btn.addEventListener("click", () => {
    if (btn.disabled) return;
    const action = btn.dataset.action;
    if (action === "back") {
      resetWorkingState();
      resetCancelButton();
      logBuf = "";
      logEl.textContent = "";
      logEl.classList.remove("visible");
      detailsToggle.textContent = "Show details";
      setPane("idle");
      return;
    }
    if (action === "restart") {
      vscode.postMessage({ type: "restart" });
      btn.disabled = true;
      btn.innerHTML = '<span style="opacity:.7">Restarting…</span>';
      return;
    }
    if (action === "cancel") {
      vscode.postMessage({ type: "cancel" });
      btn.disabled = true;
      btn.textContent = "Cancelling…";
      return;
    }
    if (action === "checkUpdates") {
      resetWorkingState();
      setCheckProgress(1, "Checking GitHub");
      setPane("working");
      vscode.postMessage({ type: "action", action: "checkUpdates" });
      return;
    }
    logBuf = "";
    logEl.textContent = "";
    logEl.classList.remove("visible");
    detailsToggle.textContent = "Show details";
    resetWorkingState();
    setPane("working");
    // enablePrevious carries the rollback target version via the button's
    // data-version attr; harmless (undefined) for every other action.
    vscode.postMessage({ type: "action", action, version: btn.dataset.version });
  });
});

document.querySelectorAll(".recItem").forEach(el => {
  el.addEventListener("click", () => {
    const id = el.dataset.extId;
    if (id) { vscode.postMessage({ type: "openExtension", id }); return; }
    const url = el.dataset.url;
    if (url) vscode.postMessage({ type: "openUrl", url });
  });
});

detailsToggle.addEventListener("click", () => {
  const showing = logEl.classList.toggle("visible");
  detailsToggle.textContent = showing ? "Hide details" : "Show details";
  if (showing) {
    logEl.textContent = logBuf || "No console logs yet.";
    logEl.scrollTop = logEl.scrollHeight;
  }
});

function resetWorkingState() {
  stepLabelEl.textContent = "Preparing…";
  stepSubEl.textContent = "Step 1 of 5";
  progressFillEl.style.width = "0%";
  logoEl.style.transform = "";
}

function resetCancelButton() {
  const cb = document.getElementById("cancelBtn");
  if (cb) { cb.hidden = true; cb.disabled = false; cb.textContent = "Cancel"; }
}

function setCheckProgress(idx, label) {
  currentStep = idx;
  stepLabelEl.textContent = label + "...";
  stepSubEl.textContent = "Step " + idx + " of 3";
  progressFillEl.style.width = (idx / 3 * 100) + "%";
}

// Map raw log lines to friendly progress steps.
// Keyed by first-match wins. Only update when the new step >= current step
// (so a late "Patching..." line doesn't go backwards from "Installing").
const STEPS = [
  { match: /Checking GitHub experimental/i, idx: 1, label: "Checking GitHub", check: true },
  { match: /Checking GitHub Orbit wrapper/i, idx: 1, label: "Checking wrapper", check: true },
  { match: /Reading installed Claude Code patcher/i, idx: 2, label: "Reading installed patcher", check: true },
  { match: /Comparing installed patcher/i, idx: 3, label: "Comparing versions", check: true },
  { match: /Downloading anthropic|Downloading marketplace|Downloading \\+ patching|Downloading original/i, idx: 1, label: "Downloading Claude Code" },
  { match: /Extracting VSIX/i, idx: 2, label: "Extracting" },
  { match: /Patching Claude webview/i, idx: 2, label: "Applying patches" },
  { match: /syntax check|Verification passed/i, idx: 3, label: "Verifying" },
  { match: /Writing patched|Patched VSIX written|Overall status/i, idx: 4, label: "Packaging" },
  { match: /Uninstalling current|Installing patched|Installing original|Falling back/i, idx: 5, label: "Installing" },
];
// During the disable (revert) flow we only hit steps 1 and 5, and the language
// should reflect "restoring stock" rather than "downloading/installing".
const REVERT_LABELS = {
  1: "Restoring original Claude Code",
  5: "Reinstalling original Claude Code",
};
// When the user clicks Enable from the "outdated" state (i.e. patches are
// already installed and they're refreshing), step 1 reads as "updating" rather
// than "downloading" — that's the user's mental model for what's happening.
const UPDATE_LABELS = {
  1: "Updating Claude Code",
};
const TOTAL_STEPS = 5;
let currentStep = 0;
let currentAction = null;

function updateProgress(line) {
  for (const s of STEPS) {
    if (s.match.test(line)) {
      if (currentAction === "checkUpdates" && s.check) {
        if (s.idx >= currentStep) setCheckProgress(s.idx, s.label);
        return;
      }
      if (currentAction === "checkUpdates") return;
      if (s.idx >= currentStep) {
        currentStep = s.idx;
        const label = (currentAction === "disable" && REVERT_LABELS[s.idx])
          ? REVERT_LABELS[s.idx]
          : (actionStartState === "outdated" && UPDATE_LABELS[s.idx])
          ? UPDATE_LABELS[s.idx]
          : s.label;
        stepLabelEl.textContent = label + "…";
        stepSubEl.textContent = "Step " + s.idx + " of " + TOTAL_STEPS;
        progressFillEl.style.width = (s.idx / TOTAL_STEPS * 100) + "%";
      }
      return;
    }
  }
}

window.addEventListener("message", (ev) => {
  const m = ev.data;
  if (m.type === "state") {
    lastIdleState = m.state;
    const labels = {
      patched: ["patched", "Orbit patched"],
      outdated: ["outdated", "Experimental update available"],
      stock: ["stock", "Original Claude Code"],
      none: ["none", "Claude Code not installed"],
    };
    const [cls, text] = labels[m.state] || labels.none;
    statusEl.innerHTML = '<span class="dot ' + cls + '"></span><span class="label">' + text + '</span>';
    applyIdleState(m.state, {
      installedVersion: m.installedVersion,
      bundledVersion: m.bundledVersion,
      claudeCodeVersion: m.claudeCodeVersion,
      latestClaudeVersion: m.latestClaudeVersion,
      latestClaudeVerified: m.latestClaudeVerified,
      onLatestClaude: m.onLatestClaude,
      claudeUpdateAvailable: m.claudeUpdateAvailable,
      patcherHistory: m.patcherHistory,
    });
    lastHistory = Array.isArray(m.patcherHistory) ? m.patcherHistory : [];
    lastInstalledVersion = m.installedVersion || null;
    return;
  }
  if (m.type === "log") {
    logBuf += (logBuf ? "\\n" : "") + m.line;
    logEl.textContent = logBuf;
    if (logEl.classList.contains("visible")) logEl.scrollTop = logEl.scrollHeight;
    updateProgress(m.line);
    return;
  }
  if (m.type === "lockCancel") {
    const cb = document.getElementById("cancelBtn");
    if (cb) cb.hidden = true;
    return;
  }
  if (m.type === "phase") {
    if (m.phase === "working") {
      currentStep = 0;
      currentAction = m.action || null;
      actionStartState = lastIdleState;
      if (currentAction === "checkUpdates") setCheckProgress(1, "Checking GitHub");
      // Offer Cancel only for the long, reversible flows (download/patch before
      // the extension swap). The quick version check has nothing worth cancelling.
      const cb = document.getElementById("cancelBtn");
      if (cb) {
        const cancellable = ["enable", "enableStable", "enablePrevious", "disable", "updateWrapper"].indexOf(currentAction) !== -1;
        cb.hidden = !cancellable;
        cb.disabled = false;
        cb.textContent = "Cancel";
      }
      setPane("working");
    } else if (m.phase === "cancelled") {
      resetWorkingState();
      resetCancelButton();
      setPane("idle");
    } else if (m.phase === "done") {
      resetCancelButton();
      progressFillEl.style.width = "100%";
      doneMsgEl.textContent = m.message || "All set.";
      const doneSub = document.getElementById("doneSub");
      if (m.subHtml) doneSub.innerHTML = m.subHtml;
      else doneSub.textContent = m.subMessage || "";
      donePrimaryBtn.hidden = false;
      donePrimaryBtn.disabled = false;
      donePrimaryBtn.dataset.action = "restart";
      donePrimaryBtn.innerHTML = '<svg viewBox="0 0 14 14" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M12.5 7a5.5 5.5 0 1 1-1.7-3.95"/><polyline points="13,1 13,4.2 9.8,4.2"/></svg>Restart Claude Code';
      if (m.action === "checkUpdates" && m.updateAvailable) {
        donePrimaryBtn.dataset.action = m.updateAction || "enable";
        donePrimaryBtn.innerHTML = m.updateAction === "updateWrapper" ? "Update Orbit wrapper" : "Install experimental";
      } else if (m.action === "checkUpdates") {
        donePrimaryBtn.hidden = true;
      } else if (!doneSub.textContent) {
        doneSub.textContent = "Reload VS Code for the change to take effect.";
      }
      setPane("done");
    } else if (m.phase === "error") {
      resetCancelButton();
      errorSubEl.textContent = m.message || "Unknown error.";
      logEl.classList.add("visible");
      detailsToggle.textContent = "Hide details";
      setPane("error");
    }
  }
});

vscode.postMessage({ type: "refresh" });
</script>
</body></html>`;
  }
}

// Lightweight GET that follows one redirect and gives up after OTA_TIMEOUT_MS.
// Doesn't pull in node-fetch or axios — keeps the wrapper VSIX dependency-free.
function httpsGet(url, timeoutMs) {
  return new Promise((resolve, reject) => {
    const req = https.get(url, { headers: { "User-Agent": "claude-code-orbit-vscode" } }, (res) => {
      if ((res.statusCode === 301 || res.statusCode === 302) && res.headers.location) {
        res.resume();
        resolve(httpsGet(res.headers.location, timeoutMs));
        return;
      }
      if (res.statusCode !== 200) {
        res.resume();
        reject(new Error("HTTP " + res.statusCode));
        return;
      }
      const chunks = [];
      res.on("data", (c) => chunks.push(c));
      res.on("end", () => resolve(Buffer.concat(chunks).toString("utf8")));
      res.on("error", reject);
    });
    req.on("error", reject);
    req.setTimeout(timeoutMs, () => { req.destroy(new Error("timeout after " + timeoutMs + "ms")); });
  });
}

function httpsDownload(url, dest, timeoutMs) {
  return new Promise((resolve, reject) => {
    const req = https.get(url, { headers: { "User-Agent": "claude-code-orbit-vscode" } }, (res) => {
      if ((res.statusCode === 301 || res.statusCode === 302) && res.headers.location) {
        res.resume();
        resolve(httpsDownload(res.headers.location, dest, timeoutMs));
        return;
      }
      if (res.statusCode !== 200) {
        res.resume();
        reject(new Error("HTTP " + res.statusCode));
        return;
      }
      const chunks = [];
      res.on("data", (c) => chunks.push(c));
      res.on("end", () => {
        fs.writeFileSync(dest, Buffer.concat(chunks));
        resolve(dest);
      });
      res.on("error", reject);
    });
    req.on("error", reject);
    req.setTimeout(timeoutMs, () => { req.destroy(new Error("timeout after " + timeoutMs + "ms")); });
  });
}

// Pull the latest patcher from the public OTA repo. Sanity-checks the payload
// looks like our patcher (must contain `patch_webview_js`) so a misconfigured
// raw URL doesn't silently write garbage. Returns the cached file path on
// success, or null to signal the caller should use the bundled fallback.
async function fetchOtaPatcher(context, log) {
  try {
    const url = OTA_PATCHER_URL + "?t=" + Date.now();
    log("Fetching OTA patcher: " + url);
    const body = await httpsGet(url, OTA_TIMEOUT_MS);
    if (body.indexOf("def patch_webview_js") === -1) {
      throw new Error("payload missing patch_webview_js marker (got " + body.length + " bytes)");
    }
    const dir = context.globalStorageUri.fsPath;
    fs.mkdirSync(dir, { recursive: true });
    const outPath = path.join(dir, "patch_claude_ota.py");
    fs.writeFileSync(outPath, body, "utf8");
    log("OTA patcher loaded (" + body.length + " bytes)");
    return outPath;
  } catch (err) {
    log("OTA patcher unavailable (" + (err && err.message ? err.message : err) + ") — using bundled");
    return null;
  }
}

// Pull the OTA stable-version pin. Falls back to STABLE_CLAUDE_VERSION when
// the file doesn't exist or isn't reachable.
async function fetchOtaStableVersion(log) {
  try {
    const body = await httpsGet(OTA_STABLE_VERSION_URL, OTA_TIMEOUT_MS);
    const v = body.trim().split(/\s+/)[0];
    if (!/^\d+\.\d+\.\d+/.test(v)) throw new Error("not a version: " + JSON.stringify(v));
    log("OTA stable version pin: " + v);
    return v;
  } catch (err) {
    log("OTA stable version unavailable (" + (err && err.message ? err.message : err) + ") — using bundled " + (readBundledStableVersion({ extensionUri: { fsPath: '' } }) || STABLE_CLAUDE_VERSION_FALLBACK));
    return null;
  }
}

// Read the production patcher version shipped inside this VSIX.
// detectState() uses this as an offline fallback when the remote patcher
// version from GitHub is unavailable.
function readBundledPatcherVersion(context) {
  try {
    const stablePath = path.join(context.extensionUri.fsPath, "stable", "patcher_version.txt");
    const legacyPath = path.join(context.extensionUri.fsPath, "patch_version.txt");
    const p = fs.existsSync(stablePath) ? stablePath : legacyPath;
    return fs.readFileSync(p, "utf8").trim() || null;
  } catch (_) {
    return null;
  }
}

// Read the stable Claude Code version shipped inside this VSIX.
// Returns the version string, or the hardcoded fallback if the file is
// missing / unreadable.
function readBundledStableVersion(context) {
  try {
    const stablePath = path.join(context.extensionUri.fsPath, "stable", "stable_version.txt");
    const legacyPath = path.join(context.extensionUri.fsPath, "STABLE_VERSION.txt");
    const p = fs.existsSync(stablePath) ? stablePath : legacyPath;
    const v = fs.readFileSync(p, "utf8").trim().split(/\s+/)[0];
    if (!/^\d+\.\d+\.\d+/.test(v)) throw new Error("not a version: " + JSON.stringify(v));
    return v;
  } catch (_) {
    return STABLE_CLAUDE_VERSION_FALLBACK;
  }
}

function cmpVer(a, b) {
  if (!a || !b) return 0;
  const pa = String(a).split(".").map(n => parseInt(n, 10) || 0);
  const pb = String(b).split(".").map(n => parseInt(n, 10) || 0);
  for (let i = 0; i < Math.max(pa.length, pb.length); i++) {
    const x = pa[i] || 0, y = pb[i] || 0;
    if (x !== y) return x < y ? -1 : 1;
  }
  return 0;
}

function detectState(context) {
  const bundledVersion = readBundledPatcherVersion(context);
  // Read the remote patcher version cached by the background poller into
  // globalState. This is the PRIMARY source of truth for "is my patcher
  // outdated?" — the bundled version only serves as an offline fallback.
  const remoteVersion = context.globalState.get(GS_REMOTE_PATCHER_VERSION);
  // Load the rollback registry now so both "none" and "stock" states can
  // offer "Install specific version" — the picker needs version entries
  // and the bundled manifest is always present even before any OTA fetch.
  const manifest = getEffectiveManifest(context);
  const patcherHistoryFallback = patcherHistoryFromManifest(manifest);
  const ext = vscode.extensions.getExtension(STOCK_ID);
  if (!ext) return { state: "none", installedVersion: null, bundledVersion, remoteVersion, claudeCodeVersion: null, latestClaudeVersion: null, latestClaudeVerified: false, onLatestClaude: false, onStable: false, claudeUpdateAvailable: false, previousPatcher: null, patcherHistory: patcherHistoryFallback };

  // Claude Code's own version, read straight from its package.json via the
  // Extensions API. Lets us show "Patches active on Claude Code v2.1.150"
  // and decide whether the user is already on Stable.
  const claudeCodeVersion = (ext.packageJSON && ext.packageJSON.version) || null;
  // Newest Claude Code available (cached by the poller). onStable suppresses the
  // "newer Claude" hint — stable is a deliberate frozen pin, not a stale snapshot.
  const latestClaudeVersion = context.globalState.get(GS_LATEST_CLAUDE_VERSION) || null;
  const stablePin = readBundledStableVersion(context);
  const onStable = !!claudeCodeVersion && claudeCodeVersion === stablePin;
  // Is the newest available Claude one we've VERIFIED (<= the stable pin)? The
  // stable pin is the newest version the patcher was test-run against, so it is
  // our "verified up to" line. Drives "Update" (verified) vs "Try experimental
  // (unverified, may break)" wording.
  const latestClaudeVerified = !!latestClaudeVersion && cmpVer(latestClaudeVersion, stablePin) <= 0;
  // On the newest Claude we know about (installed >= latest). Only assertable
  // when we actually have a cached latest — never claim "Latest" blindly.
  const onLatestClaude = !!claudeCodeVersion && !!latestClaudeVersion && cmpVer(claudeCodeVersion, latestClaudeVersion) >= 0;

  try {
    const jsPath = path.join(ext.extensionUri.fsPath, "webview", "index.js");
    if (fs.existsSync(jsPath)) {
      const text = fs.readFileSync(jsPath, "utf8");
      const isPatched =
        text.indexOf("ccPatchSettingsBtn") !== -1 ||
        text.indexOf("ccPatchSessionItem") !== -1;
      if (isPatched) {
        const m = text.match(/ccPatchBuildVersion="([^"]+)"/);
        const installedVersion = m ? m[1] : null;
        // Determine "outdated" status:
        //   1. Primary: compare installed vs remote (fetched from GitHub by the
        //      background poller and cached in globalState).
        //   2. Fallback: if no remote version cached (offline / first launch),
        //      compare installed vs bundled (shipped in the Orbit VSIX).
        //   3. No marker at all => pre-versioning build, always treat as outdated.
        const isOutdated = !installedVersion
          || (remoteVersion && cmpVer(installedVersion, remoteVersion) < 0)
          || (!remoteVersion && bundledVersion && cmpVer(installedVersion, bundledVersion) < 0);
        // Experimental snapshot is behind the newest published Claude Code:
        // re-patching pulls the newer Claude and re-applies the same patcher.
        const claudeUpdateAvailable = !onStable && !!claudeCodeVersion && !!latestClaudeVersion
          && cmpVer(claudeCodeVersion, latestClaudeVersion) < 0;
        // Full version list (newest-first) for the "Previous versions" picker,
        // plus the single newest-older entry (kept for any internal callers).
        // Uses the bundled registry when the OTA cache is empty (pre-push).
        const manifest = getEffectiveManifest(context);
        const previousPatcher = pickPreviousPatcher(manifest, installedVersion);
        const patcherHistory = patcherHistoryFromManifest(manifest);
        return {
          state: isOutdated ? "outdated" : "patched",
          installedVersion,
          bundledVersion,
          remoteVersion,
          claudeCodeVersion,
          latestClaudeVersion,
          latestClaudeVerified,
          onLatestClaude,
          onStable,
          claudeUpdateAvailable,
          previousPatcher,
          patcherHistory,
        };
      }
    }
  } catch (_) {}
  return { state: "stock", installedVersion: null, bundledVersion, remoteVersion, claudeCodeVersion, latestClaudeVersion, latestClaudeVerified, onLatestClaude, onStable, claudeUpdateAvailable: false, previousPatcher: null, patcherHistory: patcherHistoryFallback };
}

async function findPython() {
  const candidates = process.platform === "win32" ? ["python", "python3", "py"] : ["python3", "python"];
  for (const c of candidates) {
    const r = await new Promise((res) => {
      const p = cp.spawn(c, ["--version"], { shell: false });
      p.on("error", () => res(null));
      p.on("close", (code) => res(code === 0 ? c : null));
    });
    if (r) return r;
  }
  return null;
}

function runPython(python, script, args, onLine, onSpawn) {
  return new Promise((resolve, reject) => {
    const p = cp.spawn(python, [script, ...args], { shell: false });
    // Hand the process to the caller so a user-requested cancel can kill the
    // download mid-flight (the long, fully-safe step before any uninstall).
    if (typeof onSpawn === "function") { try { onSpawn(p); } catch (_) {} }
    let stderr = "";
    const stream = (chunk) => {
      chunk.toString().split(/\r?\n/).forEach((l) => l && onLine(l));
    };
    p.stdout.on("data", stream);
    p.stderr.on("data", (d) => { stderr += d; stream(d); });
    p.on("error", reject);
    p.on("close", (code) => {
      if (code === 0) resolve();
      else reject(new Error("Patcher exited with code " + code + (stderr ? "\n" + stderr : "")));
    });
  });
}

function deactivate() {}
module.exports = { activate, deactivate };
