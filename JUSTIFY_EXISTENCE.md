# Justify Existence Ledger

Living record of every feature/module/primitive added or materially changed.
Each entry: the job it does, why it's built THIS way vs the rejected
alternative, whether it reuses a unified primitive, and a prune verdict
(KEEP / MERGE / REFACTOR / REMOVE / WATCH). Incomplete by definition — add an
entry whenever you touch a surface that lacks one.

---

## Claude Code version-update detection (Orbit wrapper)

**Files:** `Claude Code Orbit/extension.js` — `fetchLatestClaudeVersion()`,
`readInstalledClaudeVersion()`, `GS_LATEST_CLAUDE_VERSION` /
`GS_LAST_NOTIFIED_CLAUDE`, plus consumers in `checkForPatcherUpdate` (poller),
the `checkUpdates` message handler, and `detectState` → `applyIdleState`.

**Job:** Experimental patches snapshot whatever Claude Code is newest *at patch
time*; they don't auto-follow new releases. Nothing in Orbit noticed when
Anthropic shipped a newer Claude Code, so users got silently stranded on an old
snapshot (e.g. patched on 2.1.157 while 2.1.158 shipped). This detects "a newer
Claude Code exists" and prompts a re-patch (the patcher is version-agnostic and
re-applies cleanly to the new version).

**Why THIS way (Marketplace query) vs rejected alternative (VS Code's own
"update available" signal):** the Marketplace gallery is the authoritative
source — it's the *same* endpoint the patcher already uses to find/download
Claude Code (`extensionquery`, filterType 7, flags 0x1). It's always accurate to
the real newest release. VS Code's built-in signal only reflects what its own
lazy refresh cycle has discovered and can lag a fresh release — unacceptable for
a tool whose whole point is tracking the newest version.

**Unified primitive / reuse:** reuses `cmpVer`, `readBundledStableVersion`, the
existing `globalState` cache pattern (mirrors `GS_REMOTE_PATCHER_VERSION`), and
the existing poller/`detectState`/`applyIdleState` flow. One fetch → one cache →
three consumers (poller, button, hero). No parallel mechanism introduced.

**Two-tier wording (verified vs unverified):** when a newer Claude exists, the
wording forks on `latestClaudeVerified = cmpVer(latest, stablePin) <= 0`:
- `<= stable pin` → "Update to vX" (we test-ran the patcher against that exact
  version at promotion time, so it's verified/safe).
- `> stable pin` → "Try experimental vX (unverified, may break)" + the safe
  "Use verified build vPin" option alongside.
Crucially this REUSES the stable pin as the "verified up to" line rather than
adding a third version number to keep in sync — a new number would be debt.
`stablePin` is the single source of truth for "newest Claude we've verified."

**Gating:** suppressed when `onStable` — stable is a deliberate frozen pin, not a
stale snapshot, so stable users are never nagged to move off it. Returns `null`
on any network/parse failure so a flaky connection never produces a false alarm.

**Known limitation (WATCH):** `onStable` is version-equality only
(`claudeCodeVersion === stablePin`), so an experimental user who happens to be on
the stable-pinned version is treated as a stable user and not nagged about a
newer-than-pin release until stable is promoted. Acceptable: it matches the
existing "(Stable)" heuristic and avoids adding channel-tracking state.

**Prune verdict:** KEEP. Closes a real UX hole (the "Check for updates" button
checked only Orbit's own patcher version, never Claude Code's). WATCH the
Marketplace `flags`/`api-version` contract — if the gallery response shape
changes, `fetchLatestClaudeVersion` must fail soft (it already returns `null`).

---

## "Tool & target" up-to-date panel + plain-outcome button labels (Orbit wrapper)

**Files:** `Claude Code Orbit/extension.js` — the `checkUpdates` "up to date"
branch (`resultSubHtml` builder), the `.verTable`/`.verNote` styles, the idle
pane buttons (`disableBtn`, `checkUpdatesBtn`, `stableBtn`) + their hints, the
error-pane recovery button, and the `enableStable`/error confirmation copy.

**Job:** The up-to-date screen listed three coequal version lines (Claude Code
`v2.1.158`, Orbit patcher `v1.2.57`, Orbit app `v1.1.7`) then appended an
italic footnote apologizing that two of them are unrelated. Users (incl. the
owner) read `v1.2.57` as a version that *should* line up with `v2.1.158` and
couldn't. The button labels "Restore original" (restore *what*?) and "Use
stable" (vs the jargon "experimental") compounded the confusion.

**Why THIS way (subtraction-first reframe) vs rejected alternative (keep the
three-line list, just reword the footnote):** the footnote was a band-aid on a
layout that *invites* the bad comparison. Rewording it can't beat not making the
comparison in the first place. So the patch tool is now rendered as a build
NUMBER (`#57`, the trailing semver segment) aimed at the Claude version it was
built for — `Patch tool #57 · ✓ built for v2.1.158` — never as a peer "version".
A build number with a target reads as a tool fact, not a version race. Buttons
were relabeled to their OUTCOME: "Restore original" → "Remove Orbit" (+ a
persistent "restore the original, unpatched Claude Code" tooltip, replacing the
three sites that blanked `disableBtn.title`); "Use stable vX" → "Use verified
build vX"; "Check experimental updates" → "Check for updates".

**Dynamic match indicator (the requested feature):** the per-line note is
computed live from `cmpVer(claudeCodeVersion, stablePin)` — `<=` pin → green
`✓ built for vPin` (+ "exactly the version you're running" when equal); `>` pin
→ amber `△ built for vPin` + a footnote stating it patched cleanly and works but
this exact Claude build isn't verified yet. This surfaces the real
"patcher-works-but-wasn't-made-for-this-Claude" state that can occur when a new
Claude ships before Orbit promotes a new stable pin.

**Unified primitive / reuse:** REUSES `stablePin` (`readBundledStableVersion`)
as the single "verified up to" source — same line the two-tier update wording
already keys on — so NO new version number or channel state was introduced. The
"verified build" rename is applied consistently to every user-facing "stable"
string (buttons, hints, hero mismatch copy, post-action + error confirmations);
internal action names (`enableStable`) and the `stable/` folder are unchanged.
`patchNum` falls back to the whole string for non-dotted values (e.g. "dev").

**Prune verdict:** KEEP. Removes the apologetic footnote anti-pattern and the
ambiguous labels. WATCH the `#NN` build-number display: it's the trailing semver
segment, so it is only monotonic *within* the `1.2.x` patcher channel — a future
`1.3.0` bump would reset it to `#0`. Acceptable while the patcher stays on
`1.2.x`; revisit the display (e.g. show `#1.3.0`) if the minor ever bumps.

---

## Patcher rollback registry — "Use previous version" (Orbit wrapper)

**Files:** `patchers/manifest.json` + `patchers/patch_claude-<ver>.py` (the
registry, pushed to `orbit`), `tools/archive_patcher.py` (the writer), and in
`Claude Code Orbit/extension.js`: `OTA_PATCHERS_MANIFEST_URL`/`OTA_PATCHERS_BASE`,
`GS_PATCHER_MANIFEST`, `fetchPatcherManifest()`, `pickPreviousPatcher()`, the
poller cache, `detectState`→`previousPatcher`, `enablePrevious()`, the
`enablePrevious` dispatch/done-copy, and the `#previousBtn` button + wiring.

**Job:** The experimental channel pushes freely and "Check for updates" always
takes the newest. Until now the only fallback when a new patcher misbehaved was
"Use verified build" — a big jump down to the bundled stable pin. This adds a
granular one-step-back: a registry of every released patcher + the Claude Code
version each was verified against, and a button that re-installs the previous
one. This is the rollback half of "push as much as we want, let users go back."

**Why THIS way vs the rejected alternative (the user's first instinct — delete
the bundled stable and let users pull ANY historical build from a folder):**
two reasons that design fails. (1) **Compatibility:** the patcher is NOT
version-independent — `patch_claude.py` anchors only match a window of Claude
Code releases, so an old patcher fired blind at today's Claude would just
`RuntimeError`. So each registry entry records a `claude` pin and rollback
re-installs the patcher *pinned to that Claude* (reusing the exact `--version`
mechanism "Use verified build" already uses) — a known-good (patcher, Claude)
pair by construction, never a blind downgrade. (2) **Floor:** bundled stable is
the only offline, always-present, guaranteed pair — the floor you fall to when
the network's down or every experimental broke. Rollback is online (it fetches
the archived patcher). So stable STAYS as the floor; the registry is the ladder
above it. Deleting stable would remove the very safety the feature provides.

**Unified primitive / reuse:** no new version counter — reuses the monotonic
build number `build.py` already stamps and the patcher semver. Reuses `cmpVer`,
`httpsGet`/`httpsDownload`, `runPython`/`findPython`, the `enable()` swap shape,
the `--version` Claude pin, the poller→globalState→`detectState` cache pattern
(mirrors `GS_LATEST_CLAUDE_VERSION`), and the existing action/cancel/commit flow.
`patchers/manifest.json` is the single source of truth for "what can I roll back
to"; `tools/archive_patcher.py` is the only writer (idempotent upsert).

**Not bundled, fetched OTA:** the registry lives only on `orbit`, never in the
VSIX — that's the point (push a new version, users get the rollback option with
no VSIX reinstall).

**Stable dissolved into the registry (`build.py`):** there is no longer a
hand-promoted stable patcher. `build.py` now (1) archives the current
experimental patcher into the registry FIRST, then (2) bundles the NEWEST
registry entry as the known-good, writing its version/claude into the same
`extension/stable/*` paths the wrapper already reads. So the ~101 `stable`
references in `extension.js` funnel through one chokepoint
(`readBundledStableVersion` ← the bundled `stable_version.txt`) and inherit the
new meaning ("newest archived version") with ZERO extension.js edits — verified
byte-identical (md5 `ad4224af…`, Claude 2.1.158, patcher 1.2.57) so the cut was a
behavioral no-op. The ONE human-set survivor is the Claude target
(`stable/stable_version.txt` = "the Claude version we've verified against") — it
can't be auto-derived (it's a human judgement: did I test this Claude?), and the
archiver stamps each snapshot with it. The legacy `stable/patch_claude.py` /
`patcher_version.txt` remain only as a registry-empty fallback.

**UI streamlined — "stable"/"experimental" removed from view (extension.js):** the
buttons are now "Install newest" (was "Use experimental"), "Remove Orbit", "Check
for updates", and "Previous versions" — a scrollable picker pane (`data-pane=
"versions"`, `renderVersions()`) listing every archived version newest-first, each
row showing **version · Claude target · build #** with an Install action (the
installed one badged "Installed", its button disabled). Picking a row calls the
existing `enablePrevious` flow. The "Use verified build" button + "(Stable)" badge
+ two-tier verified/unverified wording are GONE; the error-pane recovery button is
now "Install newest". Driven by a new `patcherHistory` field on `detectState`
(flattened from the cached manifest via `patcherHistoryFromManifest`). Verified:
syntax, build #94, VSIX bundles the picker, history sorts newest-first. NOT yet
live-tested (the DOM wiring — row rendering, pane nav, button visibility — needs a
real webview eyeball). Dead server code left in place (harmless, unreachable):
the `enableStable` dispatch + its done-message branches — candidate to prune.

**Reachability fix — registry bundled in the VSIX + history backfilled:** the
first cut made the picker read ONLY the OTA-cached manifest, which is empty until
a push — so on a local install the button hid (no data) and there was only one
version anyway. Fixed two ways: (1) `build.py` now bundles `patchers/`
(manifest + every `patch_claude-*.py`) into `extension/patchers/`;
`getEffectiveManifest()` uses the OTA cache when non-empty else the bundled copy,
and `enablePrevious()` installs straight from the bundled `.py` when present
(offline, instant) — so the picker works the moment Orbit is installed, no push.
(2) Backfilled real prior versions 1.2.52–1.2.56 from git history (extracted each
commit's `Claude Code/patch_claude_vsix_v147.py`), pinned to their committed
Claude 2.1.154, so the picker has genuine rollback targets. Verified: build #95
bundles manifest + 6 patchers (~+300 KB compressed); picker lists all six
newest-first, installed badged. WATCH: bundling every patcher will bloat the VSIX
as versions accumulate — cap to the last N (OTA for older) once it grows. WATCH:
historical entries pin Claude 2.1.154 — installing one fails if the Marketplace
has dropped that build (edge case).

**Picker polish + ad real estate (extension.js):** (1) "Previous versions" is now
a floating text link (`altAction`), the low-opacity patched/outdated hint is
removed, and the recommended block grows (`flex:1 1 auto`, centered) with larger
cards to fill the otherwise-dead patched screen. (2) Recs split into two labelled
sections — "Recommended Extensions" (the 3 ext) vs "Recommended Companies"
(Connexions) — via `recExtHtml`/`recCompanyHtml` (dropped the per-item eyebrow).
(3) Picker Install now routes through a `confirm` pane ("Install patcher vX?")
before the working/cancel flow, so a deliberate downgrade never fires on a stray
click. (4) BACK-BUTTON FIX: the grown ad block was competing with `.card{flex:1}`
for height and overlapping the versions-pane Back button (only a sliver
hoverable). Fixed by `setPane()` hiding `.recommended` on every non-idle pane
(ads belong on idle) AND giving the versions pane `flex:1` so its list+Back sit
inside the card box. Verified build #97 (syntax, sections, confirm pane, ad-hide,
versions flex). Live DOM (overlap gone, confirm flow) still needs an eyeball.

**Prune verdict:** KEEP (infrastructure). WATCH: (1) the registry is **inert
until pushed to `orbit` AND a version newer than the seed ships** — with only
1.2.57 archived there is nothing older to roll back to yet. (2) `claude` pin is
recorded from `stable/stable_version.txt` at archive time — if an experimental
patcher is cut against a Claude *newer* than the current stable pin, the recorded
pin is conservative (rollback downloads that older, verified Claude); fine, but
revisit if experimental routinely runs ahead of stable. (3) The manual release
step (`tools/archive_patcher.py` before each push) is easy to forget — if skipped,
that version simply won't be a rollback target. Candidate to fold into the
release flow / Dark-Knight check later.
