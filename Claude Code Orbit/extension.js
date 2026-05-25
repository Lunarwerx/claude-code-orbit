const vscode = require("vscode");
const path = require("path");
const fs = require("fs");
const cp = require("child_process");
const os = require("os");

const STOCK_ID = "anthropic.claude-code";
// Last Claude Code version verified against the current patch set. If "newest"
// fails (Anthropic shipped a breaking change), Orbit offers to install this
// specific version instead — all 50 patch anchors are guaranteed to match.
const STABLE_CLAUDE_VERSION = "2.1.150";

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
}

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
    if (msg.type === "action" && !this.busy) {
      this.busy = true;
      this.send("phase", { phase: "working", action: msg.action });
      try {
        if (msg.action === "enable") await this.enable(false);
        else if (msg.action === "enableStable") await this.enable(true);
        else if (msg.action === "disable") await this.disable();
        this.send("phase", {
          phase: "done",
          action: msg.action,
          message: msg.action === "disable" ? "Original Claude Code restored." : "Orbit installed.",
        });
      } catch (err) {
        this.send("phase", {
          phase: "error",
          message: String(err && err.message ? err.message : err),
        });
      } finally {
        this.busy = false;
        this.pushState();
      }
    }
  }

  async enable(useStable) {
    const python = await findPython();
    if (!python) throw new Error("Python not found on PATH. Install Python 3 and retry.");
    this.log("Using Python: " + python);

    const work = fs.mkdtempSync(path.join(os.tmpdir(), "claude-orbit-"));
    const patcher = path.join(this.context.extensionUri.fsPath, "patcher", "patch_claude.py");
    const out = path.join(work, "patched.vsix");

    // Pass our bundled version so the patcher stamps it as ccPatchBuildVersion
    // in the patched webview. detectState() reads it back later to know whether
    // an installed patch is current or behind a newer Orbit release.
    const patcherVersion = readBundledPatcherVersion(this.context) || "dev";
    const args = [STOCK_ID, "--out", out, "--download-dir", work, "--patcher-version", patcherVersion];
    if (useStable) {
      args.push("--version", STABLE_CLAUDE_VERSION);
      this.log("Downloading + patching stable " + STOCK_ID + " v" + STABLE_CLAUDE_VERSION + " (patcher v" + patcherVersion + ")");
    } else {
      this.log("Downloading + patching latest " + STOCK_ID + " (patcher v" + patcherVersion + ")");
    }
    await runPython(python, patcher, args, (line) => this.log(line));

    this.log("Uninstalling current " + STOCK_ID);
    if (vscode.extensions.getExtension(STOCK_ID)) {
      await vscode.commands.executeCommand("workbench.extensions.uninstallExtension", STOCK_ID);
    }
    this.log("Installing patched VSIX");
    await vscode.commands.executeCommand("workbench.extensions.installExtension", vscode.Uri.file(out));
  }

  async disable() {
    const python = await findPython();
    if (!python) throw new Error("Python not found on PATH. Install Python 3 and retry.");
    this.log("Using Python: " + python);

    const work = fs.mkdtempSync(path.join(os.tmpdir(), "claude-orbit-"));
    const patcher = path.join(this.context.extensionUri.fsPath, "patcher", "patch_claude.py");

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
      }
    );

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
    return `<!doctype html>
<html><head>
<meta charset="utf-8"/>
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src ${this.view.webview.cspSource}; style-src 'unsafe-inline'; script-src 'nonce-${nonce}';"/>
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
        <span class="btnLabel" id="enableBtnLabel">Enable Orbit</span>
      </button>
      <button class="btn" id="disableBtn" data-action="disable">
        <svg viewBox="0 0 14 14" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"><circle cx="7" cy="7" r="5"/></svg>
        Restore original
      </button>
      <button class="altAction" id="stableBtn" data-action="enableStable" hidden
              title="Pin to a known-good Claude Code build instead of the very latest from the Marketplace.">
        Use stable v${STABLE_CLAUDE_VERSION} instead
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
    </div>

    <!-- DONE -->
    <div class="statePane" data-pane="done">
      <p class="doneMsg" id="doneMsg">All set.</p>
      <p class="doneSub">Restart Claude Code for the change to take effect.</p>
      <button class="btn primary" data-action="restart">
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
      <button class="btn primary" data-action="enableStable">Try stable v${STABLE_CLAUDE_VERSION}</button>
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
const STABLE_CLAUDE_VERSION = "${STABLE_CLAUDE_VERSION}";
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
const errorSubEl = document.getElementById("errorSub");
const logEl = document.getElementById("log");
const detailsToggle = document.getElementById("detailsToggle");
const logoEl = document.getElementById("logo");
const enableBtn = document.getElementById("enableBtn");
const enableBtnIcon = document.getElementById("enableBtnIcon");
const enableBtnLabel = document.getElementById("enableBtnLabel");
const disableBtn = document.getElementById("disableBtn");
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

  // Always render Enable Orbit + Restore original; gray the inapplicable one per state.
  enableBtnIcon.innerHTML = ICON_CHECK;
  enableBtnLabel.textContent = "Enable Orbit";

  // Reset both buttons + hero variant to neutral, then apply per-state styling.
  enableBtn.classList.remove("primary");
  disableBtn.classList.remove("primary");
  enableBtn.disabled = false;
  disableBtn.disabled = false;
  enableBtn.hidden = false;
  disableBtn.hidden = false;
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
    disableBtn.classList.add("primary");
    disableBtn.title = "";
    idleHint.innerHTML = 'To re-apply after a Claude Code update, restore original first, then enable again.';
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
      patchedSub.textContent = line + ".";
    }
    enableBtnIcon.innerHTML = ICON_REFRESH;
    enableBtnLabel.textContent = "Update patches";
    enableBtn.classList.add("primary");
    enableBtn.title = "Re-download and re-patch Claude Code with the latest Orbit patcher (v" + (bundledVersion || "?") + ").";
    disableBtn.title = "";
    idleHint.innerHTML = 'Click Update patches to download the latest Claude Code and apply the newest patch set.';
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
    idleHint.innerHTML = 'Enable downloads the latest <code>anthropic.claude-code</code>, applies UI patches, and installs it.';
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

// Map raw log lines to friendly progress steps.
// Keyed by first-match wins. Only update when the new step >= current step
// (so a late "Patching..." line doesn't go backwards from "Installing").
const STEPS = [
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
      outdated: ["outdated", "Update available"],
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
  if (m.type === "phase") {
    if (m.phase === "working") {
      currentStep = 0;
      currentAction = m.action || null;
      actionStartState = lastIdleState;
      setPane("working");
    } else if (m.phase === "done") {
      progressFillEl.style.width = "100%";
      doneMsgEl.textContent = m.message || "All set.";
      setPane("done");
    } else if (m.phase === "error") {
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

function readBundledPatcherVersion(context) {
  try {
    const p = path.join(context.extensionUri.fsPath, "patch_version.txt");
    return fs.readFileSync(p, "utf8").trim() || null;
  } catch (_) {
    return null;
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
  const ext = vscode.extensions.getExtension(STOCK_ID);
  if (!ext) return { state: "none", installedVersion: null, bundledVersion, claudeCodeVersion: null };

  // Claude Code's own version, read straight from its package.json via the
  // Extensions API. Lets us show "Patches active on Claude Code v2.1.150"
  // and decide whether the user is already on Stable.
  const claudeCodeVersion = (ext.packageJSON && ext.packageJSON.version) || null;

  try {
    const jsPath = path.join(ext.extensionUri.fsPath, "webview", "index.js");
    if (fs.existsSync(jsPath)) {
      const text = fs.readFileSync(jsPath, "utf8");
      const isPatched =
        text.indexOf("ccPatchYoloBtn") !== -1 ||
        text.indexOf("ccPatchUsageBtn") !== -1 ||
        text.indexOf("ccPatchSessionItem") !== -1;
      if (isPatched) {
        const m = text.match(/ccPatchBuildVersion="([^"]+)"/);
        const installedVersion = m ? m[1] : null;
        // No marker => pre-versioning build, treat as outdated.
        // Marker present but older than bundled => outdated.
        const isOutdated = !installedVersion || (bundledVersion && cmpVer(installedVersion, bundledVersion) < 0);
        return {
          state: isOutdated ? "outdated" : "patched",
          installedVersion,
          bundledVersion,
          claudeCodeVersion,
        };
      }
    }
  } catch (_) {}
  return { state: "stock", installedVersion: null, bundledVersion, claudeCodeVersion };
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

function runPython(python, script, args, onLine) {
  return new Promise((resolve, reject) => {
    const p = cp.spawn(python, [script, ...args], { shell: false });
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
