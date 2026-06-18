# Orbit Launcher — the ONE source (downstream products read this file)

The Orbit **launcher** (the VS Code sidebar extension: install / update /
previous-versions / the stable–experimental channel tags) is maintained **only
here**, in `Lunarwerx/claude-code-orbit`. Downstream Orbit products
(**Codex Orbit**, and any future one) do **not** keep their own copy of the
launcher. They **pull it from here at build time, rebrand the handful of identity
values, and keep only the finished `.vsix`.**

If you are an agent working in a downstream product repo: **read this file every
time you cut a release**, then follow "How to consume" below. This file is the
contract; the changelog at the bottom is the signal that the launcher moved.

---

## Canonical launcher version

| field            | value                                   |
|------------------|-----------------------------------------|
| `wrapper`        | **1.2.13**                              |
| `repo`           | `Lunarwerx/claude-code-orbit` @ `main`  |
| channel system   | tag lives in the patcher (`ORBIT_CHANNEL` → `ccPatchChannel`), mirrored to the manifest; wrapper reads it. Experimental (red) / Stable (green). No compatibility/preview cruft. |

## The source files to pull (raw, always `main` = newest)

```
extension.js   https://raw.githubusercontent.com/Lunarwerx/claude-code-orbit/main/Claude%20Code%20Orbit/extension.js
package.json   https://raw.githubusercontent.com/Lunarwerx/claude-code-orbit/main/Claude%20Code%20Orbit/package.json
stamp engine   https://raw.githubusercontent.com/Lunarwerx/claude-code-orbit/main/tools/orbit_config.py
build.py       https://raw.githubusercontent.com/Lunarwerx/claude-code-orbit/main/build.py
```

## How to consume (pull → rebrand → build → keep only the VSIX)

The launcher is product-agnostic. The ONLY thing that makes it "yours" is your
own `orbit.config.json` (target extension id, repo, slug, namespace, displayName,
logo, patcher path). The stamp engine rebrands the pulled launcher into your
identity — **you never hand-edit `extension.js`.**

```
1. Pull the four files above into a TEMP dir (NOT committed to your repo).
2. Rebrand into your identity (run from the pulled checkout):
     python tools/orbit_config.py stamp --to <YOUR_REPO>/orbit.config.json \
            --out "<YOUR_REPO>/<your wrapperDir>"
   This swaps target → your `target`, repo → your `repo`, namespace → your `ns`,
   plus name/logo/displayName — verified byte-identical when src == dst, so it
   only ever changes identity, never behavior.
3. python build.py   →  builds latest/<your-slug>.vsix using YOUR media + patcher.
4. Discard the pulled launcher source. Keep ONLY the built .vsix.
```

You bring (and keep) ONLY these product-specific files: `orbit.config.json`, your
patcher (`{patcher.dir}/{patcher.entry}`), `patch_modules/catalog.json`, and your
`media/*.png` logos. Everything else is pulled.

**Never hand-edit the launcher.** If you need a launcher change, request it
upstream in `claude-code-orbit`; it lands here, this changelog updates, and your
next build picks it up automatically.

---

## Launcher changelog (newest first — this is the "it moved" signal)

- **wrapper 1.2.13** — update detection surfaces **automatically** again. The
  panel now runs a fresh background check on open/refocus (`provider.autoCheck()`,
  throttled to once a minute) instead of only on the 4-hour poll or a manual
  "Check for updates" — so a newly-published applicable update shows its "Install
  newest" button without any click. When **Hide untested updates** is holding
  releases back, the count is now visible: the manual-check result reads "No stable
  updates — N untested update(s) hidden" (was a generic "untested updates hidden"),
  and the patched hero shows a quiet "N untested update(s) hidden · turn off Hide
  untested updates to install" line (new `hiddenUntestedCount` from `detectState`),
  so you always know something's waiting.
- **wrapper 1.2.12** — **Remove Orbit** now opens a real **centered confirmation
  modal** (dimmed backdrop, pop-in, Cancel / Yes-remove) instead of the inline
  slide-down inside the gear menu; it dismisses on Cancel, a backdrop click, or
  Escape, and any state change closes it. **Card-overlap fix:** the status/hero
  card was `flex:1` (shrinkable), so dragging the panel short shrank it below its
  content and the hero spilled down onto the recommendation cards (they visibly
  stacked). It's now `flex:1 0 auto` — it still grows to fill a tall panel but
  never shrinks past its content, so `.recommended` yields and scrolls instead.
- **wrapper 1.2.11** — gear-menu polish pass. The settings button is now a real
  **cog** icon (was a sun). All four menu items render as consistent, left-aligned
  buttons (Check for updates · Previous versions · Patches · Remove Orbit). **Remove
  Orbit** now asks for inline confirmation ("…restore the original Claude Code?
  Cancel / Yes, remove") instead of uninstalling on the first click. New **"Hide
  untested updates"** toggle: when on, the red update banner, the status-bar badge,
  and "Check for updates" only surface **stable** releases (persisted host-side via
  `claudeCodeOrbit.hideUntested`; gated in `detectState`, the manual check, and the
  background poller). The footer **"Show details"** toggle was removed (logging
  still runs, just not surfaced). The recommendations list is now the sole scroll
  region (`.wrap` is `overflow:hidden`), so the hero/footer stay put and only the
  cards scroll.
- **wrapper 1.2.10** — secondary actions (Check for updates, Previous versions,
  Patches, Remove Orbit) moved into an always-on top-right **⚙ gear dropdown**, so
  the main view stays hero + Install-newest + recommendations at any panel size.
  The recommendations list now scrolls when the panel is short. The gear shows
  only on the idle screen.
- **wrapper 1.2.9** — channel tag relabeled **EXPERIMENTAL → UNTESTED** (it means
  "new or not-yet-tested against the newest target," not broken), with a hover
  tooltip explaining it; the red styling and the internal `experimental` channel
  id are unchanged. The "Patches" picker moved up directly under "Previous
  versions" (it had been drifting down beside the recommended-extensions block).
- **wrapper 1.2.8** — the auto "update available" toast now fires ONLY for
  **stable** releases. Experimental pushes still flip the status-bar badge and
  the sidebar hero (red tag) so the bleeding edge stays one click away, but they
  no longer interrupt with a notification — so a maintainer can ship experimental
  freely without nagging everyone. The available update's channel is resolved
  from the manifest's per-version entry (falling back to `release_channel.txt`).
- **wrapper 1.2.7** — stable/experimental channel tags now read from the patcher
  itself (`ccPatchChannel`), list/banner from the manifest; dropped the BETA
  default; Back button glyph removed. (patcher-side: `ORBIT_CHANNEL` embed,
  `ship.py --stable`, sticky YOLO — Claude-specific.)
- **wrapper 1.2.6** — channel tags introduced; faster check-for-updates.
