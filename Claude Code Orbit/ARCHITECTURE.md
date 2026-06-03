# Claude Code Orbit — Architecture & Update System

## Overview

Claude Code Orbit is a thin VS Code extension shell that downloads, patches, and installs the stock `anthropic.claude-code` extension at runtime. The Orbit VSIX itself rarely needs updating — **patcher updates are distributed Over-The-Air (OTA) from GitHub** without requiring a new VSIX install.

## Non-Negotiable Release Rule

Every user-facing fix must be pushed to the GitHub update path that Orbit checks
from the sidebar. Do not leave the user with only a local VSIX to reinstall.

The live updater reads from `Lunarwerx/claude-code-orbit`:

`https://raw.githubusercontent.com/Lunarwerx/claude-code-orbit/main`

In this workspace, push release commits to the `orbit` remote. Do not assume
`origin` is the updater repo; `origin` may point at `Lunarwerx/claude-script`
and will not satisfy `Check experimental updates`.

- Patcher fix: update `Claude Code/patch_claude_vsix_v147.py`, bump
  `patcher_version.txt`, commit, and push to `orbit main`.
- Wrapper/sidebar/updater fix: bump `Claude Code Orbit/package.json`, run
  `python build.py`, commit `wrapper_version.txt` and
  `latest/claude-code-orbit.vsix`, and push to `orbit main`.
- Certified Claude pin: when the patcher has been verified against a new Claude
  Code release, update `certified_claude.txt` so the next archived entry records
  it. There is no separate "stable" channel to promote.

The intended user flow is: click `Check for updates`, see an available patcher or
wrapper update, click the update button, then reload. If a release misbehaves,
`Previous versions` rolls back to an earlier registry entry. Manual VSIX reinstall
should be the exception, not the normal release path.

See `RELEASE_RULES.md` for the full operating rules.

## File Map

```
Claude Code Orbit/           ← The VS Code extension (this folder)
├── extension.js             ← All extension logic (sidebar, patching, OTA, polling)
├── package.json             ← Extension manifest + version (single source of truth)
└── media/                   ← Icons and images

Claude Code/                 ← The patcher script (Python)
├── patch_claude_vsix_v147.py ← Applies UI patches to Claude Code's webview/index.js
└── builds/                  ← Historical patcher variants

build.py                     ← Build script: packages Orbit + patcher into a VSIX
builds/                      ← Output VSIX files (claude-code-orbit-N.vsix)
patchers/                    ← Rollback registry: manifest.json + every archived patcher
patcher_version.txt          ← OTA patcher version pin — lives on GitHub
certified_claude.txt         ← Claude Code version the current patcher is certified against
```

## The OTA Update Architecture

### Version Truth Sources (3 tiers)

| Tier | File | Location | Purpose |
|------|------|----------|---------|
| **Primary** | `patcher_version.txt` | GitHub repo root | The live patcher version. Bump this when you push a patcher fix. Polled every 4 hours. |
| **Bundled** | `patch_version.txt` | Inside the VSIX (`extension/patch_version.txt`) | Offline fallback. Set by `build.py` from `package.json` version. Only changes when a new VSIX is published. |
| **Installed** | `ccPatchBuildVersion` | Injected into Claude Code's `webview/index.js` | What's actually running. Read by `detectState()` to determine if an update is needed. |

### Data Flow

```
┌─────────────────────────────────────────────────────────┐
│                    GitHub Repo                           │
│  Lunarwerx/claude-code-orbit                            │
│                                                         │
│  ├── Claude Code/patch_claude_vsix_v147.py  ← patcher   │
│  ├── patchers/manifest.json ← rollback registry         │
│  └── patcher_version.txt   ← PATCHER version pin        │
└──────────────┬──────────────────────────────────────────┘
               │ raw.githubusercontent.com
               ▼
┌─────────────────────────────────────────────────────────┐
│              Orbit Extension (running in VS Code)        │
│                                                         │
│  ┌──────────────────────────────────────────────┐       │
│  │         Background Poller                     │       │
│  │  • Runs 30s after activation                 │       │
│  │  • Repeats every 4 hours                     │       │
│  │  • Fetches patcher_version.txt               │       │
│  │  • Compares remote vs installed              │       │
│  │  • Caches remote in globalState              │       │
│  │  • Shows notification + status bar badge     │       │
│  └──────────────────────────────────────────────┘       │
│                         │                               │
│                         ▼                               │
│  ┌──────────────────────────────────────────────┐       │
│  │         detectState()                         │       │
│  │  • Reads globalState for remoteVersion       │       │
│  │  • Reads Claude Code webview for installed   │       │
│  │  • PRIMARY: compare installed vs remote      │       │
│  │  • FALLBACK: compare installed vs bundled    │       │
│  │  → Returns: patched | outdated | stock | none│       │
│  └──────────────────────────────────────────────┘       │
│                         │                               │
│                         ▼                               │
│  ┌──────────────────────────────────────────────┐       │
│  │         enable() — Update Flow                │       │
│  │  1. Fetch OTA patcher from GitHub            │       │
│  │  2. Fall back to bundled if offline           │       │
│  │  3. Download stock Claude Code VSIX           │       │
│  │  4. Run patcher (--patcher-version X.Y.Z)    │       │
│  │  5. Uninstall old Claude Code                 │       │
│  │  6. Install patched VSIX                      │       │
│  └──────────────────────────────────────────────┘       │
└─────────────────────────────────────────────────────────┘
```

## How To Ship a Patcher Update

### You do NOT need to publish a new Orbit VSIX. Just:

1. **Edit the patcher** → `Claude Code/patch_claude_vsix_v147.py` on GitHub
2. **Bump the patcher version** → Edit `patcher_version.txt` on GitHub (e.g., `1.2.65` → `1.2.66`)
3. **Archive it** → run `python tools/archive_patcher.py` so the new version (and its `certified_claude.txt` pin) lands in the rollback registry.
4. **Done.** Within 4 hours, every user gets a notification: *"Patcher v1.2.66 is available. Update now?"*

### The Release Checklist (every time you ship a patcher update)

```
□ 1. Test the patcher against the LATEST Claude Code from the marketplace
□ 2. Fix any breakage caused by Claude Code's webview changes
□ 3. Update certified_claude.txt if you verified against a newer Claude Code
□ 4. Bump patcher_version.txt on GitHub to the new version
□ 5. Archive: python tools/archive_patcher.py (registry + rollback)
□ 6. Push patcher_version.txt + patcher + patchers/ to GitHub
□ 7. Confirm Orbit's "Check for updates" path sees the new patcher
```

**If the newest patcher breaks for someone:** they click **Previous versions** and
install an earlier registry entry — each pinned (via `--version`) to the Claude
Code version it was certified against, so the rollback is always a known-good
(patcher, Claude) pair. There is no separate channel to maintain.

### When you DO need to publish a new Orbit VSIX:

Only when `extension.js` or `package.json` changes (new features in the wrapper itself, not the patcher). Run:
```bash
python build.py
```
This auto-increments the build number, archives the current patcher into the
registry, bundles the registry (newest entry = offline fallback), writes
`patch_version.txt`, and outputs to `builds/`.

### Important: keep these in sync when publishing a new VSIX:
- `package.json` → `"version"` field
- `patcher_version.txt` on GitHub → same version
- `patchers/manifest.json` → archived by `build.py` / `archive_patcher.py`
- The Marketplace upload will carry the new `patch_version.txt` (bundled fallback)

## globalState Keys

The extension uses VS Code's `globalState` for cross-session persistence:

| Key | Type | Purpose |
|-----|------|---------|
| `claudeCodeOrbit.remotePatcherVersion` | `string` | Latest patcher version seen from GitHub. Set by background poller. Read by `detectState()`. |
| `claudeCodeOrbit.lastNotifiedVersion` | `string` | Last version the user was notified about. Prevents duplicate toasts. |

## Key Functions in extension.js

| Function | Purpose |
|----------|---------|
| `fetchRemotePatcherVersion(log)` | HTTP GET `patcher_version.txt` from GitHub. Returns version string or null. |
| `readInstalledPatcherVersion()` | Reads `ccPatchBuildVersion` from Claude Code's patched webview. |
| `checkForPatcherUpdate(context, provider, statusBarItem)` | Core poll cycle: fetch → compare → notify → update status bar. |
| `startBackgroundPolling(context, provider, statusBarItem)` | Sets up the 30s-delayed initial check + 4-hour interval. |
| `detectState(context)` | Synchronous state detection for the sidebar. Uses remote (globalState) as primary, bundled as fallback. |
| `fetchOtaPatcher(context, log)` | Fetches the full patcher `.py` file from GitHub on every Install click. |
| `bundledFallbackPatcher(context)` / `newestBundledEntry(context)` | Resolve the offline fallback patcher (newest bundled registry entry) and its certified-Claude tag. |
| `enablePrevious(version)` | Installs an archived patcher pinned to its certified Claude version (the "Previous versions" rollback). |
| `SidebarProvider.triggerEnable()` | Entry point for the notification's "Update" button. Delegates to `onMessage({ type: "action", action: "enable" })`. |

## Notification Dedup Logic

```
checkForPatcherUpdate() runs every 4 hours
  → Fetches remoteVersion from GitHub
  → Compares installedVersion vs remoteVersion
  → If remote > installed:
      → Status bar: ⬇ "Orbit Update" (always shows)
      → Notification: only if lastNotifiedVersion !== remoteVersion
      → After showing: lastNotifiedVersion = remoteVersion
```

This means:
- The status bar badge persists until the user updates
- The toast notification only fires ONCE per version
- If the user clicks "Later", they'll still see the status bar badge
- Opening the sidebar shows the "Update available" state with the "Update patches" button

## 🚫 Version Bump Protection

**DO NOT bump any version number without Jacob's explicit permission.** This includes:
- `package.json` → `"version"` field
- `patcher_version.txt` on GitHub
- `certified_claude.txt` (the Claude version the patcher is certified against)
- Any other version pin anywhere in the codebase

Rebuilding the VSIX for local testing does **not** require a version bump — `build.py` auto-increments the build number in the filename (`claude-code-orbit-N.vsix`) without touching `package.json`.

## 🧪 Local Testing Workflow (Dev Mode)

### The problem
Normally, Orbit fetches the patcher from GitHub on every "Enable" click. This means you can't test local patcher changes without pushing to GitHub first.

### The solution: `claudeCodeOrbit.devMode`

Enable dev mode in VS Code settings (`Ctrl+,` → search "Orbit dev") or add to `settings.json`:
```json
"claudeCodeOrbit.devMode": true
```

**What dev mode does:**
| Behavior | Normal | Dev Mode |
|----------|--------|----------|
| Patcher source | GitHub OTA (primary), bundled registry (fallback) | **Bundled only** (skips GitHub) |
| Poll interval | 4 hours | **60 seconds** |
| Startup delay | 30 seconds | **5 seconds** |
| Status bar | "✓ Orbit" or "⬇ Orbit Update" | **"🧪 Orbit Dev"** |

### Step-by-step: test a patcher change locally

```
□ 1. Edit the patcher:    Claude Code/patch_claude_vsix_v147.py
□ 2. Rebuild the VSIX:    python build.py
□ 3. Install the VSIX:    code --install-extension builds/claude-code-orbit-N.vsix
□ 4. Reload VS Code:      Ctrl+Shift+P → "Reload Window"
□ 5. Enable dev mode:     Settings → claudeCodeOrbit.devMode = true
□ 6. Reload again:        (dev mode only takes effect on activation)
□ 7. Open Orbit sidebar → Click "Enable Orbit"
□ 8. Verify your changes work
□ 9. Iterate:             Edit → python build.py → install → reload → test
```

**Rebuilding does NOT bump the version.** You can run `python build.py` 30 times in a row — it just creates `claude-code-orbit-40.vsix`, `claude-code-orbit-41.vsix`, etc. The version in `package.json` stays the same.

### When you're ready to ship

1. Turn OFF dev mode
2. Push the patcher + `patcher_version.txt` to GitHub
3. Bump `patcher_version.txt` (this is the only version bump needed for a patcher-only update)
4. Users get notified within 4 hours
