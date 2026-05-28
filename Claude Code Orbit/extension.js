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

// OTA patcher-version pin — separate from stable_version.txt (which pins the
// Claude Code version). This file contains just the patcher version (e.g. "1.1.4").
// Orbit polls this in the background so users get notified the moment a new
// patcher lands on GitHub — no VSIX reinstall needed.
const OTA_PATCHER_VERSION_URL = OTA_BASE + "/patcher_version.txt";

// Background polling: how often to check GitHub for a new patcher version.
const POLL_INTERVAL_MS = 4 * 60 * 60 * 1000; // 4 hours
const STARTUP_DELAY_MS = 30 * 1000;           // wait 30s before first check

// globalState keys for cross-session persistence of the remote version and
// notification deduplication.
const GS_REMOTE_PATCHER_VERSION = "claudeCodeOrbit.remotePatcherVersion";
const GS_LAST_NOTIFIED_VERSION = "claudeCodeOrbit.lastNotifiedVersion";

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

    const installedVersion = readInstalledPatcherVersion();
    if (!installedVersion) {
      // Claude Code isn't patched yet — nothing to compare against.
      statusBarItem.text = "$(check) Orbit";
      statusBarItem.backgroundColor = undefined;
      statusBarItem.show();
      return;
    }

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
        else if (msg.action === "disable") await this.disable();
        else if (msg.action === "updateWrapper") await this.updateWrapper();
        else if (msg.action === "checkUpdates") {
          let resultMsg = "";
          let resultSub = "";
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
            const installedVersion = readInstalledPatcherVersion();
            const wrapperVersion = readBundledWrapperVersion(this.context);
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
            } else {
              resultMsg = "No experimental update found.";
              resultSub = "GitHub experimental patcher: v" + remoteVersion + ". Installed Claude Code patcher: v" + installedVersion + ". Orbit wrapper UI: v" + wrapperVersion + ".";
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
              ? "Stable Orbit installed."
              : "Experimental Orbit installed.",
          subMessage: msg.action === "updateWrapper"
            ? "Reload VS Code to start the updated Orbit sidebar."
            : msg.action === "enableStable"
            ? "Reload VS Code to start Claude Code from the stable patched bundle."
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
              ? "The swap failed partway, so Claude Code may currently be uninstalled. Use the buttons below to reinstall it (Use stable / Use experimental).\n\n" + baseMsg
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
    ];
    const recsHtml = recs.map(r => `
      <button class="recItem" data-ext-id="${r.id}" title="Open ${r.name} in Extensions">
        <img class="recIcon" src="${r.icon}" alt=""/>
        <div class="recBody">
          <div class="recName">${r.name}</div>
          <div class="recTag">${r.tag}</div>
        </div>
        <span class="recArrow">›</span>
      </button>`).join("");
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
.statePane[data-pane="error"]{flex:1;justify-content:center;align-items:stretch;
  padding-bottom:20vh}
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

/* Alt-action: small text link styled like "Use stable v2.1.150 instead" */
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
.errorSub{font-family:ui-monospace,Consolas,monospace;font-size:11.5px;opacity:.85;
  background:rgba(239,68,68,.06);padding:9px 11px;border-radius:5px;text-align:left;
  max-height:140px;overflow:auto;border-left:2px solid rgba(239,68,68,.4)}

/* recommended extensions block — sits above the footer */
.recommended{margin-top:auto;padding-top:18px;flex-shrink:0}
.recHeader{font-size:10px;font-weight:600;letter-spacing:.16em;text-transform:uppercase;
  opacity:.45;text-align:center;margin:0 0 10px}
.recList{display:flex;flex-direction:column;gap:6px}
.recItem{display:flex;align-items:center;gap:10px;width:100%;text-align:left;
  padding:8px 10px;border:1px solid rgba(127,127,127,.14);border-radius:7px;
  background:rgba(127,127,127,.05);color:var(--vscode-foreground);cursor:pointer;
  font:inherit;transition:background .12s ease,border-color .12s ease,transform .08s ease}
.recItem:hover{background:rgba(127,127,127,.12);border-color:rgba(127,127,127,.28)}
.recItem:active{transform:translateY(1px)}
.recIcon{width:26px;height:26px;flex-shrink:0;border-radius:5px;object-fit:contain}
.recBody{flex:1;min-width:0}
.recName{font-size:12px;font-weight:600;line-height:1.2;
  overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.recTag{font-size:10.5px;opacity:.55;line-height:1.35;margin-top:2px;
  overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.recArrow{opacity:.4;font-size:16px;line-height:1;flex-shrink:0}

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
      </div>
      <div class="stockHero" id="stockHero" hidden>
        <p class="patchedTitle">Original Claude Code</p>
        <p class="patchedSub" id="stockSub">Claude Code is installed without Orbit patches.</p>
      </div>
      <button class="btn" id="enableBtn" data-action="enable">
        <span class="btnIcon" id="enableBtnIcon"></span>
        <span class="btnLabel" id="enableBtnLabel">Use experimental</span>
      </button>
      <button class="btn" id="disableBtn" data-action="disable">
        <svg viewBox="0 0 14 14" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"><circle cx="7" cy="7" r="5"/></svg>
        Restore original
      </button>
      <button class="btn" id="checkUpdatesBtn" data-action="checkUpdates">
        <svg viewBox="0 0 14 14" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M12.5 7a5.5 5.5 0 1 1-1.7-3.95"/><polyline points="13,1 13,4.2 9.8,4.2"/></svg>
        Check experimental updates
      </button>
      <button class="altAction" id="stableBtn" data-action="enableStable" hidden
              title="Use the bundled known-good production patcher and pinned Claude Code version.">
        Use stable v${stableVersion}
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

    <!-- ERROR -->
    <div class="statePane" data-pane="error">
      <div class="iconCircle error">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><line x1="12" y1="8" x2="12" y2="13"/><line x1="12" y1="16.5" x2="12" y2="16.5"/><circle cx="12" cy="12" r="9"/></svg>
      </div>
      <p class="errorMsg">Something went wrong.</p>
      <p class="errorSub" id="errorSub"></p>
      <button class="btn primary" data-action="enableStable">Use stable v${stableVersion}</button>
      <button class="btn" data-action="back">Back</button>
    </div>

  </div>

  <div class="recommended">
    <div class="recHeader">Recommended Extensions</div>
    <div class="recList">${recsHtml}</div>
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
  error: document.querySelector('[data-pane="error"]'),
};
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
const stockHero = document.getElementById("stockHero");
const stockSub = document.getElementById("stockSub");
const stableBtn = document.getElementById("stableBtn");

let logBuf = "";
let lastIdleState = null;        // tracks most recent state from "state" message
let actionStartState = null;     // snapshot of state when working phase begins

const ICON_CHECK = '<svg viewBox="0 0 14 14" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M2 7l3 3 7-7"/></svg>';
const ICON_REFRESH = '<svg viewBox="0 0 14 14" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M12.5 7a5.5 5.5 0 1 1-1.7-3.95"/><polyline points="13,1 13,4.2 9.8,4.2"/></svg>';

function applyIdleState(state, info) {
  const installedVersion = info && info.installedVersion;
  const bundledVersion = info && info.bundledVersion;
  const claudeCodeVersion = info && info.claudeCodeVersion;
  const onStable = claudeCodeVersion && claudeCodeVersion === STABLE_CLAUDE_VERSION;

  // Always render Use experimental + Restore original; gray the inapplicable one per state.
  enableBtnIcon.innerHTML = ICON_CHECK;
  enableBtnLabel.textContent = "Use experimental";

  // Reset both buttons + hero variant to neutral, then apply per-state styling.
  enableBtn.classList.remove("primary");
  disableBtn.classList.remove("primary");
  enableBtn.disabled = false;
  disableBtn.disabled = false;
  enableBtn.hidden = false;
  disableBtn.hidden = false;
  // Check for updates: only relevant when patches are already applied
  checkUpdatesBtn.hidden = true;
  // Stable alt-action: shown whenever Claude Code is installed.
  // We don't hide it when onStable — "Enable Orbit" always pulls newest, so the
  // user needs both buttons present to choose between newest vs. pinned-stable.
  stableBtn.hidden = !claudeCodeVersion;
  statusEl.hidden = false;
  patchedHero.classList.remove("updateAvailable");

  if (state === "patched") {
    patchedHero.hidden = false;
    stockHero.hidden = true;
    statusEl.hidden = true;                // hide redundant pill — hero card says it
    patchedTitle.textContent = "Orbit is enabled";
    {
      let parts = [];
      if (installedVersion) parts.push("Patches v" + installedVersion);
      if (claudeCodeVersion) {
        parts.push("Claude Code v" + claudeCodeVersion + (onStable ? " (Stable)" : ""));
      }
      patchedSub.textContent = parts.length
        ? parts.join(" · ") + "."
        : "Claude Code is running with the patches applied.";
    }
    enableBtn.hidden = true;               // hide entirely — patched hero already says "enabled"
    checkUpdatesBtn.hidden = false;
    disableBtn.classList.add("primary");
    disableBtn.title = "";
    idleHint.innerHTML = 'Use experimental pulls the current GitHub patcher. Use stable installs the bundled production patcher.';
  } else if (state === "outdated") {
    patchedHero.hidden = false;
    patchedHero.classList.add("updateAvailable");
    stockHero.hidden = true;
    statusEl.hidden = true;
    patchedTitle.textContent = "Update available";
    {
      let line = installedVersion
        ? "Patches v" + installedVersion + " → v" + (bundledVersion || "?")
        : "Legacy patches → v" + (bundledVersion || "?");
      if (claudeCodeVersion) line += " · Claude Code v" + claudeCodeVersion + (onStable ? " (Stable)" : "");
      line = (installedVersion
        ? "Experimental patcher v" + installedVersion + " -> v" + (bundledVersion || "?")
        : "Legacy patches -> experimental v" + (bundledVersion || "?"))
        + (claudeCodeVersion ? " - Claude Code v" + claudeCodeVersion + (onStable ? " (Stable)" : "") : "");
      patchedSub.textContent = line + ".";
    }
    enableBtnIcon.innerHTML = ICON_REFRESH;
    enableBtnLabel.textContent = "Update experimental";
    enableBtn.classList.add("primary");
    enableBtn.title = "Download Claude Code and patch it with the current experimental GitHub patcher (v" + (bundledVersion || "?") + ").";
    checkUpdatesBtn.hidden = false;
    disableBtn.title = "";
    idleHint.innerHTML = 'Experimental tracks the current GitHub patcher. Stable stays on the bundled known-good patcher.';
  } else if (state === "stock") {
    patchedHero.hidden = true;
    stockHero.hidden = false;
    statusEl.hidden = true;                // hide pill — stock hero card says it
    stockSub.textContent = claudeCodeVersion
      ? "Claude Code v" + claudeCodeVersion + (onStable ? " (Stable)" : "") + " — no Orbit patches yet."
      : "Claude Code is installed without Orbit patches.";
    enableBtn.classList.add("primary");
    enableBtn.title = "";
    disableBtn.hidden = true;              // hide entirely — nothing to restore
    idleHint.innerHTML = 'Experimental downloads <code>anthropic.claude-code</code>, pulls the current GitHub patcher, and installs it.';
  } else {
    patchedHero.hidden = true;
    stockHero.hidden = true;
    enableBtn.disabled = true;
    enableBtn.title = "Install Claude Code first.";
    disableBtn.disabled = true;
    disableBtn.title = "Install Claude Code first.";
    stableBtn.hidden = true;               // nothing to install over yet
    idleHint.innerHTML = 'Install <code>anthropic.claude-code</code> from the Marketplace, then come back here.';
  }
}

function setPane(name) {
  Object.keys(panes).forEach(k => panes[k].classList.toggle("active", k === name));
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
    vscode.postMessage({ type: "action", action });
  });
});

document.querySelectorAll(".recItem").forEach(el => {
  el.addEventListener("click", () => {
    const id = el.dataset.extId;
    if (id) vscode.postMessage({ type: "openExtension", id });
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
    });
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
        const cancellable = ["enable", "enableStable", "disable", "updateWrapper"].indexOf(currentAction) !== -1;
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
      doneSub.textContent = m.subMessage || "";
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
  const ext = vscode.extensions.getExtension(STOCK_ID);
  if (!ext) return { state: "none", installedVersion: null, bundledVersion, remoteVersion, claudeCodeVersion: null };

  // Claude Code's own version, read straight from its package.json via the
  // Extensions API. Lets us show "Patches active on Claude Code v2.1.150"
  // and decide whether the user is already on Stable.
  const claudeCodeVersion = (ext.packageJSON && ext.packageJSON.version) || null;

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
        return {
          state: isOutdated ? "outdated" : "patched",
          installedVersion,
          bundledVersion,
          remoteVersion,
          claudeCodeVersion,
        };
      }
    }
  } catch (_) {}
  return { state: "stock", installedVersion: null, bundledVersion, remoteVersion, claudeCodeVersion };
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
