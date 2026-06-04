# Justify Existence Ledger

Living record of every feature/module/primitive added or materially changed.
Each entry: the job it does, why it's built THIS way vs the rejected
alternative, whether it reuses a unified primitive, and a prune verdict
(KEEP / MERGE / REFACTOR / REMOVE / WATCH). Incomplete by definition — add an
entry whenever you touch a surface that lacks one.

---

## Queue flush rebuilt per-session (1.2.69) — kills "queued forever" stranding

**Files:** `Claude Code/patch_claude_vsix_v147.py` — rewrote `ccPatchQueueTick`,
replaced `ccPatchSendText(t,files,sel)` with `ccPatchSendQueued(it)`, fixed
`ccPatchQueueSendNow`. Removed globals `ccPatchQueueWasBusy` and
`ccPatchQueueSessRef`; added `ccPatchQueueBusyMap` (`Map<session,wasBusy>`).

**Job:** flush each queued message the instant *its own* chat goes idle —
regardless of which chat is on screen or how many chats are streaming.

**Why THIS way / what was broken:** the old flush was a SINGLE global busy→idle
edge detector keyed to `ccPatchActiveSession()` (one `wasBusy` flag, one
`sessRef`). `ccPatchActiveSession()` is just "the last session that rendered" —
any background chat that re-renders while Thinking hijacks it (the same unreliable
global flagged in the queue-mechanism memory #6, which fixed the *decision* path
but left the *flush* path on it). Consequence: queue into chat A, then switch to /
stream chat B → A's idle edge is observed against B, never A; and since an idle
chat has no future edge, A's item sits in QUEUED forever. Second latent bug: even
when caught, `ccPatchSendText` sent to the *active* session, so it could fire into
the wrong chat. Fix is a SUBTRACTION of the fragile coupling: each item already
carries a live ref to its own session (`it.sess`), so we watch `it.sess.busy.value`
directly per session and `it.sess.send(...)` to its own session. Rejected
alternative — iterate the global sessions list (`$.sessions.value`): the tick is a
standalone `setInterval` with no component scope, and the items already pin exactly
the sessions that matter, so the list is redundant.

**Verified:** `node --check` on the three functions; `py_compile` on the patcher;
a 6-scenario `node` simulation (single-chat finish; **queue-A-then-switch-to-B
background finish — the reported bug**; two concurrent busy chats; one-message-
per-turn sequencing with no double-send) — all pass. Built #115, archived as
`patchers/patch_claude-1.2.69.py`. NOT yet pushed OTA.

**Verdict:** KEEP. Watch: items reloaded from localStorage (text-only; no `sess`
object until their chat next renders and `ccPatchQueueOwns` rebinds) are tracked
only after they've been viewed once — same as before, acceptable.

---

## Removed the visible Orbit toggle; gated fork-row (3rd patch)

**Files:** `Claude Code Orbit/extension.js` — deleted the on-screen `orbitToggle`
pill + its CSS/JS + the `data-orbit` machinery + `orbit:true` flags; the self-hide
is now PURELY backend (dynamic `context.extension.id` filter). Added `fork-row` to
`TOGGLEABLE_PATCHES`. `Claude Code/patch_claude_vsix_v147.py` — gated the fork
action-row block (`feature_on("fork-row")`, native dropdown stays when off),
added `fork-row` to `GATEABLE_FEATURES` and to the conditional-verify map.

**Job:** Jacob: "when I said changeable I meant the BACK END so no one sees it" —
the self-hide should be automatic from the extension's name, not a user-facing
button. So the pill is gone; whichever product is running hides its own rec entry
and shows the sibling, invisibly. Also grew the real patch toggles 2 → 3.

**Why THIS way / honest finding:** the visible toggle was a misread of "exposable"
— removed wholesale (justify-or-die: it had no reason to exist once the behavior is
automatic). Gating fork-row used the early-guard pattern (`m_fork = ... if
feature_on else None`) so the big replace block keeps its existing `else:` body —
minimal blast radius, no re-indent. KEY architectural finding for scope: the
patcher's ~28 patches are NOT all individually separable. Three classes —
(a) distinct-injection (usage-meter, yolo-mode, fork-row, the settings button,
switch-model) → individually gateable; (b) helper-woven (image preview, sticky
preview, color themes live in the always-injected CLAUDE_HELPER_JS + global
listeners) → only separable by refactoring the helper; (c) grouped (the settings
menu + its account/instructions items; the 12-piece welded sidebar) → gateable
only as one unit. So the realistic toggle set = a few individual + a couple of
GROUP toggles ("Settings menu", "Right sidebar"), not 28 independent switches.

**Verified:** patched real stock 2.1.159 in 3 configs (none / fork-row off /
all-three off) — each patched, verified, and contained exactly the right patches;
node+py_compile clean; build-113 inspected.

**Verdict:** KEEP. Next: the two high-value GROUP toggles Jacob named (sidebar,
settings menu) as single on/off switches.

---

## Patch picker UI + dynamic self-hide (the pick-and-choose, made visible)

**Files:** `Claude Code Orbit/extension.js` — `TOGGLEABLE_PATCHES` list +
`GS_DISABLED_PATCHES` key; a collapsible **Patches** checkbox panel in the idle
pane (+ CSS); webview JS that persists the disabled set via a `setDisabledPatches`
message; the `onMessage` handler that saves it; `--disable <ids>` appended to the
patcher args in BOTH `enable()` and `enablePrevious()`; and dynamic self-hide
(recs filter on the live `context.extension.id`, not the hardcoded `SELF_EXT_ID`).
`Claude Code/patch_claude_vsix_v147.py` — `--disable` now IGNORES unknown ids
(warn, not fail) so wrapper/patcher version skew can't break a patch.

**Job:** Make the pick-and-choose ACTUALLY VISIBLE and usable. The engine existed
([[modular-patch-registry]], the gating entry below) but had no UI, so to Jacob it
looked uncoded. Now: open Patches → uncheck a patch → Install/Update re-patches
without it. Selection persists in globalState. Self-hide is dynamic per Jacob's
"if the extension's name is X, hide X" — zero per-product edits for the kit.

**Why THIS way vs rejected:** The wrapper holds its own small `TOGGLEABLE_PATCHES`
mirror (not reading the bundled catalog.json) because only the patcher's
`GATEABLE_FEATURES` can truly be honored — the UI list must match what the patcher
can gate, so it's the contract, kept deliberately tiny and in lockstep. Patcher
leniency (ignore unknown --disable) over strict-error: the OTA patcher and the
installed wrapper drift in version, and a stale id must NOT fail the whole
re-patch. Dynamic self-id over hardcode: the kit copies with no edit.

**Verified (not asserted):** patched real stock 2.1.159 with
`--disable usage-meter,bogus-id` → bogus-id ignored, usage button absent, YOLO
still applied, verification passed (80 checks); `node --check` + `py_compile`
clean; built build-112 and inspected the VSIX — patch picker, checkboxes,
TOGGLEABLE list, setDisabledPatches, `--disable` wiring, dynamic self-hide, and
the globalState key all present.

**Wiring status / debt:** `TOGGLEABLE_PATCHES` = the 2 proven-safe gateable
features (usage-meter, yolo-mode). It grows as more independent blocks are gated
(each: wrap the block + make its verify conditional + add to both lists). The
welded sidebar cluster is NOT yet a toggle — splitting/grouping it safely is the
next milestone. The UI list and the patcher's GATEABLE set are hand-kept in sync;
a future check could assert they match.

**Verdict:** KEEP. WATCH (the two-list sync; the gateable set is small until more
blocks are untangled).

---

## Orbit cross-promo + show/hide toggle + logo refresh (recs)

**Files:** `Claude Code Orbit/extension.js` — added a Claude Code Orbit self-entry
beside Codex Orbit in `recs[]` (both flagged `orbit:true`), a `data-orbit` attr in
`renderRec`, a `.recHeaderRow` + `#orbitToggle` control, its CSS, and the webview
JS that shows/hides `[data-orbit]` items persisted in `localStorage`.
`Claude Code Orbit/media/claude-code-orbit.png` ← new logo from Downloads;
`Claude Code Orbit/media/rec-codex-orbit.png` ← new Codex reference logo from
Downloads. `build.py` — added `media/rec-codex-orbit.png` to `INCLUDED_PATHS`.

**Job:** Jacob wanted both LunarWerx Orbit tools surfaced in the recommendations
(Codex Orbit + Claude Code Orbit) with a "super simple on/off" to hide/reveal them
— so on Claude Code you can collapse the Codex promo and vice-versa — plus the two
refreshed logos. See [[modular-patch-registry]] for the broader cross-link intent.

**Why THIS way (one client-side toggle over [data-orbit]) vs rejected:** the toggle
hides via a `data-orbit` flag + `localStorage`, no server round-trip or re-render —
the recs are static HTML so a CSS/JS hide is the lightest mechanism. One toggle for
the whole Orbit family (not per-entry) matches "super simple on off"; flipping it
off hides the sibling product, which is the stated use case. Rejected: a buried VS
Code setting (not "exposable"); a postMessage→globalState round-trip (overkill for a
view-pref). Self-entry is auto-hidden via `SELF_EXT_ID` (Jacob's follow-up: don't advertise
our own add-on to itself — looks like list-padding and annoys users). Both entries
stay in `recs[]` for kit symmetry, but each product filters out its OWN id, so
Claude Code Orbit shows only Codex Orbit and vice-versa. The new Codex reference
logo was also corrected to `codex orbit.png` (white-knot transparent mark, 85%
alpha — shows on the dark cards); the black-tile version I first grabbed was the
file Jacob deleted.

**Caught during build:** `INCLUDED_PATHS` never listed `rec-codex-orbit.png`, so the
Codex icon would have shipped as a broken image. Added it; verified the built VSIX
bundles all six media files.

**Verified (not asserted):** `node --check` on extension.js passed; `python build.py`
produced build-110 (pkg 1.1.10 UNCHANGED); inspected the VSIX — new logos + Codex
icon bundled, and the codex-orbit / claude-code-orbit / orbitToggle / data-orbit /
Connections tokens all present.

**Wiring status / debt:** `SELF_EXT_ID` is hardcoded to this product's id; once
extension.js reads [[modular-patch-registry]]'s orbit.config.json it derives from
`lunarwerx.<slug>`. The new Codex logo updated only OUR reference copy
(rec-codex-orbit.png); the Codex Orbit sibling project's own media is untouched.

**Verdict:** KEEP.

---

## Patcher feature gating — `--disable`/`--enable` (the actual pick-and-choose)

**Files:** `Claude Code/patch_claude_vsix_v147.py` — added `ENABLED_FEATURES`
global + `GATEABLE_FEATURES` set + `feature_on()`; `--disable`/`--enable` CLI args
in `main()`; guards around block 16 (YOLO) and block 19c (usage-meter); conditional
verification in `verify_extension_dir()` (pop a disabled feature's checks).

**Job:** Turn the catalog/registry from a planning artifact into a working
mechanism: the patcher can now SKIP individual feature modules. First real,
user-driveable slice of the pick-and-choose feature Jacob asked to actually SEE.
See [[modular-patch-registry]].

**Why THIS way (module-global gate + per-block guard) vs rejected alternatives:**
Reused the existing global-set-in-main pattern (`PATCHER_VERSION`) instead of
threading an `enabled` param through the 1000-line `patch_webview_js` — minimal
blast radius, no signature churn. Only purely-additive/replace-to-native blocks
are gateable (the shared helpers/CSS stay present-but-unused when a feature is
off — harmless, nothing renders them). The welded sidebar cluster and core
scaffold are deliberately NOT gateable yet (their blocks share captures + apply
order; splitting them safely is a later pass). Rejected: stripping the unused
helper/CSS too — cosmetic, higher risk, no user-visible benefit for v1.

**Verified (not asserted):** patched the real stock `anthropic.claude-code-2.1.159`
VSIX twice — control (all on) → usage button + YOLO present and verification
passed; `--disable usage-meter,yolo-mode` → both absent and verification STILL
passed (conditional checks worked). Default (no flag) = `ENABLED_FEATURES=None` =
byte-identical to before, so existing users are unaffected.

**Wiring status / debt:** the patcher SUPPORTS gating; nothing drives it yet from
the UI. Next: (1) a Patches checkbox panel in the Orbit sidebar (extension.js)
persisted to globalState; (2) extension.js passes `--disable <ids>` to the patcher
on Enable; (3) build a numbered wrapper VSIX so Jacob can install + click. Then
expand `GATEABLE_FEATURES` to the rest of the independent set (fork-row,
color-theme, per-chat-color, sticky-preview, show-more, image-preview,
composer-send/queue) — each gated + verified the same way.

**Verdict:** KEEP. WATCH (GATEABLE is 2 features as a proven beachhead; expand
with the UI so the catalog's userFacing set and the patcher's GATEABLE set stay
in lockstep — drift between them is the thing to guard).

---

## Unified wrapper kit — `orbit.config.json` + `tools/orbit_config.py` + `ORBIT_KIT.md`

**Files:** `orbit.config.json` (new — the ~12 canonical product-identity fields),
`tools/orbit_config.py` (new — single derivation function + `show`/`check` drift
detector), `ORBIT_KIT.md` (new — the fork playbook + the full field→location
mapping table). Also: deleted junk (root + `Claude Code/` logs, `__pycache__`,
`Claude Code/_t.vsix` — all gitignored).

**Job:** Make the Orbit *wrapper* product-agnostic so Codex Orbit (and any future
"patch a VS Code extension" product) is a copy-the-folder, edit-one-config fork
instead of a hand-maintained divergent twin. Jacob's pain: every Claude Code Orbit
wrapper change had to be re-applied to Codex Orbit by hand. The swap surface —
proven to live in exactly 3 files (extension.js, package.json, build.py) — now
derives from one config. See [[modular-patch-registry]].

**Why THIS way (one config + derivation fn) vs rejected alternatives:** One source
of truth for identity (CLAUDE.md prime directive) — the dozen scattered literals
(`STOCK_ID`, `OTA_*`, the `{ns}` namespace across views/commands/globalState,
`EXT_NAME`, the patcher glob) all reduce to `{target, repo, slug, ns, ...}`.
Rejected: (a) templating the whole wrapper with a generator now — bigger blast
radius on release-critical files before the model is proven; (b) a docs-only
mapping with no machine check — `orbit_config.py check` makes the config
*falsifiable* against the live code (18 grounded assertions), which is what lets
the next step (routing build.py/extension.js to READ the config) be verified as
byte-identical output.

**Self-caught:** the drift detector first reported 3 false drifts — OTA urls and
the icon path are built by concatenation/`joinPath`, not literal strings. Sharpened
the assertions to match how the code expresses them (suffix/part match) rather than
weakening them. Blade taught, not blunted.

**Cleanup discipline (Dark Knight, three-axis guilt):** deleted ONLY items proven
guilty on all axes (hold no needed data / no code uses / nothing references):
logs, pycache, temp vsix. Spared the rollback registry (`patchers/` — sole
recovery), `latest/`, the version pins, and `Potentials/`. FLAGGED but did not
delete: 3 stale stock test VSIXes (`anthropic.claude-code-2.1.154/158/159`, 225 MB,
none matches certified 2.1.161) — large + network-to-restore, so they get a nod
before removal.

**Wiring status / debt:** the wrapper code does NOT yet read `orbit.config.json` —
v1 is the config + the verified mapping. Next: a `build.py` stamping step that
writes package.json identity + injects the config into extension.js from
`orbit.config.json`, byte-parity verified, plus a true dry-run so it's testable
without release side effects (build.py currently always writes `latest/` + archives
the patcher). Physical "single kit folder" consolidation follows the wiring (the
wrapper folder name is itself `wrapperDir`, config-driven).

**Verdict:** KEEP (config + tool + playbook). WATCH (until build.py/extension.js
read the config, identity is single-sourced in intent but still dual-maintained in
fact; `orbit_config.py check` is the guard that keeps them honest meanwhile).

---

## Patch-module registry — `patch_modules/catalog.json` + `tools/patches.py`

**Files:** `patch_modules/catalog.json` (new — single source of truth enumerating
all 31 patch modules: tier, required/userFacing, dependsOn, owned verification
tokens), `tools/patches.py` (new — registry CLI: `list` / `resolve` / `check`).

**Job:** Make the patcher pick-&-choose per Jacob's directive ([[modular-patch-registry]]):
a user/contributor enables or disables individual patches; the resolver
force-includes the core scaffold, expands `dependsOn`, and cascade-drops anything
that depended on a disabled module. Step 1 of turning the 2957-line monolith
(`Claude Code/patch_claude_vsix_v147.py`) into the beloved Vencord/Tampermonkey
modular model, with Claude as sole maintainer of community patch PRs.

**Why THIS way (declarative catalog as data) vs rejected alternatives:** The
honest reality of the patcher is a *capture-based* transform — it parses Claude
Code's minified webview once to grab obfuscated identifiers, then ~30 blocks
share those captures and a fixed apply order. So "every patch is an isolated
anchor+replacement JSON" would be a LIE: many patches genuinely depend on the
core scaffold and on the sidebar cluster root. The catalog tells that truth —
3 always-on core modules, a 12-module sidebar cluster with an internal root, and
~16 independent feature toggles — and the resolver works *around* the couplings
(declared `dependsOn`) instead of pretending they don't exist. Data, not code,
is also the safety gate: a community patch PR against the catalog + a declared
verify-token set is reviewable line-by-line and CI-checkable, which is what makes
Claude-as-maintainer safe. Rejected: (a) wrapping each block in `if enabled`
inside the monolith first — higher risk, mutates the live patcher before the
model is proven; (b) a markdown-only catalog — not machine-resolvable.

**Self-caught during build:** the validator rejected explicit `dependsOn` edges
between always-applied core modules (redundant — `dependsOn` must mean exactly
one thing, pull-in dependency for *optional* modules; apply ORDER is encoded by
catalog array order). Edges dropped; contract kept single-purpose.

**Wiring status / debt:** the registry is NOT yet consumed by the apply path —
this is the registry layer only. Next increment routes
`patch_webview_js`/`patch_webview_css` through it module-by-module, each step
verified to produce byte-identical output to today's monolith so live users see
no regression. `blocks[]` fields are the traceability map for that extraction.

**Verdict:** KEEP. WATCH (until the apply path is routed through it, the catalog
is truth-by-assertion — the byte-parity extraction is what makes it load-bearing).

---

## Codex Orbit cross-link + "Connexions" → "Connections" fix (recs list)

**Files:** `Claude Code Orbit/extension.js` — `renderHTML()` `recs[]`: inserted a
Codex Orbit entry after SayDeploy and corrected the company label from
"Connexions" to "Connections". `Claude Code Orbit/media/rec-codex-orbit.png` —
new asset (copied from Codex Orbit's published store icon
`codex-orbit-store.png`).

**Job:** (1) Reciprocate the cross-promotion Codex Orbit already ships — its
`recs[]` lists `lunarwerx.claude-code-orbit` as "The companion Orbit patcher for
Claude Code," so Claude Code Orbit now lists `lunarwerx.codex-orbit` as "The
companion Orbit patcher for OpenAI Codex." The two sibling patchers point at each
other. (2) Kill a typo: the company is **Connections** (`connections.icu`, folder
`Project/Connections`), never "Connexions" — the stray X was wrong everywhere it
appeared.

**Why THIS way vs rejected alternatives:** Reused the existing `recs[]` registry
and the `id`-based (vs `url`/`company`) entry shape — Codex Orbit is a real
Marketplace extension, so it opens in the Extensions view like the other three,
no new render path. Reused Codex Orbit's own *store* icon rather than its
white-on-transparent mark so it reads as a crisp 44×44 app-tile matching
SayDeploy's tile (the `.recIcon` `border-radius:9px` slot is built for tiles).
Rejected renaming `rec-connexions.png` → `rec-connections.png`: the filename is
internal, the same asset is referenced by Codex Orbit too, and renaming would
fork a shared asset name for zero user-visible gain — fixed only the visible
label.

**Known remaining debt (not in scope here):** the identical "Connexions" label
and the `recs[]` block are DUPLICATED in `Project/Codex Orbit/.../extension.js`
(same typo, same hardcoded array). One source of truth would be a shared recs
manifest both wrappers read; today each repo hand-maintains its own copy.

**Verdict:** KEEP (cross-link + asset). WATCH (the duplicated recs array across
the two Orbit repos — candidate for a shared manifest if a third surface appears).

---

## Account usage meter — 5hr/weekly rings in the composer footer

**Files:** `Claude Code/patch_claude_vsix_v147.py` — added the `ccPatchUsage*`
helper family inside `CLAUDE_HELPER_JS` (data accessor, ring SVG, popover,
button icon) + exported them on `globalThis`; added patch block **19c** (inject
the button after the "Show command menu (/)" button in `We1`); added the
`.ccPatchUsage*` CSS rules in `patch_webview_css`; added the `"usage meter"`
verification check.

**Job:** Let the user see how much of their 5-hour (session) and weekly limits
they've burned, at a glance, without leaving the composer or guessing — a
"battery for your account." A ghost button sits beside the slash-command button;
clicking it opens a popover with one ring per limit window (Session 5hr / Weekly
/ Weekly Sonnet when present), each showing % used, a green→amber→red arc, and a
"resets in Xh" countdown. The button icon itself is a live mini-ring.

**Why THIS way (read the native `utilization` signal) vs rejected alternatives:**
The connection store (`session.connection.value`) already carries a reactive
`utilization` signal the host pushes via `usage_update`, shaped exactly as
`{fiveHour,sevenDay,sevenDaySonnet}` with `{utilization:0-100, resetsAt}`. The
native Account & Usage panel renders the same signal as flat bars (`Mq1`/`_t1`).
So we invent NO new data path, no scraping, no separate fetch loop — we surface
the one source of truth that already exists, refreshing it on open with the
native `requestUsageUpdate()` RPC. Rejected: (a) a second polling/estimation
layer — would be a parallel truth that drifts from the host's; (b) reusing the
existing settings-dropdown "Account & usage" item only — it executes the native
`account-usage` command (full modal), not the at-a-glance footer affordance the
user asked for. The button icon reads `.value` during `We1`'s render, so it
re-renders live on `usage_update` with zero extra ticker — idiomatic for this
signals-based webview.

**Unified primitive / reuse:** `globalThis.ccPatchComposerSession` (already
stashed by block 19a for the send/queue controls) is the single handle to the
session; usage reads `session.connection.value` off it. Popover open/close
mirrors the existing menu pattern (`_ccPatchOutsideHandler` + Escape, like
`ccPatchShowFilterMenu`/`ccPatchShowSettingsMenu`). Anchored on the stable native
string `title:"Show command menu (/)"` (unique, count=1) so it survives minifier
churn; React alias captured, not hard-coded.

**Prune verdict:** KEEP. WATCH: (1) if Anthropic adds a `sevenDayOpus` window or
renames the keys, `ccPatchUsageWindows` silently shows fewer rings — the verify
check only guards the button/CSS/RPC presence, not the field names; teach the
Dark Knight to flag a webview release where `fiveHour`/`sevenDay` vanish from the
bundle. (2) Reading `utilization.value` in `We1`'s render subscribes the whole
footer to usage updates (rare, cheap) — revisit only if usage events ever become
chatty. (3) Untested in a live webview here (node syntax-checked + 81 verify
checks pass on stock 2.1.160); the rings/animation/countdown still need an
eyeball in the running app.

---

## "Stable" channel dissolved → current + Previous-versions rollback

**Files:** deleted `stable/` + root `stable_version.txt`; added
`certified_claude.txt`; changed `tools/archive_patcher.py`, `build.py`,
`Claude Code Orbit/extension.js` (enable/disable, detectState, read helpers, UI).

**Job:** Recovery if a release breaks. The old model advertised three things
(experimental / a hand-promoted "Stable" channel / the rollback registry), but
"Stable" had already dissolved into the registry — `build.py` bundles the newest
archived entry as the offline floor. The leftover `enableStable` action,
`onStable` two-tier "verified/unverified" wording, and `STABLE_*` constants were
UI for a channel wired to no button.

**Why THIS way (one registry, Codex-style) vs rejected alternative (keep a
promoted Stable pin):** two recovery mechanisms for one job is the "47
workarounds" the prime directive forbids. The registry already pins each patcher
to a certified Claude version, so "Previous versions" is a strictly more general
recovery than a single frozen Stable pin — and needs no hand-promotion step.
Subtract the channel; keep the one primitive that does the job.

**Unified primitive / reuse:** the rollback registry (`patchers/manifest.json`)
is the single source of truth. `newestBundledEntry()` derives the offline
fallback patcher, its version, and its certified-Claude tag from that one source
(replacing the separate `stable/` files + `readBundledStableVersion`). The
certified tag survives as each entry's `claude` field, sourced from
`certified_claude.txt`.

**Verdict:** KEEP (registry + Previous versions). REMOVED: stable/ channel,
enableStable, onStable, latestClaudeVerified, STABLE_* constants,
fetchOtaStableVersion.

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

**Unified primitive / reuse:** reuses `cmpVer`, `readBundledCertifiedClaude`, the
existing `globalState` cache pattern (mirrors `GS_REMOTE_PATCHER_VERSION`), and
the existing poller/`detectState`/`applyIdleState` flow. One fetch → one cache →
three consumers (poller, button, hero). No parallel mechanism introduced.

**Wording (single-tier, post-stable):** when a newer Claude exists, Orbit simply
prompts "Update to vX" — re-patching pulls the newest and re-applies the
version-agnostic patcher. The old two-tier "verified vs unverified / Use verified
build" fork was removed with the Stable channel (see the dissolution entry above);
the certified-Claude tag now only labels the patched panel ("built for vX").

**Gating:** `fetchLatestClaudeVersion` returns `null` on any network/parse
failure so a flaky connection never produces a false "newer Claude" alarm. (The
old `onStable` suppression was removed with the Stable channel — there's no
frozen pin to protect anymore.)

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
sections — "Recommended Extensions" (the ext) vs "Recommended Companies"
(Connections) — via `recExtHtml`/`recCompanyHtml` (dropped the per-item eyebrow).
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
