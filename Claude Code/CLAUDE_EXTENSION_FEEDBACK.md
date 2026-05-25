# Claude Code VS Code Extension Feedback

## Part 1: TLDR for the Anthropic Team

The Claude Code VS Code extension already has a useful session history view, but the current interaction model makes active work harder to manage when several conversations are running.

Requested improvements:

- Show the session list directly in the Claude Code sidebar instead of hiding it behind the small history popover.
- Use the available empty sidebar space more efficiently by splitting the main view into a chat pane and a session pane.
- Put the session pane on the right side of the sidebar.
- Add a draggable divider so users can resize the chat pane and session pane.
- Add `Pin` and `Star` actions to sessions.
- Keep the existing rename/edit and delete buttons working exactly as they do today.
- Show a running indicator on any session that is currently active.
- When a session finishes running, show a blue unread/done dot.
- Clear the blue dot when the user opens/clicks that session.
- Allow "Rewind code" to proceed when Claude can rewind the conversation context, even if the file diff is empty.
- Make modal dialogs/backdrops render above the resizable session pane so dialogs are not visually cut off or covered.
- Add a header toggle (next to the "Session history" clock icon) that hides/shows the right-side session pane and its resize handle, with the state persisted across reloads.
- Harden the split-pane flex layout so chat content with long messages or wide code blocks cannot push the divider, clip the session pane, or visually bleed into the other pane.

Why this matters:

Users often have multiple Claude Code sessions running or paused across different tasks. The sidebar has enough room to show session state without forcing users into a cramped popover. A persistent, resizable session pane makes it much easier to monitor running chats, return to completed work, and keep important sessions pinned.

The rough UI shape:

- Left/main pane: the existing Claude chat experience.
- Vertical drag handle.
- Right pane: session list with search, local/web tabs, rename/edit, delete, pin/star via context menu, running spinner, and blue completion dot.

One related bug:

- The "Rewind code" dialog currently disables the primary rewind action when no file changes are detected, even though the user may still want to rewind the conversation/context to that point.
- After adding a persistent right-side session pane, modal z-index/backdrop behavior needs to be hardened so dialogs appear above the pane and the resize divider.

Please implement this natively in the extension source rather than as a post-build VSIX patch.

## Part 2: Implementation Instructions for an AI Code Agent

### Goal

Implement session list ergonomics in the Claude Code VS Code extension:

1. Render the session list persistently in the main Claude sidebar layout.
2. Place it on the right side of the chat pane.
3. Add a draggable resize divider between chat and sessions.
4. Add session pinning and starring.
5. Add running and completed indicators.
6. Preserve the existing inline rename/edit and delete behavior.
7. Avoid relying on the existing popover as the primary session browser.
8. Allow context rewind even when the dry-run file diff is empty.
9. Ensure modal dialogs/backdrops layer above the split-pane session UI.

The implementation should be done in the extension source files before bundling. Do not edit the minified bundle by hand if the source is available.

### Relevant Existing Code Concepts

The bundled VSIX currently contains these conceptual pieces:

- `fe1`: main Claude Code sidebar/chat view.
- `Rs`: session list component.
- `OR0`: individual session row component.
- `Re1`: history popover/flyout wrapper.
- `renameSession(sessionId, title)`: existing session rename path.
- `deleteSession(session)`: existing delete path.
- `session.busy.value`: available running/busy state.
- `session.summary.value`: current session display title.
- `session.sessionId.value`: persistent session identifier.
- `session.lastModifiedTime.value`: timestamp used for sorting.
- `Xs`: current bundled rewind confirmation dialog.
- `mX`: current bundled shared modal/dialog component.
- `rewindCode(userMessageId, { dryRun })`: dry-run request used by the rewind dialog.

Use the real source names if they differ from these minified/bundled names.

### Layout Change

In the main Claude sidebar view, render the session list next to the existing chat content.

Desired DOM/component structure:

```tsx
<div className={styles.body}>
  <div className={styles.content}>
    {/* existing chat/session body */}
  </div>

  <div
    className={styles.sessionResizeHandle}
    onPointerDown={startSessionResize}
  />

  <aside className={styles.inlineSessions}>
    <SessionList
      localSessions={localSessionsSorted}
      remoteSessions={remoteSessionsSorted}
      localSessionsLoaded={sessions.localSessionsLoaded.value}
      remoteSessionsLoaded={sessions.remoteSessionsLoaded.value}
      remoteConnected={sessions.remoteConnected.value}
      remoteReconnecting={sessions.remoteReconnecting.value}
      activeSession={sessions.activeSession.value ?? null}
      onSessionClick={openSessionAndClearDone}
      onRenameSession={renameSession}
      onDeleteSession={deleteSession}
      onOpenInNewWindow={openInNewWindow}
      onRefresh={() => {
        sessions.listSessions();
        sessions.listRemoteSessions();
      }}
      currentCwd={context.defaultCwd.value}
      authMethod={context.authStatus.value?.authMethod}
      onOpenURL={context.openURL}
    />
  </aside>
</div>
```

The session pane should appear on the right, not above the chat.

Use flexbox:

```css
.body {
  display: flex;
  min-width: 0;
  min-height: 0;
}

.content {
  flex: 1 1 auto;
  min-width: 0;
  min-height: 0;
  position: relative;
  z-index: 2;
  overflow: visible;
}

.inlineSessions {
  position: relative;
  z-index: 0;
  flex: 0 0 var(--claude-session-pane-width, min(44vw, 360px));
  min-width: 180px;
  max-width: 75%;
  overflow: hidden;
  border-left: 1px solid var(--app-primary-border-color);
  background: var(--app-primary-background);
}

.inlineSessions > * {
  height: 100%;
}

.sessionResizeHandle {
  position: relative;
  z-index: 0;
  flex: 0 0 4px;
  cursor: col-resize;
  background: var(--app-primary-border-color);
  opacity: 0.5;
}

.sessionResizeHandle:hover,
.sessionResizeHandle:active {
  opacity: 1;
  background: var(--vscode-sash-hoverBorder, var(--app-primary-border-color));
}
```

### Resize Behavior

Implement pointer-based resizing. The divider should adjust a CSS variable on the root element or containing layout element.

Example:

```ts
function startSessionResize(event: React.PointerEvent) {
  event.preventDefault();

  const root = event.currentTarget.parentElement;
  const pane = root?.querySelector<HTMLElement>('[data-claude-session-pane]');
  if (!pane) return;

  const startX = event.clientX;
  const startWidth = pane.getBoundingClientRect().width;

  function onMove(moveEvent: PointerEvent) {
    const nextWidth = Math.max(
      180,
      Math.min(window.innerWidth * 0.75, startWidth - (moveEvent.clientX - startX)),
    );

    document.documentElement.style.setProperty(
      '--claude-session-pane-width',
      `${nextWidth}px`,
    );
  }

  function onUp() {
    document.removeEventListener('pointermove', onMove);
    document.removeEventListener('pointerup', onUp);
  }

  document.addEventListener('pointermove', onMove);
  document.addEventListener('pointerup', onUp);
}
```

Because the pane is on the right, dragging left should make it wider and dragging right should make it narrower. The formula above uses:

```ts
startWidth - (moveEvent.clientX - startX)
```

That is intentional.

### Modal Layering

The persistent session pane adds a new sibling next to the chat pane. Any modal opened from the chat pane must render above both panes and above the resize handle.

Implementation guidance:

- Prefer rendering modals through the app's existing portal/root modal system if one exists.
- If the modal is rendered inside the chat pane, make sure the chat pane has a higher stacking context than the session pane.
- Keep the session pane and resize handle at a low z-index.
- Use a modal overlay z-index higher than split-pane UI, context menus, resize handles, and session row actions.
- Use a sufficiently opaque backdrop so the composer and session list do not visually bleed through the dialog.

Example CSS:

```css
.content {
  position: relative;
  z-index: 2;
  overflow: visible;
}

.inlineSessions,
.sessionResizeHandle {
  position: relative;
  z-index: 0;
}

.modalOverlay {
  position: fixed;
  inset: 0;
  z-index: 10000;
  background: rgba(0, 0, 0, 0.9);
}
```

If using CSS modules, apply this to the actual shared modal overlay used by rewind/context dialogs. In the bundled VSIX this currently corresponds conceptually to the shared modal component used by `mX`, with an overlay class similar to `overlay_f3sAzg`.

### Pin and Star Behavior

Add two session actions:

- `Pin`: pins/unpins the session and sorts pinned sessions to the top.
- `Star`: prefixes the visible session title with a star.

Best native implementation:

- Store pin/star metadata separately from the title if the extension has session metadata or global storage.
- Avoid modifying the user-visible title for pinning if there is a better metadata field.
- For starring, if no metadata/storage option exists, prefixing `⭐ ` to the title is acceptable.

Fallback implementation if there is no session metadata API:

```ts
function getSessionTitle(session: Session): string {
  return (session.summary?.value || 'Untitled').trim();
}

function isPinned(session: Session): boolean {
  const title = getSessionTitle(session);
  return title.startsWith('📌 ') || title.startsWith('⭐ 📌 ');
}

function starTitle(session: Session): string {
  const title = getSessionTitle(session);
  return title.startsWith('⭐ ') ? title : `⭐ ${title}`;
}

function togglePinTitle(session: Session): string {
  const title = getSessionTitle(session);
  const starred = title.startsWith('⭐ ');
  const body = starred ? title.slice(2).trimStart() : title;

  if (body.startsWith('📌 ')) {
    const unpinned = body.slice(2).trimStart();
    return starred ? `⭐ ${unpinned}` : unpinned;
  }

  return starred ? `⭐ 📌 ${body}` : `📌 ${body}`;
}
```

Then call the existing rename/session-title update path:

```ts
onRenameSession(session.sessionId.value, starTitle(session));
onRenameSession(session.sessionId.value, togglePinTitle(session));
```

Important:

- Do not remove or replace the existing rename/edit button.
- Do not remove or replace the existing delete button.
- Do not cram pin/star buttons into the same hover action area if it causes the edit/delete buttons to disappear.
- Prefer a right-click context menu for `Pin` and `Star`, or a small overflow menu if the design system has one.

### Context Menu

Add a context menu to each session row.

Behavior:

- Right-click a session row.
- Show menu items:
  - `Pin` or `Unpin`
  - `Star`
- Selecting a menu item should immediately perform the action.
- The menu should not close before the click handler runs.

Implementation warning:

If the menu is closed by a document-level `mousedown` handler, button `click` may never fire. Use `onMouseDown` or `onPointerDown` for the menu items, or delay the outside-click listener.

Example:

```tsx
function SessionContextMenu({ x, y, onPin, onStar, onClose }) {
  function run(action: () => void) {
    return (event: React.MouseEvent) => {
      event.preventDefault();
      event.stopPropagation();
      action();
      onClose();
    };
  }

  return (
    <div className={styles.contextMenu} style={{ left: x, top: y }}>
      <button onMouseDown={run(onPin)}>Pin</button>
      <button onMouseDown={run(onStar)}>Star</button>
    </div>
  );
}
```

### Sorting

Pinned sessions should sort above unpinned sessions.

Within each pinned/unpinned group, preserve the current recency sort.

Example:

```ts
function compareSessions(a: Session, b: Session): number {
  return Number(isPinned(b)) - Number(isPinned(a))
    || b.lastModifiedTime.value - a.lastModifiedTime.value;
}

const localSessionsSorted = [...sessions.value].sort(compareSessions);
const remoteSessionsSorted = [...remoteSessions.value].sort(compareSessions);
```

Do not group by workspace for this request. Claude Code does not currently expose workspace grouping clearly in the sidebar UX, and adding inferred workspace groups caused confusion.

### Running Indicator

Show a small loading indicator for sessions where:

```ts
session.busy.value === true
```

Place this indicator in the session row near the session title or metadata. It should not hide the edit/delete controls.

Example row fragment:

```tsx
<span className={styles.sessionName}>
  {highlightedSessionName}
</span>

{session.busy.value && (
  <span
    className={styles.sessionStatusRunning}
    title="Running"
    aria-label="Running"
  />
)}
```

CSS:

```css
.sessionStatusRunning {
  box-sizing: border-box;
  width: 10px;
  height: 10px;
  border-radius: 50%;
  border: 2px solid var(--app-secondary-foreground);
  border-top-color: transparent;
  animation: sessionStatusSpin 0.8s linear infinite;
  flex: 0 0 auto;
}

@keyframes sessionStatusSpin {
  to {
    transform: rotate(360deg);
  }
}
```

### Done Dot

When a session transitions from running to idle, show a blue dot until the user opens that session.

Track previous busy state by session id:

```ts
const previousBusyBySession = new Map<string, boolean>();
const completedSessions = new Set<string>();

function updateSessionStatus(session: Session) {
  const id = session.sessionId.value;
  const busy = Boolean(session.busy.value);
  const previous = previousBusyBySession.get(id);

  if (previous === true && busy === false) {
    completedSessions.add(id);
  }

  previousBusyBySession.set(id, busy);
}

function clearCompleted(session: Session) {
  completedSessions.delete(session.sessionId.value);
}
```

In React source, this should probably be component state or a small hook so changes cause a re-render:

```ts
function useSessionStatus(session: Session) {
  const [, forceRender] = useState(0);

  useEffect(() => {
    const id = session.sessionId.value;
    const busy = Boolean(session.busy.value);
    const previous = previousBusyBySession.get(id);

    if (previous === true && busy === false) {
      completedSessions.add(id);
      forceRender((value) => value + 1);
    }

    previousBusyBySession.set(id, busy);
  }, [session, session.busy.value]);

  return {
    running: Boolean(session.busy.value),
    done: completedSessions.has(session.sessionId.value),
    clearDone: () => {
      completedSessions.delete(session.sessionId.value);
      forceRender((value) => value + 1);
    },
  };
}
```

Row behavior:

```tsx
const status = useSessionStatus(session);

function handleClick(event: React.MouseEvent) {
  status.clearDone();
  onClick(event);
}
```

Blue dot CSS:

```css
.sessionStatusDone {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--vscode-charts-blue, #3b82f6);
  flex: 0 0 auto;
}
```

Display priority:

1. If running, show spinner.
2. Else if completed/unread, show blue dot.
3. Else show no indicator.

### Existing Edit/Delete Must Stay Intact

The current session row already has hover actions:

- Rename/edit session.
- Delete session.

Do not replace this block. Keep the original conditions and callbacks.

The row should continue to use the existing inline edit behavior:

- Start rename/edit.
- Content editable or input appears.
- Enter commits.
- Escape cancels.
- Blur commits if changed.

The delete behavior should also remain unchanged.

### Session Popover

Do not remove the existing popover unless the product team wants to remove it.

Acceptable options:

- Keep the popover available from the clock/history button for users who expect it.
- Make the persistent right-side session pane the primary session browser.
- Avoid making the popover huge or full-screen. The goal is not a bigger popup. The goal is fewer popups.

### Avoid Workspace Grouping

Do not add workspace grouping as part of this change.

Reason:

- Claude Code does not currently present workspaces in this sidebar in a way that users can clearly map.
- Inferred workspace labels can be wrong or confusing.
- The higher-value improvement is a persistent, sortable session pane with pin/star/status.

### Rewind Should Work Without File Changes

The rewind confirmation dialog should not disable the primary action just because the dry-run reports no changed files.

Current bundled behavior, conceptually:

```ts
const hasFileChanges = result?.filesChanged && result.filesChanged.length > 0;
const canClickRewind = result?.canRewind && !loading && (hasFileChanges || willForkAfter);
```

Desired behavior:

```ts
const hasFileChanges = result?.filesChanged && result.filesChanged.length > 0;
const canClickRewind = result?.canRewind && !loading;
```

Why:

- A user may want to rewind the conversation/context even when no files changed.
- The dry-run result can correctly say no code will be restored, but that should not prevent a context rewind if the backend reports `canRewind`.
- Keep the button disabled while loading or when `canRewind` is false.

The confirmation text can still say:

```text
The code has not changed, so no code will be restored.
```

But the primary action should remain available when context rewind is possible.

### Acceptance Criteria

The change is complete when:

- The Claude sidebar shows chat and sessions side by side.
- The sessions pane is on the right.
- The divider can resize the sessions pane smoothly.
- Existing rename/edit is still visible and works.
- Existing delete is still visible and works.
- Right-clicking a session shows `Pin` and `Star`.
- Pinning moves the session to the top of the list.
- Starring visibly marks the session.
- A running session shows a spinner.
- A finished session shows a blue dot.
- Clicking/opening a finished session clears the blue dot.
- The history popover is not the only practical way to browse sessions.
- No workspace grouping is added.
- "Rewind code" can be confirmed when `canRewind` is true, even if no files changed.
- Rewind/modal dialogs appear above the session pane, resize divider, composer, and chat content.
- Modal backdrops prevent underlying controls from visually peeking through.

### Test Plan

Manual tests:

1. Open Claude Code in the VS Code sidebar.
2. Confirm chat appears on the left and sessions appear on the right.
3. Drag the divider left and right.
4. Start a new Claude session.
5. Confirm the running session shows a spinner.
6. Wait for the session to finish.
7. Confirm the spinner becomes a blue dot.
8. Click the session.
9. Confirm the blue dot clears.
10. Right-click the session.
11. Choose `Pin`.
12. Confirm the session moves to the top.
13. Right-click the session.
14. Choose `Star`.
15. Confirm the session is visibly starred.
16. Hover the row.
17. Confirm the edit/rename button is visible.
18. Rename the session.
19. Confirm the new title persists.
20. Confirm delete still appears and still works.
21. Open the `Rewind code` dialog on a message where no files changed.
22. Confirm the `Rewind` button is enabled if the dry-run returns `canRewind`.
23. Confirm the rewind dialog is not covered by the session pane or resize divider.
24. Confirm the composer and messages underneath do not visibly peek through the modal backdrop.

Regression tests:

- Search sessions still filters correctly.
- Local/Web tabs still work.
- Remote disconnected state still renders.
- Empty state still renders.
- Keyboard navigation still works.
- Opening a session from the list still selects the correct active session.
- The existing history popover still opens if retained.
- Other shared modals still appear above the split-pane UI.

## Part 3: Follow-up — Sidebar Toggle and Layout Hardening

After living with the persistent right-side session pane for a few days, two classes of issues became obvious:

1. There is no way to hide the session pane. Sometimes users want the full width back — for reading long code blocks, reviewing wide diffs, or screen-sharing the chat alone. The resize handle minimum (180px) is too wide to act as a hide.
2. The split-pane layout was visually unstable. Long chat messages, wide code fences, or large tool output could push the divider, force the session pane offscreen, or let chat content overflow into the session list area. Some modal dialogs also rendered *underneath* the resize divider because only one of the bundle's five overlay classes was z-index-bumped.

### Requested additions

- A header icon button placed **immediately to the left of the existing "Session history" clock button** that hides/shows the inline session pane and its resize handle.
- The toggle state should persist across window reloads (`localStorage`-backed is acceptable; a real settings key is preferable in a native implementation).
- The icon should visually reflect state — full sidebar glyph when shown, hollow when hidden — so it works as a status indicator, not just an action.
- While hidden, the resize handle must also disappear (no orphan 4px stripe).
- The most recent dragged pane width should also persist, so toggling back on restores the user's chosen size.

### Layout bugs to fix in the split-pane CSS

These are caused by missing flexbox minimums and incomplete overlay z-index coverage in the patched bundle. They should be fixed in the native source rather than re-patched:

- The chat and session flex children both need `min-width: 0` and `min-height: 0`. Without these, a single wide code block or long unwrapped string anchors the flex item to its intrinsic width and the divider drifts (or the session pane clips out of the viewport).
- The chat pane needs `overflow: hidden` on its own stacking context so internal scroll containers do the scrolling, not the outer flex row.
- All modal overlay classes used by the app's dialog system need z-index above the split-pane UI (≥ 10000), not just the rewind dialog's overlay. In the current bundle that means `overlay_f3sAzg`, `overlay_W2z5EA`, `overlay_yumWmQ`, `overlay_5FHdxw`, and `overlay_ukWSlw`. Backdrops should be sufficiently opaque (~0.9 alpha) so the composer and session list cannot peek through.
- The resize handler should bail when the session pane is hidden — otherwise pointer-down on the (now display:none) handle still binds global pointermove/pointerup listeners.

### Suggested native shape

```tsx
// In the header action group, before <SessionHistoryButton />:
<IconButton
  ariaLabel="Toggle session pane"
  iconSize={20}
  onClick={toggleSessionPane}
>
  <SidebarPaneIcon active={sessionPaneVisible} />
</IconButton>
```

```ts
const [sessionPaneVisible, setSessionPaneVisible] = useSyncedSetting(
  'claude.sessionPane.visible',
  true,
);

function toggleSessionPane() {
  setSessionPaneVisible((value) => !value);
}
```

Then conditionally render the inline pane and resize handle inside the existing split-pane container:

```tsx
<div className={styles.body}>
  <ChatPane />
  {sessionPaneVisible && (
    <>
      <ResizeHandle onPointerDown={startSessionResize} />
      <SessionPane />
    </>
  )}
</div>
```

### Acceptance criteria

- Toggle button appears in the header between "Learn Claude Code" and "Session history".
- Clicking it hides both the session pane and the resize divider; clicking again restores them.
- The chat pane occupies the full sidebar width while the session pane is hidden.
- State survives reload of the VS Code window.
- Icon visually changes between shown and hidden states.
- With the session pane visible, a chat message containing a very long unwrapped line or a wide code block does not push the divider, does not overlap the session list, and the chat scroll container handles the overflow as before.
- Every shared modal (rewind, confirm delete, settings, etc.) renders above the session pane, resize divider, and composer, with an opaque enough backdrop that underlying UI does not visually bleed through.

### Test plan

1. Open Claude Code in the sidebar with at least one session running and several completed sessions visible.
2. Click the new toggle button to the left of the clock icon. Confirm the session pane and the resize handle both disappear and the chat fills the sidebar.
3. Reload the window. Confirm the session pane is still hidden.
4. Click the toggle again. Confirm the session pane returns at the previously-dragged width.
5. Drag the resize divider to a new width. Reload. Confirm the width was preserved.
6. With the session pane visible, paste a long code block (≥ 200 chars per line, no wrapping) into the chat. Confirm the divider does not move, the session pane stays at its set width, and the chat content scrolls horizontally within its own pane.
7. With the session pane visible, open `Rewind code` and any other modal dialog. Confirm each modal sits above the session pane, the resize divider, and the composer, and the backdrop is fully opaque.
8. With the session pane hidden, attempt to drag where the handle used to be. Confirm no resize occurs and no stray pointer listeners are bound.
