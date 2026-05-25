#!/usr/bin/env python
"""
patch_claude_vsix_v147.py
Patches Claude Code v2.1.147 (and nearby versions with matching anchors).

New features vs the base script:
  - Archive sessions: right-click or button replaces delete
    * Non-archived sessions show an archive button (orange box icon)
    * Archived sessions show a permanent delete button
    * Right-click menu: Pin / Star / Archive (or Unarchive)
  - Sort order: pinned first, then by time, archived sessions sink to bottom
  - Status dot moved to LEFT of session name (Cursor-style)
  - Improved time display: "2 days ago", "1 hr ago" etc.
  - Yolo mode toggle button in header (bypass permissions)
  - Inline resizable sessions panel with correct v2.1.147 variable names

Usage:
  python patch_claude_vsix_v147.py
  python patch_claude_vsix_v147.py anthropic.claude-code-2.1.147.vsix
  python patch_claude_vsix_v147.py anthropic.claude-code-2.1.147.vsix --out my-patched.vsix
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import shutil
import subprocess
import tempfile
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

# PATCHER_VERSION is injected into the patched webview/index.js as
# `var ccPatchBuildVersion = "x.y.z"`. Orbit reads it from the installed
# Claude Code to decide whether the user's patched build is behind.
# The version itself is supplied at runtime via --patcher-version so that
# build.py can pin it to package.json's version (single source of truth).
PATCHER_VERSION: str = "dev"

DEFAULT_MARKETPLACE_ITEM = "anthropic.claude-code"
MARKETPLACE_QUERY_URL = (
    "https://marketplace.visualstudio.com/_apis/public/gallery/"
    "extensionquery?api-version=7.2-preview.1"
)
LOG_PATH: Path | None = None


def log(message: str) -> None:
    line = f"[{dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {message}"
    print(line, flush=True)
    if LOG_PATH is not None:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with LOG_PATH.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def write(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


def marketplace_item_from_target(target: str) -> str | None:
    if target.startswith(("http://", "https://")):
        item = urllib.parse.parse_qs(
            urllib.parse.urlparse(target).query
        ).get("itemName", [""])[0].strip()
        return item or None
    if "." in target and not any(sep in target for sep in ("/", "\\")) and not target.lower().endswith(".vsix"):
        return target
    return None


def download_marketplace_vsix(item: str, dest_dir: Path, version: str | None = None) -> Path:
    body = {"filters": [{"criteria": [{"filterType": 7, "value": item}]}], "flags": 914}
    req = urllib.request.Request(
        MARKETPLACE_QUERY_URL,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", "Accept": "application/json;api-version=7.2-preview.1"},
    )
    with urllib.request.urlopen(req, timeout=60) as response:
        data = json.load(response)
    extension = data["results"][0]["extensions"][0]
    selected = next((v for v in extension["versions"] if not version or v["version"] == version), None)
    if selected is None:
        raise RuntimeError(f"Version {version or 'latest'} not found for {item}")
    package = next(f for f in selected["files"] if f.get("assetType", "").endswith("VSIXPackage"))
    publisher = extension["publisher"]["publisherName"]
    name = extension["extensionName"]
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{publisher}.{name}-{selected['version']}.vsix"
    log(f"Downloading {publisher}.{name} {selected['version']} to {dest}")
    urllib.request.urlretrieve(package["source"], dest)
    log(f"Downloaded VSIX size: {dest.stat().st_size} bytes")
    return dest


# --------------------------------------------------------------------------- #
# Helper JS — injected once at the session-list constant block                #
# --------------------------------------------------------------------------- #
CLAUDE_HELPER_JS = r"""function ccPatchTitle(e){return(e.summary?.value||`Untitled`).trim()}
function ccPatchGetSS(e){try{let s=JSON.parse(localStorage.getItem(`ccPatchSS`)||`{}`);return s[ccPatchSessionId(e)]||{}}catch(err){return{}}}
function ccPatchSetSS(e,p){try{let s=JSON.parse(localStorage.getItem(`ccPatchSS`)||`{}`),id=ccPatchSessionId(e);s[id]={...(s[id]||{}),...p};localStorage.setItem(`ccPatchSS`,JSON.stringify(s))}catch(err){}ccPatchFilterListeners.forEach(function(fn){try{fn()}catch(err){}})}
function ccPatchIsArchived(e){return!!ccPatchGetSS(e).archived}
function ccPatchIsPinned(e){return!!ccPatchGetSS(e).pinned}
function ccPatchIsStarred(e){return!!ccPatchGetSS(e).starred}
function ccPatchToggleArchive(e){let c=ccPatchGetSS(e);ccPatchSetSS(e,c.archived?{archived:false}:{archived:true,pinned:false,starred:false})}
function ccPatchTogglePin(e){ccPatchSetSS(e,{pinned:!ccPatchIsPinned(e)})}
function ccPatchToggleStar(e){ccPatchSetSS(e,{starred:!ccPatchIsStarred(e)})}
function ccPatchSortSessions(e,t){let ae=ccPatchIsArchived(e),at=ccPatchIsArchived(t);if(ae!==at)return ae?1:-1;if(!ae){let se=ccPatchIsStarred(e),st=ccPatchIsStarred(t);if(se!==st)return se?-1:1;let pe=ccPatchIsPinned(e),pt=ccPatchIsPinned(t);if(pe!==pt)return pe?-1:1}return t.lastModifiedTime.value-e.lastModifiedTime.value}
function ccPatchSessionId(e){return e.sessionId?.value||e.internalId||ccPatchTitle(e)}
var ccPatchBusyBySession=new Map,ccPatchDoneSessions=new Set;
function ccPatchTrackSessionStatus(e,t){let n=ccPatchSessionId(e),r=!!e.busy?.value,a=ccPatchBusyBySession.get(n);a===void 0?ccPatchBusyBySession.set(n,r):(a&&!r&&(ccPatchDoneSessions.add(n),setTimeout(t,0)),ccPatchBusyBySession.set(n,r))}
function ccPatchClearDone(e){ccPatchDoneSessions.delete(ccPatchSessionId(e))}
function ccPatchIsWaiting(e){let t=e.permissionRequests?.value;return!!(t&&t.length>0)}
function ccPatchSessionIndicator(e,t){if(!t&&ccPatchIsWaiting(e))return`waiting`;if(e.busy?.value)return`running`;if(ccPatchDoneSessions.has(ccPatchSessionId(e)))return`done`;return``}
function ccPatchActivityText(e){if(e.permissionRequests?.value?.length)return`Waiting for input`;if(!e.busy?.value)return null;let m=e.messages?.value;if(m&&m.length>0){let l=m[m.length-1];if(l){let c=l.content;if(Array.isArray(c)){for(let j=c.length-1;j>=0;j--){let b=c[j];if(!b)continue;if(b.type===`tool_use`){let n=b.name||``,inp=b.input;if(inp&&inp.command)return`Running `+String(inp.command).replace(/\s+/g,` `).slice(0,50);if(inp&&inp.file_path)return(n===`Read`?`Reading `:`Editing `)+String(inp.file_path).split(/[/\\]/).pop();return`Running `+n.replace(/_/g,` `)+`...`}if(b.type===`thinking`)return`Thinking...`;if(b.type===`text`&&b.text)return String(b.text).replace(/\s+/g,` `).slice(0,50).trim()}}}}return`Thinking...`}
function ccPatchCloseMenu(){document.querySelector(`.claudePatchContextMenu`)?.remove()}
function ccPatchShowMenu(e,t,pLabel,n,sLabel,r,a){ccPatchCloseMenu();let i=document.createElement(`div`);i.className=`claudePatchContextMenu`,i.style.left=`${Math.min(e,window.innerWidth-150)}px`,i.style.top=`${Math.min(t,window.innerHeight-96)}px`;let s=(o,c)=>{let u=document.createElement(`button`),f=!1,d=(v)=>{v.preventDefault(),v.stopPropagation();if(f)return;f=!0,ccPatchCloseMenu(),c()};return u.textContent=o,u.onmousedown=d,u.onclick=d,i.appendChild(u),u};s(pLabel,n);s(sLabel,r);if(a)s(a.label,a.fn);document.body.appendChild(i);setTimeout(()=>document.addEventListener(`mousedown`,ccPatchCloseMenu,{once:!0}),0)}
var ccPatchPaneVis=(function(){try{return localStorage.getItem(`ccPatchPaneVis`)!==`false`}catch(e){return true}})();
function ccPatchTogglePane(){ccPatchPaneVis=!ccPatchPaneVis;try{localStorage.setItem(`ccPatchPaneVis`,String(ccPatchPaneVis))}catch(e){}document.documentElement.classList.toggle(`ccPatchPaneHidden`,!ccPatchPaneVis)}
if(!ccPatchPaneVis)document.documentElement.classList.add(`ccPatchPaneHidden`);
var ccPatchSearchQ=``;
function ccPatchSetSearch(q){ccPatchSearchQ=q;ccPatchFilterListeners.forEach(function(fn){try{fn()}catch(err){}})}
function ccPatchToggleSearch(){let p=document.querySelector(`.claudePatchInlineSessions`),i=document.querySelector(`.ccPatchSearchInput`);if(!p||!i)return;let v=p.classList.toggle(`ccPatchSearchActive`);if(v){setTimeout(function(){i.focus();i.select()},0)}else{ccPatchSetSearch(``);i.value=``}}
function ccPatchStartResize(e){e.preventDefault();if(!ccPatchPaneVis)return;let t=e.currentTarget.parentElement?.querySelector(`.claudePatchInlineSessions`);if(!t)return;let p=t.parentElement,pw=p?p.getBoundingClientRect().width:window.innerWidth,n=e.clientX,r=t.getBoundingClientRect().width,a=(i)=>{let s=Math.max(140,Math.min(pw*0.55,r-(i.clientX-n)));document.documentElement.style.setProperty(`--claude-patch-sessions-width`,`${s}px`)},o=()=>{document.removeEventListener(`pointermove`,a),document.removeEventListener(`pointerup`,o)};document.addEventListener(`pointermove`,a),document.addEventListener(`pointerup`,o)}
var ccPatchFilterListeners=new Set;
function ccPatchAgeMsMap(){return{"1h":36e5,"24h":864e5,"7d":6048e5,"30d":2592e6}}
function ccPatchDefaultFilters(){return{types:[],ages:[],hideUntitled:!0}}
function ccPatchReadFilters(){try{let e=JSON.parse(localStorage.getItem(`claudePatchFilters`)||`null`);if(e&&typeof e===`object`)return{types:Array.isArray(e.types)?e.types.slice():[],ages:Array.isArray(e.ages)?e.ages.slice():[],hideUntitled:e.hideUntitled!==!1}}catch(t){}return ccPatchDefaultFilters()}
function ccPatchIsUntitledEmpty(s){if(!s)return!1;let sum=s.summary?.value,msgs=s.messages?.value;return(!sum||!String(sum).trim())&&(!msgs||!msgs.length)}
function ccPatchWriteFilters(e){try{localStorage.setItem(`claudePatchFilters`,JSON.stringify(e))}catch(t){}ccPatchFilterListeners.forEach((n)=>{try{n()}catch(r){}})}
function ccPatchFiltersActive(){let e=ccPatchReadFilters();return e.types.length+e.ages.length}
function ccPatchSessionMatchesFilters(e,t){if(!t)t=ccPatchReadFilters();if(t.types.length){let n=!1;for(let r of t.types){if(r===`pinned`&&ccPatchIsPinned(e))n=!0;else if(r===`starred`&&ccPatchIsStarred(e))n=!0;else if(r===`running`&&e.busy?.value)n=!0;else if(r===`waiting`&&ccPatchIsWaiting(e))n=!0;if(n)break}if(!n)return!1}if(t.ages.length){let n=ccPatchAgeMsMap(),r=0;for(let a of t.ages){let i=n[a]||0;if(i>r)r=i}if(r>0){let a=e.lastModifiedTime?.value;if(typeof a!==`number`||Date.now()-a>r)return!1}}return!0}
function ccPatchFilterSort(e){let t=ccPatchReadFilters(),q=ccPatchSearchQ.trim().toLowerCase(),n=t.types.length||t.ages.length?e.filter((r)=>ccPatchSessionMatchesFilters(r,t)):[...e];if(t.hideUntitled)n=n.filter((r)=>!ccPatchIsUntitledEmpty(r));if(q)n=n.filter((r)=>ccPatchTitle(r).toLowerCase().includes(q));return n.sort(ccPatchSortSessions)}
function ccPatchCloseFilterMenu(){let m=document.querySelector(`.claudePatchFilterMenu`);if(m&&m._ccPatchOutsideHandler){document.removeEventListener(`mousedown`,m._ccPatchOutsideHandler)}m?.remove();document.querySelector(`.ccPatchFilterButton.ccPatchFilterButtonOpen`)?.classList.remove(`ccPatchFilterButtonOpen`)}
function ccPatchFilterIconSVG(name){let svg=document.createElementNS(`http://www.w3.org/2000/svg`,`svg`);svg.setAttribute(`width`,`12`);svg.setAttribute(`height`,`12`);svg.setAttribute(`viewBox`,`0 0 12 12`);svg.setAttribute(`fill`,`none`);svg.setAttribute(`stroke`,`currentColor`);svg.setAttribute(`stroke-width`,`1.3`);svg.setAttribute(`stroke-linecap`,`round`);svg.setAttribute(`stroke-linejoin`,`round`);svg.classList.add(`ccPatchFilterItemIcon`);let paths={pinned:`<circle cx="6" cy="4.5" r="2.5"/><line x1="6" y1="7" x2="6" y2="11"/>`,starred:`<polygon points="6,1.5 7.2,4.5 10.5,4.9 8.2,7.1 8.8,10.5 6,9 3.2,10.5 3.8,7.1 1.5,4.9 4.8,4.5"/>`,running:`<circle cx="6" cy="6" r="4"/><line x1="6" y1="3.5" x2="6" y2="6"/><line x1="6" y1="6" x2="8" y2="7.2"/>`,waiting:`<circle cx="6" cy="6" r="4"/><line x1="6" y1="6" x2="6" y2="3.5"/><line x1="6" y1="6" x2="8" y2="6"/>`,"1h":`<circle cx="6" cy="6" r="4.5"/><polyline points="6,3.5 6,6 7.6,7"/>`,"24h":`<rect x="1.5" y="2.5" width="9" height="8" rx="1"/><line x1="1.5" y1="5" x2="10.5" y2="5"/><line x1="4" y1="1.5" x2="4" y2="3.5"/><line x1="8" y1="1.5" x2="8" y2="3.5"/>`,"7d":`<rect x="1.5" y="2.5" width="9" height="8" rx="1"/><line x1="1.5" y1="5" x2="10.5" y2="5"/><line x1="4" y1="1.5" x2="4" y2="3.5"/><line x1="8" y1="1.5" x2="8" y2="3.5"/>`,"30d":`<rect x="1.5" y="2.5" width="9" height="8" rx="1"/><line x1="1.5" y1="5" x2="10.5" y2="5"/><line x1="4" y1="1.5" x2="4" y2="3.5"/><line x1="8" y1="1.5" x2="8" y2="3.5"/>`};svg.innerHTML=paths[name]||``;return svg}
function ccPatchShowFilterMenu(e){if(document.querySelector(`.claudePatchFilterMenu`)){ccPatchCloseFilterMenu();return}let t=e.currentTarget;if(!t)return;t.classList.add(`ccPatchFilterButtonOpen`);let n=t.getBoundingClientRect(),r=document.createElement(`div`);r.className=`claudePatchFilterMenu`,r.style.top=`${Math.round(n.bottom+4)}px`,r.style.left=`${Math.min(Math.round(n.left),window.innerWidth-260)}px`;let i=ccPatchReadFilters(),s=(g,b)=>{let m=document.createElement(`div`);m.className=`claudePatchFilterGroup`;let f=document.createElement(`div`);f.className=`claudePatchFilterGroupTitle`,f.textContent=g,m.appendChild(f);for(let[v,w,icon]of b){let y=document.createElement(`label`);y.className=`claudePatchFilterOption`;let k=document.createElement(`input`);k.type=`checkbox`;let A=g===`Type`?`types`:`ages`;k.checked=i[A].includes(v),k.onchange=()=>{let z=ccPatchReadFilters();if(k.checked){if(!z[A].includes(v))z[A].push(v)}else z[A]=z[A].filter((q)=>q!==v);ccPatchWriteFilters(z),i=z};let _=document.createElement(`span`);_.textContent=w;_.className=`ccPatchFilterItemLabel`;y.appendChild(k);if(icon)y.appendChild(ccPatchFilterIconSVG(icon));y.appendChild(_);m.appendChild(y)}r.appendChild(m)};s(`Type`,[[`pinned`,`Pinned`,`pinned`],[`starred`,`Starred`,`starred`],[`running`,`Running`,`running`],[`waiting`,`Waiting`,`waiting`]]);s(`Age`,[[`1h`,`Last 1 hour`,`1h`],[`24h`,`Last 24 hours`,`24h`],[`7d`,`Last 7 days`,`7d`],[`30d`,`Last 30 days`,`30d`]]);(function(){let hg=document.createElement(`div`);hg.className=`claudePatchFilterGroup`;let ht=document.createElement(`div`);ht.className=`claudePatchFilterGroupTitle`;ht.textContent=`Hide`;hg.appendChild(ht);let hl=document.createElement(`label`);hl.className=`claudePatchFilterOption`;let hc=document.createElement(`input`);hc.type=`checkbox`;hc.checked=i.hideUntitled;hc.onchange=function(){let z=ccPatchReadFilters();z.hideUntitled=hc.checked;ccPatchWriteFilters(z);i=z};let hs=document.createElement(`span`);hs.textContent=`Untitled chats`;hs.className=`ccPatchFilterItemLabel`;hl.appendChild(hc);hl.appendChild(hs);hg.appendChild(hl);r.appendChild(hg)})();let o=document.createElement(`div`);o.className=`claudePatchFilterFooter`;let c=document.createElement(`button`);c.textContent=`Clear all`,c.onclick=(g)=>{g.preventDefault(),g.stopPropagation(),ccPatchWriteFilters(ccPatchDefaultFilters()),ccPatchCloseFilterMenu()},o.appendChild(c),r.appendChild(o),document.body.appendChild(r);setTimeout(()=>{let h=(g)=>{if(!r.contains(g.target)&&!t.contains(g.target))ccPatchCloseFilterMenu()};r._ccPatchOutsideHandler=h;document.addEventListener(`mousedown`,h)},0)}
function ccPatchYoloOn(){return document.documentElement.classList.contains(`ccPatchYoloMode`)}
function ccPatchYoloDefault(){return ccPatchYoloOn()?`bypassPermissions`:`default`}
function ccPatchYoloApplyArr(arr,on){if(!arr)return;arr.forEach(function(s){try{if(s&&s.permissionMode)s.permissionMode.value=on?`bypassPermissions`:`default`}catch(e){}})}
// YOLO always starts OFF on every page load and reinstall. The previous version
// persisted state via localStorage, but the init only re-added the visual class —
// it didn't flip existing sessions' permissionMode signals, which caused a
// silent desync: button looked ON, prompts still appeared. Starting OFF means
// the visual state and functional state can only change together via the toggle.
function ccPatchYoloToggle($){var on=!ccPatchYoloOn();document.documentElement.classList.toggle(`ccPatchYoloMode`,on);if($){try{ccPatchYoloApplyArr($.sessions&&$.sessions.value,on)}catch(e){}try{ccPatchYoloApplyArr($.remoteSessions&&$.remoteSessions.value,on)}catch(e){}}}
"""


def patch_webview_js(webview_js: Path) -> bool:
    text = read(webview_js)
    changed = False

    # ── 1. Inject helper functions ──────────────────────────────────────────
    anchor = "var _R0=16,wR0=1000;"
    if "function ccPatchStarTitle(" not in text:
        if anchor not in text:
            raise RuntimeError("Could not find Claude session-list helper anchor")
        version_line = f'var ccPatchBuildVersion="{PATCHER_VERSION}";'
        text = text.replace(anchor, version_line + CLAUDE_HELPER_JS + anchor, 1)
        changed = True

    # ── 2. Sort pinned sessions first, archived last ────────────────────────
    old_sort = "}):_1,t=S0.useRef(F);"
    new_sort = (
        "}):_1;b=ccPatchFilterSort(b);"
        "let [ccPatchArchState,ccPatchSetArchState]=S0.useState(()=>localStorage.getItem('ccPatchArchiveOpen')==='true');"
        "let [ccPatchPinState,ccPatchSetPinState]=S0.useState(()=>localStorage.getItem('ccPatchPinOpen')!=='false');"
        "let [ccPatchStarState,ccPatchSetStarState]=S0.useState(()=>localStorage.getItem('ccPatchStarOpen')!=='false');"
        "let [ccPatchSessState,ccPatchSetSessState]=S0.useState(()=>localStorage.getItem('ccPatchSessionsOpen')!=='false');"
        # Subscribe to filter changes so the list re-renders when filters toggle
        "{let[ccPFT,ccPFS]=S0.useState(0);"
        "S0.useEffect(()=>{let M=()=>ccPFS((v)=>v+1);ccPatchFilterListeners.add(M);return()=>ccPatchFilterListeners.delete(M)},[])}"
        "let t=S0.useRef(F);"
    )
    if old_sort in text:
        text = text.replace(old_sort, new_sort, 1)
        changed = True

    # ── 3. Track session busy/done status ───────────────────────────────────
    old_status_hook = "z6();let j=S0.useRef(null),D=S0.useRef(!0);"
    new_status_hook = (
        "z6();let j=S0.useRef(null),D=S0.useRef(!0),"
        "[L,I]=S0.useState(0);"
        "S0.useEffect(()=>{ccPatchTrackSessionStatus(Z,()=>I(M=>M+1))},[Z,Z.busy?.value]);"
    )
    if old_status_hook in text:
        text = text.replace(old_status_hook, new_status_hook, 1)
        changed = True

    # ── 4. Right-click context menu (pin / star / archive) ──────────────────
    old_context = (
        'return S0.default.createElement("button",{ref:F,className:`${w2.sessionItem} ${J?w2.active:""} ${Y?w2.focused:""}`,onClick:X?void 0:G,onMouseMove:q},'
    )
    new_context = (
        'return S0.default.createElement("button",{ref:F,className:`${w2.sessionItem} ccPatchSessionItem ${J?w2.active:""} ${Y?w2.focused:""}`,onClick:X?void 0:(M)=>{ccPatchClearDone(Z),I(W5=>W5+1),G(M)},onMouseMove:q,'
        'onContextMenu:(M)=>{if(z&&Z.sessionId.value)M.preventDefault(),M.stopPropagation(),'
        'ccPatchShowMenu(M.clientX,M.clientY,'
        'ccPatchIsPinned(Z)?`Unpin`:`Pin`,()=>ccPatchTogglePin(Z),'
        'ccPatchIsStarred(Z)?`Unstar`:`Star`,()=>ccPatchToggleStar(Z),'
        'ccPatchIsArchived(Z)?{label:`Unarchive`,fn:()=>ccPatchToggleArchive(Z)}:{label:`Archive`,fn:()=>ccPatchToggleArchive(Z)})}},'
    )
    if old_context in text:
        text = text.replace(old_context, new_context, 1)
        changed = True

    # ── 5. Capture session indicator variable ───────────────────────────────
    old_status_var = (
        "let _=J&&!Z.summary.value&&!Z.messages.value.length&&!Z.teleportedMessageCount.value;return"
    )
    new_status_var = (
        "let R1=ccPatchSessionIndicator(Z,J),"
        "_=J&&!Z.summary.value&&!Z.messages.value.length&&!Z.teleportedMessageCount.value;return"
    )
    if old_status_var in text:
        text = text.replace(old_status_var, new_status_var, 1)
        changed = True

    # ── 6. Copilot-style row: status dot LEFT, ternary wraps both edit+display ──
    old_status_insert = (
        'X?S0.default.createElement("span",{ref:j,className:`${w2.sessionName} ${w2.sessionNameEditing}`,'
        'contentEditable:!0,suppressContentEditableWarning:!0,onKeyDown:A,onBlur:P,'
        'onClick:(M)=>M.stopPropagation()},Kk(Z)):S0.default.createElement("span",{className:w2.sessionName},Le1(Kk(Z),Q)),'
        'B&&Z.worktree.value&&Z.worktree.value.path!==W&&'
    )
    new_status_insert = (
        # Status dot on far LEFT (always visible, dims when idle)
        'S0.default.createElement("span",{className:"claudePatchStatus "'
        '+(R1==="running"?"claudePatchStatusRunning":R1==="waiting"?"claudePatchStatusWaiting":R1==="done"?"claudePatchStatusDone":"claudePatchStatusIdle"),'
        'title:R1==="running"?"Running":R1==="waiting"?"Waiting for input":R1==="done"?"Completed":""}),'
        # Ternary: edit mode wraps in ccPatchRowInnerEdit so input fills row cleanly
        'X?'
        'S0.default.createElement("div",{className:"ccPatchRowInner ccPatchRowInnerEdit"},'
        'S0.default.createElement("span",{ref:j,className:`${w2.sessionName} ${w2.sessionNameEditing}`,'
        'contentEditable:!0,suppressContentEditableWarning:!0,onKeyDown:A,onBlur:P,'
        'onClick:(M)=>M.stopPropagation()},Kk(Z)))'
        ':'
        # Display mode: stacked name + time
        'S0.default.createElement("div",{className:"ccPatchRowInner"},'
        'S0.default.createElement("span",{className:w2.sessionName+" ccPatchRowName",'
        'onDoubleClick:(M)=>{M.stopPropagation(),z&&z(Z)}},Le1(Kk(Z),Q)),'
        'S0.default.createElement("span",{className:"ccPatchRowTime"},'
        'R1==="running"||R1==="waiting"?ccPatchActivityText(Z)||NR0(Z.lastModifiedTime.value):NR0(Z.lastModifiedTime.value))'
        '),'  # closes display ccPatchRowInner
        'B&&Z.worktree.value&&Z.worktree.value.path!==W&&'
    )
    if old_status_insert in text:
        text = text.replace(old_status_insert, new_status_insert, 1)
        changed = True

    # ── 7. Hover actions: replace entire sessionMeta block ──────────────────────
    #   - removes: rename button, archive button, duplicate time span
    #   - keeps: pin button (always), delete button (archived sessions only)
    old_archive_delete = (
        ',S0.default.createElement("span",{className:w2.sessionMeta},'
        'S0.default.createElement("span",{className:w2.sessionTime},NR0(Z.lastModifiedTime.value)),'
        '!X&&!_&&(z||H)&&S0.default.createElement("span",{className:w2.sessionActions},'
        'z&&Z.sessionId.value&&S0.default.createElement("span",{role:"button",tabIndex:0,'
        'className:w2.actionButton,'
        'onClick:(M)=>{M.stopPropagation(),z(Z)},'
        'onKeyDown:(M)=>{if(M.key==="Enter"||M.key===" ")M.preventDefault(),M.stopPropagation(),z(Z)},'
        'title:"Rename session"},'
        'S0.default.createElement(sa,{className:w2.actionIcon})),'
        'H&&S0.default.createElement("span",{role:"button",tabIndex:0,'
        'className:`${w2.actionButton} ${w2.deleteButton}`,'
        'onClick:(M)=>{M.stopPropagation(),H(Z)},'
        'onKeyDown:(M)=>{if(M.key==="Enter"||M.key===" ")M.preventDefault(),M.stopPropagation(),H(Z)},'
        'title:"Delete session"},'
        'S0.default.createElement(Ne1,{className:w2.actionIcon})'
        '))))});'
    )
    # Inline SVG icons — codicon font is blocked by webview CSP (no font-src), so
    # we ship hand-crafted SVGs that mirror codicon visual weight.
    # Pin: tilted thumbtack (head at top-right, needle pointing to bottom-left),
    # matches the VS Code codicon "pin" silhouette.
    _ICON_PIN = (
        'S0.default.createElement("svg",{width:12,height:12,viewBox:"0 0 16 16","aria-hidden":true},'
        'S0.default.createElement("path",{'
        'd:"M10.4 1.5l4.1 4.1-1.3 1.3-1.1-1.1-3 3 .7 2.5-1.1 1.1-3-3-3.6 3.6-.7-.7 3.6-3.6-3-3 1.1-1.1 2.5.7 3-3-1.1-1.1z",'
        'fill:ccPatchIsPinned(Z)?"currentColor":"none",'
        'stroke:"currentColor",strokeWidth:1.1,strokeLinejoin:"round"}))'
    )
    _ICON_STAR = (
        'S0.default.createElement("svg",{width:12,height:12,viewBox:"0 0 12 12","aria-hidden":true},'
        'S0.default.createElement("polygon",{points:"6,1.5 7.2,4.5 10.5,4.9 8.2,7.1 8.8,10.5 6,9 3.2,10.5 3.8,7.1 1.5,4.9 4.8,4.5",'
        'fill:ccPatchIsStarred(Z)?"currentColor":"none",'
        'stroke:"currentColor",strokeWidth:1.2,strokeLinejoin:"round"}))'
    )
    _ICON_ARCHIVE = (
        'S0.default.createElement("svg",{width:12,height:12,viewBox:"0 0 12 12",fill:"none",'
        'stroke:"currentColor",strokeWidth:1.2,strokeLinecap:"round","aria-hidden":true},'
        'S0.default.createElement("rect",{x:1,y:2,width:10,height:2.5,rx:0.5}),'
        'S0.default.createElement("path",{d:"M2 4.5v5h8v-5"}),'
        'S0.default.createElement("line",{x1:6,y1:6,x2:6,y2:8.5}),'
        'S0.default.createElement("polyline",{points:"4.5,7 6,8.5 7.5,7"}))'
    )
    _ICON_TRASH = (
        'S0.default.createElement("svg",{width:12,height:12,viewBox:"0 0 12 12",fill:"none",'
        'stroke:"currentColor",strokeWidth:1.2,strokeLinecap:"round","aria-hidden":true},'
        'S0.default.createElement("line",{x1:1.5,y1:3,x2:10.5,y2:3}),'
        'S0.default.createElement("path",{d:"M3 3l.8 8h4.4l.8-8"}),'
        'S0.default.createElement("path",{d:"M4.5 3V2h3v1"}))'
    )
    # Unarchive: same archive box but arrow points UP (lift out of the box)
    _ICON_UNARCHIVE = (
        'S0.default.createElement("svg",{width:12,height:12,viewBox:"0 0 12 12",fill:"none",'
        'stroke:"currentColor",strokeWidth:1.2,strokeLinecap:"round","aria-hidden":true},'
        'S0.default.createElement("rect",{x:1,y:2,width:10,height:2.5,rx:0.5}),'
        'S0.default.createElement("path",{d:"M2 4.5v5h8v-5"}),'
        'S0.default.createElement("line",{x1:6,y1:8.5,x2:6,y2:6}),'
        'S0.default.createElement("polyline",{points:"4.5,7.5 6,6 7.5,7.5"}))'
    )
    new_archive_delete = (
        # Hover-reveal actions: (star + pin shown only on non-archived) + (archived → unarchive+delete, else → archive)
        # Star/pin state is preserved while archived — buttons re-appear on unarchive.
        ',S0.default.createElement("div",{className:"ccPatchRowActions"},'
        '!ccPatchIsArchived(Z)&&S0.default.createElement("span",{role:"button",tabIndex:0,'
        'className:`${w2.actionButton} ccPatchActionBtn ccPatchStarBtn`,'
        'onClick:(M)=>{M.stopPropagation(),ccPatchToggleStar(Z)},'
        'title:ccPatchIsStarred(Z)?"Unstar":"Star"},' + _ICON_STAR + '),'
        '!ccPatchIsArchived(Z)&&S0.default.createElement("span",{role:"button",tabIndex:0,'
        'className:`${w2.actionButton} ccPatchActionBtn ccPatchPinBtn`,'
        'onClick:(M)=>{M.stopPropagation(),ccPatchTogglePin(Z)},'
        'title:ccPatchIsPinned(Z)?"Unpin":"Pin"},' + _ICON_PIN + '),'
        # Unarchive button — only rendered on archived sessions, sits left of delete
        'ccPatchIsArchived(Z)&&S0.default.createElement("span",{role:"button",tabIndex:0,'
        'className:`${w2.actionButton} ccPatchActionBtn ccPatchUnarchiveBtn`,'
        'onClick:(M)=>{M.stopPropagation(),ccPatchToggleArchive(Z)},'
        'title:"Unarchive"},' + _ICON_UNARCHIVE + '),'
        '(ccPatchIsArchived(Z)'
        '?H&&S0.default.createElement("span",{role:"button",tabIndex:0,'
        'className:`${w2.actionButton} ccPatchActionBtn ccPatchDeleteBtn`,'
        'onClick:(M)=>{M.stopPropagation(),H(Z)},'
        'title:"Delete permanently"},' + _ICON_TRASH + ')'
        ':S0.default.createElement("span",{role:"button",tabIndex:0,'
        'className:`${w2.actionButton} ccPatchActionBtn ccPatchArchiveBtn`,'
        'onClick:(M)=>{M.stopPropagation(),ccPatchToggleArchive(Z)},'
        'title:"Archive"},' + _ICON_ARCHIVE + '))'
        ')'   # closes ccPatchRowActions div
        ')});'  # closes rowBtn + fn body + outer wrapper + end
    )
    if old_archive_delete in text:
        text = text.replace(old_archive_delete, new_archive_delete, 1)
        changed = True

    # ── 8. Improve time display: "2 days ago" instead of "2d" ───────────────
    old_nro = (
        'function NR0($){let J=Date.now()-$,Y=Math.floor(J/1000),X=Math.floor(Y/60),'
        'Q=Math.floor(X/60),G=Math.floor(Q/24),q=Math.floor(G/30),z=Math.floor(G/365);'
        'if(z>0)return`${z}y`;if(q>0)return`${q}mo`;if(G>0)return`${G}d`;'
        'if(Q>0)return`${Q}h`;if(X>0)return`${X}m`;return"now"}'
    )
    new_nro = (
        'function NR0($){let J=Date.now()-$,Y=Math.floor(J/1000),X=Math.floor(Y/60),'
        'Q=Math.floor(X/60),G=Math.floor(Q/24),q=Math.floor(G/30),z=Math.floor(G/365);'
        'if(z>0)return z===1?`1 yr ago`:`${z} yrs ago`;'
        'if(q>0)return q===1?`1 mo ago`:`${q} mo ago`;'
        'if(G>0)return G===1?`1 day ago`:`${G} days ago`;'
        'if(Q>0)return Q===1?`1 hr ago`:`${Q} hrs ago`;'
        'if(X>0)return X===1?`1 min ago`:`${X} min ago`;'
        'return`now`}'
    )
    if old_nro in text:
        text = text.replace(old_nro, new_nro, 1)
        changed = True

    # ── 10. Session pane toggle button — replaces session history clock ─────────
    # Inline SVG of a panel with a vertical divider near the right edge
    # (matches VS Code's sidebar-toggle iconography).
    old_toggle = (
        'p0.default.createElement(QQ,{ref:X,ariaLabel:"Session history",iconSize:20,'
        'onClick:()=>q(!G)},p0.default.createElement(in1,null))'
    )
    new_toggle = (
        'p0.default.createElement(QQ,{ariaLabel:"Toggle session pane",iconSize:20,'
        'onClick:ccPatchTogglePane},'
        'p0.default.createElement("span",{className:"ccPatchToggleIcon"},'
        'p0.default.createElement("svg",{width:18,height:18,viewBox:"0 0 18 18",'
        'fill:"none",stroke:"currentColor",strokeWidth:1.4,'
        'strokeLinecap:"round",strokeLinejoin:"round","aria-hidden":true},'
        'p0.default.createElement("rect",{x:2,y:3,width:14,height:12,rx:1.2}),'
        'p0.default.createElement("line",{x1:11.5,y1:3,x2:11.5,y2:15}))))'
    )
    if old_toggle in text:
        text = text.replace(old_toggle, new_toggle, 1)
        changed = True

    # ── 12. Inline sessions panel (correct v2.1.147 variable names) ─────────
    old_inline = (
        'p0.default.createElement("div",{className:h6.body},'
        'p0.default.createElement("div",{className:h6.content},'
    )
    new_inline = (
        'p0.default.createElement("div",{className:h6.body},'
        'p0.default.createElement("div",{className:"claudePatchInlineSessions"},'
        # Slim Copilot-style sidebar header: "Sessions" title + SVG icon row
        'p0.default.createElement("div",{className:"ccPatchSidebarHeader"},'
        'p0.default.createElement("span",{className:"ccPatchSidebarTitle"},"Sessions"),'
        # YOLO mode — replaces Refresh as the first button in the sessions header
        'p0.default.createElement("button",{className:"ccPatchHeaderBtn ccPatchYoloBtn",title:"YOLO mode \\u2014 bypass permission prompts (toggle)",'
        'onClick:function(){ccPatchYoloToggle($)}},'
        'p0.default.createElement("svg",{width:14,height:14,viewBox:"0 0 14 14",fill:"none",'
        'stroke:"currentColor",strokeWidth:1.4,strokeLinejoin:"round",strokeLinecap:"round","aria-hidden":true},'
        'p0.default.createElement("polygon",{points:"7.5,1 3.5,7.5 6.5,7.5 5,13 11,5.8 7.5,5.8"}))),'
        # Search — inline SVG
        'p0.default.createElement("button",{className:"ccPatchHeaderBtn",title:"Search",'
        'onClick:ccPatchToggleSearch},'
        'p0.default.createElement("svg",{width:14,height:14,viewBox:"0 0 14 14",fill:"none",'
        'stroke:"currentColor",strokeWidth:1.4,strokeLinecap:"round","aria-hidden":true},'
        'p0.default.createElement("circle",{cx:6,cy:6,r:4}),'
        'p0.default.createElement("line",{x1:9,y1:9,x2:12.5,y2:12.5}))),'
        # Filter
        'p0.default.createElement("button",{'
        'className:ccPatchFiltersActive()?"ccPatchFilterButton ccPatchHeaderBtn ccPatchFilterButtonActive":"ccPatchFilterButton ccPatchHeaderBtn",'
        'title:"Filter",onClick:ccPatchShowFilterMenu},'
        'p0.default.createElement("span",{className:"ccPatchFilterIcon","aria-hidden":true})),'
        # Usage — opens account-usage panel via slash command equivalent
        'p0.default.createElement("button",{className:"ccPatchHeaderBtn ccPatchUsageBtn",title:"Account & usage",'
        'onClick:()=>{try{Z.commandRegistry.executeCommand("account-usage")}catch(e){}}},'
        'p0.default.createElement("svg",{width:14,height:14,viewBox:"0 0 14 14",fill:"none",'
        'stroke:"currentColor",strokeWidth:1.4,strokeLinecap:"round",strokeLinejoin:"round","aria-hidden":true},'
        'p0.default.createElement("circle",{cx:7,cy:7,r:5.5}),'
        'p0.default.createElement("path",{d:"M8.7 5.2c-.4-.5-1.1-.8-1.8-.8-1 0-1.8.55-1.8 1.3 0 .8.8 1.1 1.8 1.3 1 .25 1.8.55 1.8 1.35 0 .75-.8 1.3-1.8 1.3-.75 0-1.4-.3-1.8-.8"}),'
        'p0.default.createElement("line",{x1:7,y1:3.4,x2:7,y2:4.4}),'
        'p0.default.createElement("line",{x1:7,y1:9.6,x2:7,y2:10.6})))),'
        # (Toggle panel button removed — Toggle session pane in the top toolbar handles this.)
        # Slide-down search row (hidden until .ccPatchSearchActive on panel)
        'p0.default.createElement("div",{className:"ccPatchSearchRow"},'
        'p0.default.createElement("input",{type:"text",className:"ccPatchSearchInput",'
        'placeholder:"Search sessions...",'
        'onInput:(e)=>ccPatchSetSearch(e.target.value),'
        'onKeyDown:(e)=>{if(e.key===`Escape`)ccPatchToggleSearch()}})),'
        # "New Session" standalone button below header
        'p0.default.createElement("button",{className:"ccPatchNewSessionBtn",'
        'onClick:()=>{if(!Z.startNewConversationTab())$.createSession()}},'
        '"New Session"),'
        'p0.default.createElement(Rs,{'
        'localSessions:[...$.sessions.value].sort(ccPatchSortSessions),'
        'localSessionsLoaded:$.localSessionsLoaded.value,'
        'remoteSessions:[...$.remoteSessions.value].sort(ccPatchSortSessions),'
        'remoteConnected:$.remoteConnected.value,'
        'remoteReconnecting:$.remoteReconnecting.value,'
        'remoteSessionsLoaded:$.remoteSessionsLoaded.value,'
        'onReconnectRemote:()=>{$.listRemoteSessions()},'
        'activeSession:$.activeSession.value||null,'
        'onSessionClick:x,'
        'onRenameSession:s,'
        'onDeleteSession:_1,'
        'onOpenInNewWindow:Z.host!=="jetbrains"?r:void 0,'
        'currentCwd:Z.defaultCwd.value,'
        'authMethod:"local",'
        'onRefresh:()=>{$.listSessions(),$.listRemoteSessions()},'
        'onOpenURL:Z.openURL})),'
        'p0.default.createElement("div",{className:"claudePatchResizeHandle",onPointerDown:ccPatchStartResize}),'
        'p0.default.createElement("div",{className:`${h6.content} claudePatchMainContent`},'
    )
    if old_inline in text:
        text = text.replace(old_inline, new_inline, 1)
        changed = True

    # ── 14. Archive section — collapsible folder at bottom of session list ───
    old_sessions_list = (
        'w2.sessionsList},b.map((g1,o1)=>{let h5=o1===w,k2=p===g1.sessionId.value;'
        'return S0.default.createElement(OR0,{key:g1.sessionId.value??o1,'
        'ref:(W5)=>{if(W5)$1.current.set(o1,W5)},session:g1,isActive:g1===q,isFocused:h5,'
        'isRenaming:k2,searchQuery:y,onClick:()=>z(g1),onMouseMove:()=>{O(o1),E(null)},'
        'onStartRename:_==="local"&&U?s:void 0,onFinishRename:K1,onCancelRename:H1,'
        'onDelete:_==="local"&&V?V:void 0,onOpenInNewWindow:_==="local"&&H?H:void 0,'
        'currentCwd:B})})))'
    )
    _OR0_row = (
        'S0.default.createElement(OR0,{key:g1.sessionId.value??o1,'
        'ref:function(W5){if(W5)$1.current.set(o1,W5)},session:g1,isActive:g1===q,isFocused:h5,'
        'isRenaming:k2,searchQuery:ccPatchSearchQ||y,onClick:function(){z(g1)},onMouseMove:function(){O(o1),E(null)},'
        'onStartRename:_==="local"&&U?s:void 0,onFinishRename:K1,onCancelRename:H1,'
        'onDelete:_==="local"&&V?V:void 0,onOpenInNewWindow:_==="local"&&H?H:void 0,'
        'currentCwd:B})'
    )
    new_sessions_list = (
        'w2.sessionsList},(function(){'
        'var ccStar=b.filter(function(s){return!ccPatchIsArchived(s)&&ccPatchIsStarred(s)}),'
        'ccPin=b.filter(function(s){return!ccPatchIsArchived(s)&&!ccPatchIsStarred(s)&&ccPatchIsPinned(s)}),'
        'ccAct=b.filter(function(s){return!ccPatchIsArchived(s)&&!ccPatchIsStarred(s)&&!ccPatchIsPinned(s)}),'
        'ccArch=b.filter(ccPatchIsArchived),'
        'items=[],idx=0;'
        # ── STARRED section ──────────────────────────────────────────────────
        'if(ccStar.length>0){'
        'items.push(S0.default.createElement("button",{key:"__star_hdr__",'
        'className:"ccPatchArchiveSectionHeader ccPatchStarSection"+(ccPatchStarState?" ccPatchArchiveSectionOpen":""),'
        'onClick:function(e){e.stopPropagation();ccPatchSetStarState(function(v){'
        'localStorage.setItem("ccPatchStarOpen",String(!v));return!v})}},'
        'S0.default.createElement("span",{className:"ccPatchArchiveLabel"},"⭐ Starred"),'
        'S0.default.createElement("span",{className:"ccPatchArchiveCount"},ccStar.length)'
        '));}'
        'ccStar.forEach(function(g1){var o1=idx++,h5=o1===w,k2=p===g1.sessionId.value;'
        'if(ccPatchStarState)items.push(' + _OR0_row + ')});'
        # ── PINNED section ───────────────────────────────────────────────────
        'if(ccPin.length>0){'
        'items.push(S0.default.createElement("button",{key:"__pin_hdr__",'
        'className:"ccPatchArchiveSectionHeader ccPatchPinSection"+(ccPatchPinState?" ccPatchArchiveSectionOpen":""),'
        'onClick:function(e){e.stopPropagation();ccPatchSetPinState(function(v){'
        'localStorage.setItem("ccPatchPinOpen",String(!v));return!v})}},'
        'S0.default.createElement("span",{className:"ccPatchArchiveLabel"},"\U0001f4cc Pinned"),'
        'S0.default.createElement("span",{className:"ccPatchArchiveCount"},ccPin.length)'
        '));}'
        'ccPin.forEach(function(g1){var o1=idx++,h5=o1===w,k2=p===g1.sessionId.value;'
        'if(ccPatchPinState)items.push(' + _OR0_row + ')});'
        # ── SESSIONS section (unpinned/unstarred/unarchived) ─────────────────
        # Always rendered so the "Sessions" header sits above the main list even
        # when no pins/stars/archives exist — matches the brother repo (v0.4.2).
        'items.push(S0.default.createElement("button",{key:"__sess_hdr__",'
        'className:"ccPatchArchiveSectionHeader ccPatchSessionsSection"+(ccPatchSessState?" ccPatchArchiveSectionOpen":""),'
        'onClick:function(e){e.stopPropagation();ccPatchSetSessState(function(v){'
        'localStorage.setItem("ccPatchSessionsOpen",String(!v));return!v})}},'
        'S0.default.createElement("span",{className:"ccPatchArchiveLabel"},"Sessions"),'
        'S0.default.createElement("span",{className:"ccPatchArchiveCount"},ccAct.length)'
        '));'
        'ccAct.forEach(function(g1){var o1=idx++,h5=o1===w,k2=p===g1.sessionId.value;'
        'if(ccPatchSessState)items.push(' + _OR0_row + ')});'
        # ── ARCHIVED section ─────────────────────────────────────────────────
        'if(ccArch.length>0){'
        'items.push(S0.default.createElement("button",{key:"__arch_hdr__",'
        'className:"ccPatchArchiveSectionHeader"+(ccPatchArchState?" ccPatchArchiveSectionOpen":""),'
        'onClick:function(e){e.stopPropagation();ccPatchSetArchState(function(v){'
        'localStorage.setItem("ccPatchArchiveOpen",String(!v));return!v})}},'
        'S0.default.createElement("span",{className:"ccPatchArchiveLabel"},"Archived"),'
        'S0.default.createElement("span",{className:"ccPatchArchiveCount"},ccArch.length)'
        '));}'
        'ccArch.forEach(function(g1){var o1=idx++,h5=o1===w,k2=p===g1.sessionId.value;'
        'if(ccPatchArchState)items.push(S0.default.createElement("div",{key:"__arch_item_"+o1,className:"ccPatchArchivedItem"},'
        + _OR0_row + '))});'
        'return items})()'
        '))'  # closes createElement("div",{sessionsList}) + outer ternary paren
    )
    if old_sessions_list in text:
        text = text.replace(old_sessions_list, new_sessions_list, 1)
        changed = True

    # ── 13. Filter subscription in header — keeps filter-button active dot reactive
    old_filter_hook = "[N,E]=p0.useState(null),y=p0.useCallback((b)=>{setTimeout(()=>{let t=gn1(b.milestoneId)"
    new_filter_hook = (
        "[N,E]=p0.useState(null),"
        "[ccPatchFTick,ccPatchFSet]=p0.useState(0),"
        "ccPatchFEffect=p0.useEffect(()=>{"
        "let ccPFL=()=>ccPatchFSet((v)=>v+1);"
        "ccPatchFilterListeners.add(ccPFL);"
        "return()=>{ccPatchFilterListeners.delete(ccPFL)}},[]),"
        "y=p0.useCallback((b)=>{setTimeout(()=>{let t=gn1(b.milestoneId)"
    )
    if old_filter_hook in text:
        text = text.replace(old_filter_hook, new_filter_hook, 1)
        changed = True

    # ── 15. Rewind fix ───────────────────────────────────────────────────────
    old_rewind = "let B=q?.filesChanged&&q.filesChanged.length>0,W=q?.canRewind&&!Q&&(B||J);"
    new_rewind = "let B=q?.filesChanged&&q.filesChanged.length>0,W=q?.canRewind&&!Q;"
    if old_rewind in text:
        text = text.replace(old_rewind, new_rewind, 1)
        changed = True

    # ── 16. YOLO mode — new sessions default to bypassPermissions when toggled ─
    # `O0` is the signal constructor used for session class fields in stock 2.1.148.
    # When YOLO is off, ccPatchYoloDefault() returns "default" — identical behavior.
    old_perm_init = 'permissionMode=O0("default")'
    new_perm_init = 'permissionMode=O0(ccPatchYoloDefault())'
    if old_perm_init in text and new_perm_init not in text:
        text = text.replace(old_perm_init, new_perm_init, 1)
        changed = True

    # ── 18. Hoist sessions panel: add wrapper classes so CSS can position it ──
    # Adds `claudePatchRoot` to the h6.root div (becomes position:relative anchor)
    # and `claudePatchHeader` to h6.header (gets padding-right to clear the panel).
    old_root_cls = '`${h6.root} ${window.IS_FULL_EDITOR?h6.editorMode:""}`'
    new_root_cls = '`${h6.root} ${window.IS_FULL_EDITOR?h6.editorMode:""} claudePatchRoot`'
    if old_root_cls in text and "claudePatchRoot" not in text:
        text = text.replace(old_root_cls, new_root_cls, 1)
        changed = True

    old_header_cls = 'createElement("div",{className:h6.header}'
    new_header_cls = 'createElement("div",{className:`${h6.header} claudePatchHeader`}'
    if old_header_cls in text and "claudePatchHeader" not in text:
        text = text.replace(old_header_cls, new_header_cls, 1)
        changed = True

    # ── 17. Top toolbar cleanup — remove the small "New session" QQ button ─────
    # The big "New Session" full-width button below the sessions panel header
    # makes this redundant. The YOLO bolt that used to sit here was also moved —
    # it now lives in the sessions panel header (see step #12, first button).
    # Anchor includes the leading comma so the Fragment children list stays valid.
    old_new_session = (
        ',p0.default.createElement(QQ,{ariaLabel:"New session",iconSize:20,'
        'onClick:()=>{if(!Z.startNewConversationTab())$.createSession()}},'
        'p0.default.createElement(nn1,null))'
    )
    if old_new_session in text:
        text = text.replace(old_new_session, "", 1)
        changed = True

    if changed:
        write(webview_js, text)
    return changed


def patch_webview_css(webview_css: Path) -> bool:
    text = read(webview_css)
    changed = False
    old = (
        ".dropdown_Wc_2Bg{position:fixed;background:var(--app-menu-background);"
        "border:1px solid var(--app-menu-border);display:flex;z-index:1000;outline:none;"
        "box-sizing:border-box;border-radius:12px;flex-direction:column;"
        "width:min(400px,100vw - 32px);max-height:min(500px,50vh);padding:6px;"
        "box-shadow:0 4px 16px #0000001a}"
    )
    new = old + (
        # Main content — shifts left by panel width so chat doesn't go under the sessions column.
        # No transition on padding-right (it caused layout reflow every frame and made the chat lag
        # during streaming). The min() against 55% caps the padding to never exceed the panel's
        # rendered max-width — without that cap, a wide --width on a narrow container could push
        # the chat off-screen left.
        ".claudePatchMainContent{position:relative;overflow:hidden;min-width:0;min-height:0;"
        "padding-right:min(var(--claude-patch-sessions-width,min(40%,340px)), 55%);"
        "box-sizing:border-box}"
        ".ccPatchPaneHidden .claudePatchMainContent{padding-right:0!important}"
        "@media(max-width:400px){.claudePatchMainContent{padding-right:0!important}}"
        # All modal overlay classes — raise above split-pane UI, opaque backdrop
        ".overlay_f3sAzg,.overlay_W2z5EA,.overlay_yumWmQ,.overlay_5FHdxw,.overlay_ukWSlw"
        "{z-index:10000!important;background-color:#000000e6!important}"
        # h6.root becomes positioning anchor for the absolutely-positioned sessions panel
        ".claudePatchRoot{position:relative}"
        # h6.header gets right-padding so its buttons clear the sessions column.
        # Same min(...,55%) clamp as main content for the same reason.
        ".claudePatchHeader{padding-right:calc(min(var(--claude-patch-sessions-width,min(40%,340px)), 55%) + 8px)!important;"
        "box-sizing:border-box}"
        # Inline sessions panel — absolutely positioned from h6.root's top edge,
        # so it visually reaches the top (above the header divider) like VS Code.
        # z-index:1 so Claude Code's own popovers (Account & Usage, etc., ~z-index:1000)
        # naturally render above the panel. Slide uses transform (compositor-friendly).
        # Defaults are container-relative so the panel scales gracefully in narrow splits.
        ".claudePatchInlineSessions{position:absolute;top:0;right:0;bottom:0;z-index:1;"
        "width:var(--claude-patch-sessions-width,min(40%,340px));"
        "min-width:140px;max-width:55%;min-height:0;"
        "border-left:1px solid var(--app-primary-border-color);"
        "display:flex;flex-direction:column;"
        "overflow:hidden;background:var(--app-primary-background);"
        "transform:translateX(0);transition:transform .2s ease;"
        "will-change:transform}"
        # Rs component fills remaining height below header
        ".claudePatchInlineSessions>div:last-child{flex:1;min-height:0;overflow:hidden}"
        # Resize handle — absolutely positioned along the left edge of the panel.
        # Uses the same clamped value as the panel's effective width.
        ".claudePatchResizeHandle{position:absolute;top:0;bottom:0;z-index:2;"
        "right:min(var(--claude-patch-sessions-width,min(40%,340px)), 55%);"
        "width:4px;cursor:col-resize;background:var(--app-primary-border-color);opacity:.5}"
        ".claudePatchResizeHandle:hover,.claudePatchResizeHandle:active{"
        "opacity:1;background:var(--vscode-sash-hoverBorder,var(--app-primary-border-color))}"
        # Pane-hidden: slide the panel off-screen with transform, snap header padding
        ".ccPatchPaneHidden .claudePatchInlineSessions"
        "{transform:translateX(100%);pointer-events:none}"
        ".ccPatchPaneHidden .claudePatchHeader{padding-right:8px!important}"
        ".ccPatchPaneHidden .claudePatchResizeHandle{display:none!important}"
        # Responsive: only auto-hide when the panel area is genuinely tiny (was 900px,
        # which collapsed in any moderately-narrow split). 400px is below normal usage.
        "@media(max-width:400px){"
        ".claudePatchInlineSessions{transform:translateX(100%);pointer-events:none}"
        ".claudePatchHeader{padding-right:8px!important}"
        ".claudePatchResizeHandle{display:none!important}}"
        # Slim Copilot-style sidebar header
        ".ccPatchSidebarHeader{flex:0 0 auto;display:flex;align-items:center;"
        "padding:2px 4px 2px 8px;border-bottom:1px solid var(--app-primary-border-color);"
        "height:35px;box-sizing:border-box}"
        ".ccPatchSidebarTitle{flex:1;font-size:11px;font-weight:600;text-transform:uppercase;"
        "letter-spacing:.06em;opacity:.45;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}"
        ".ccPatchHeaderBtn{display:inline-flex;align-items:center;justify-content:center;"
        "background:transparent;border:0;cursor:pointer;width:24px;height:24px;"
        "border-radius:4px;color:var(--app-secondary-foreground);opacity:.7;padding:0;flex-shrink:0}"
        ".ccPatchHeaderBtn:hover{background:var(--app-list-hover-background,rgba(255,255,255,.06));"
        "opacity:1;color:var(--app-primary-foreground)}"
        # "New Session" standalone full-width button below header
        ".ccPatchNewSessionBtn{display:block;width:calc(100% - 16px);margin:5px 8px;"
        "box-sizing:border-box;"
        "background:var(--app-list-hover-background,rgba(255,255,255,.06));"
        "border:0;border-radius:5px;color:var(--app-primary-foreground);cursor:pointer;"
        "font-size:12px;font-weight:500;padding:5px 0;text-align:center}"
        ".ccPatchNewSessionBtn:hover{background:var(--vscode-list-hoverBackground,rgba(255,255,255,.1))}"
        # Stacked Copilot-style session row — 48px tall (was 60px, -20%)
        ".ccPatchSessionItem{display:flex!important;align-items:center!important;"
        "min-height:48px!important;padding:0 10px!important;gap:6px!important;"
        "transition:background .12s ease!important;box-sizing:border-box!important}"
        "button.ccPatchSessionItem:hover{"
        "background:var(--app-list-hover-background,rgba(255,255,255,.06))!important}"
        ".ccPatchRowInner{flex:1;min-width:0;display:flex;flex-direction:column;gap:2px}"
        # Edit mode: fill width cleanly, hide action buttons
        ".ccPatchRowInnerEdit{flex:1;min-width:0}"
        ".ccPatchRowInnerEdit [contenteditable]{"
        "display:block;width:100%;min-width:0;outline:none;word-break:break-word}"
        ".ccPatchRowInnerEdit~.ccPatchRowActions{display:none!important}"
        ".ccPatchRowName{white-space:nowrap;overflow:hidden;text-overflow:ellipsis;line-height:1.3}"
        ".ccPatchRowTime{font-size:10.5px;opacity:.5;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}"
        # Hover-reveal action buttons panel
        ".ccPatchRowActions{display:flex;align-items:center;gap:2px;"
        "flex-shrink:0;opacity:0;transition:opacity .12s}"
        ".ccPatchSessionItem:hover .ccPatchRowActions,"
        ".ccPatchSessionItem:focus-within .ccPatchRowActions{opacity:1}"
        ".ccPatchActionBtn{display:flex!important;align-items:center;justify-content:center;"
        "background:transparent!important;border:0!important;cursor:pointer;"
        "padding:3px!important;border-radius:3px!important;"
        "color:var(--app-secondary-foreground)!important;"
        "width:22px!important;height:22px!important}"
        ".ccPatchActionBtn:hover{background:var(--app-list-hover-background)!important;"
        "color:var(--app-primary-foreground)!important}"
        # Action button SVG sizing
        ".ccPatchActionBtn svg{width:12px;height:12px;display:block}"
        ".ccPatchHeaderBtn svg{width:14px;height:14px;display:block}"
        ".ccPatchStarBtn:hover{color:var(--vscode-charts-yellow,#f59e0b)!important}"
        ".ccPatchPinBtn:hover{color:var(--vscode-charts-blue,#3b82f6)!important}"
        ".ccPatchDeleteBtn:hover{color:var(--vscode-errorForeground,#f87171)!important}"
        ".ccPatchArchiveBtn:hover{color:var(--vscode-charts-orange,#f59e0b)!important}"
        ".ccPatchUnarchiveBtn:hover{color:var(--vscode-charts-green,#10b981)!important}"
        # Search row — hidden by default, slides down when .ccPatchSearchActive on panel
        ".ccPatchSearchRow{overflow:hidden;max-height:0;transition:max-height .2s ease}"
        ".ccPatchSearchActive .ccPatchSearchRow{max-height:40px}"
        ".ccPatchSearchInput{display:block;width:calc(100% - 16px);margin:4px 8px;"
        "box-sizing:border-box;"
        "background:var(--vscode-input-background,rgba(0,0,0,.18));"
        "border:1px solid var(--vscode-input-border,transparent);"
        "border-radius:4px;color:var(--app-primary-foreground);"
        "font-size:12px;padding:4px 8px;outline:none}"
        ".ccPatchSearchInput:focus{border-color:var(--vscode-focusBorder,#007acc)}"
        ".ccPatchSearchInput::placeholder{opacity:.5}"
        # Status dot — LEFT of row, top-aligned with name text
        ".claudePatchStatus{display:block;flex:0 0 auto;width:7px;height:7px;"
        "border-radius:50%;flex-shrink:0;align-self:flex-start;margin-top:12px;"
        "background:var(--app-secondary-foreground,#888)}"
        ".claudePatchStatusDone{background:var(--vscode-charts-blue,#3b82f6)!important}"
        ".claudePatchStatusIdle{opacity:.3}"
        ".claudePatchStatusRunning{box-sizing:border-box;width:8px;height:8px;"
        "background:transparent!important;border:2px solid var(--vscode-charts-blue,#3b82f6);"
        "border-top-color:transparent;animation:claudePatchSpin .8s linear infinite}"
        ".claudePatchStatusWaiting{background:var(--vscode-charts-yellow,#f59e0b)!important;"
        "animation:claudePatchWaitPulse 1.4s ease-in-out infinite}"
        "@keyframes claudePatchSpin{to{transform:rotate(360deg)}}"
        "@keyframes claudePatchWaitPulse{0%,100%{opacity:1}50%{opacity:.4}}"
        # Context menu
        ".claudePatchContextMenu{position:fixed;z-index:2000;"
        "background:var(--app-menu-background);border:1px solid var(--app-menu-border);"
        "border-radius:6px;padding:4px;box-shadow:0 4px 16px #00000040;"
        "display:flex;flex-direction:column;min-width:130px}"
        ".claudePatchContextMenu button{background:transparent;border:0;"
        "color:var(--app-primary-foreground);text-align:left;padding:6px 10px;"
        "border-radius:4px;cursor:pointer}"
        ".claudePatchContextMenu button:hover{background:var(--app-list-hover-background)}"
        # Section headers — VS Code minimal style (starred/pinned/archived)
        ".ccPatchArchiveSectionHeader{display:flex;align-items:center;justify-content:space-between;"
        "width:100%;background:transparent;border:0;border-top:1px solid var(--app-primary-border-color);"
        "color:var(--app-secondary-foreground);cursor:pointer;padding:4px 10px 4px 8px;"
        "margin-top:4px;font-size:10.5px;font-weight:600;"
        "text-align:left;opacity:.65;user-select:none}"
        ".ccPatchArchiveSectionHeader:hover,.ccPatchArchiveSectionOpen{opacity:1}"
        ".ccPatchStarSection{border-top:0;margin-top:2px}"
        ".ccPatchPinSection{margin-top:2px}"
        ".ccPatchSessionsSection{margin-top:2px}"
        ".ccPatchArchiveLabel{flex:1}"
        ".ccPatchArchiveCount{font-size:10px;font-weight:500;opacity:.8;"
        "background:var(--app-list-hover-background);border-radius:8px;padding:0 5px;line-height:16px}"
        # Archived items — slightly dimmed like VS Code
        ".ccPatchArchivedItem{opacity:.75}"
        ".ccPatchArchivedItem:hover{opacity:1}"
        # Filter button + icon
        ".ccPatchFilterButton{position:relative}"
        ".ccPatchFilterIcon{display:inline-block;width:14px;height:10px;position:relative;opacity:.9}"
        ".ccPatchFilterIcon::before{content:'';position:absolute;left:0;right:0;top:0;height:2px;"
        "background:currentColor;border-radius:1px;box-shadow:0 4px 0 0 currentColor,0 8px 0 0 currentColor}"
        ".ccPatchFilterButtonActive .ccPatchFilterIcon::after{"
        "content:'';position:absolute;right:-3px;top:-3px;width:5px;height:5px;"
        "border-radius:50%;background:var(--vscode-charts-blue,#3b82f6);"
        "border:1.5px solid var(--app-primary-background)}"
        ".ccPatchFilterButtonOpen .ccPatchFilterIcon{opacity:1}"
        # Filter menu dropdown
        ".claudePatchFilterMenu{position:fixed;z-index:2000;"
        "background:var(--app-menu-background);border:1px solid var(--app-menu-border);"
        "border-radius:8px;padding:6px;box-shadow:0 6px 20px #00000055;"
        "display:flex;flex-direction:column;min-width:200px;max-width:280px;"
        "font-size:13px;color:var(--app-primary-foreground)}"
        ".claudePatchFilterGroup{display:flex;flex-direction:column;padding:4px 2px;"
        "border-bottom:1px solid var(--app-menu-border)}"
        ".claudePatchFilterGroup:last-of-type{border-bottom:0}"
        ".claudePatchFilterGroupTitle{font-size:11px;text-transform:uppercase;"
        "letter-spacing:.04em;opacity:.65;padding:2px 8px 4px}"
        ".claudePatchFilterOption{display:flex;align-items:center;gap:8px;"
        "padding:5px 8px;border-radius:4px;cursor:pointer;user-select:none}"
        ".claudePatchFilterOption:hover{background:var(--app-list-hover-background)}"
        ".claudePatchFilterOption input{margin:0;accent-color:var(--vscode-charts-blue,#3b82f6)}"
        ".ccPatchFilterItemIcon{flex:0 0 auto;width:12px;height:12px;opacity:.8}"
        ".ccPatchFilterItemLabel{flex:1;line-height:1.2}"
        # Hide built-in duplicate search row (we have our own in the inline header)
        ".claudePatchInlineSessions [class*=\"searchRow_\"]{display:none!important}"
        # Usage button — make $ icon a touch brighter on hover
        ".ccPatchUsageBtn:hover{color:var(--vscode-charts-green,#10b981)!important}"
        ".claudePatchFilterFooter{display:flex;justify-content:flex-end;padding:4px 2px 2px}"
        ".claudePatchFilterFooter button{background:transparent;border:0;"
        "color:var(--app-secondary-foreground);font-size:12px;padding:4px 8px;"
        "border-radius:4px;cursor:pointer}"
        ".claudePatchFilterFooter button:hover{background:var(--app-list-hover-background);"
        "color:var(--app-primary-foreground)}"
        # (toggle icon is now inline SVG — no CSS needed)
        # YOLO toggle button — now lives in the sessions header alongside other ccPatchHeaderBtn buttons
        ".ccPatchYoloBtn{display:inline-flex;align-items:center;justify-content:center;"
        "background:transparent;border:0;cursor:pointer;width:24px;height:24px;"
        "border-radius:4px;color:var(--app-secondary-foreground);opacity:.7;"
        "padding:0;flex-shrink:0;"
        "transition:background .12s,color .12s,opacity .12s}"
        ".ccPatchYoloBtn:hover{background:var(--app-list-hover-background,rgba(255,255,255,.08));"
        "opacity:1;color:var(--app-primary-foreground)}"
        ".ccPatchYoloBtn svg{width:14px;height:14px;display:block}"
        # ON state — orange-tinted background + bright orange icon
        ".ccPatchYoloMode .ccPatchYoloBtn{color:#f97316!important;opacity:1!important;"
        "background:rgba(249,115,22,.14)!important}"
        ".ccPatchYoloMode .ccPatchYoloBtn:hover{background:rgba(249,115,22,.26)!important;color:#fb923c!important}"
    )
    if old in text:
        text = text.replace(old, new, 1)
        changed = True
    if changed:
        write(webview_css, text)
    return changed


def patch_extension_dir(extension_dir: Path) -> bool:
    changed = False
    webview_js = extension_dir / "webview" / "index.js"
    webview_css = extension_dir / "webview" / "index.css"
    if not webview_js.exists() or not webview_css.exists():
        raise RuntimeError("Could not find Claude webview index.js / index.css")
    log("Patching Claude webview JS")
    changed |= patch_webview_js(webview_js)
    log("Patching Claude webview CSS")
    changed |= patch_webview_css(webview_css)
    verify_extension_dir(extension_dir)
    return changed


def verify_extension_dir(extension_dir: Path) -> None:
    js = read(extension_dir / "webview" / "index.js")
    css = read(extension_dir / "webview" / "index.css")
    checks = {
        "star helper":          "ccPatchIsStarred" in js,
        "pin helper":           "ccPatchIsPinned" in js,
        "archive helper":       "ccPatchToggleArchive" in js,
        "context menu":         "ccPatchShowMenu" in js and ".claudePatchContextMenu" in css,
        "pin sort":             "ccPatchSortSessions" in js,
        "archive sort":         "ccPatchIsArchived(e)" in js,
        "inline sessions":      "claudePatchInlineSessions" in js and ".claudePatchInlineSessions" in css,
        "main content overlay": "claudePatchMainContent" in js and ".claudePatchMainContent" in css,
        "resize handle":        "ccPatchStartResize" in js and ".claudePatchResizeHandle" in css,
        "stacked row":          "ccPatchRowInner" in js and ".ccPatchRowInner" in css,
        "row actions":          "ccPatchRowActions" in js and ".ccPatchRowActions" in css,
        "status running":       ".claudePatchStatusRunning" in css,
        "pin button":           "ccPatchPinBtn" in js and "SVG_PIN" not in js,
        "archive button":       'title:"Archive"' in js and "ccPatchArchiveBtn" in js,
        "unarchive button":     'title:"Unarchive"' in js and "ccPatchUnarchiveBtn" in js,
        "no rename button":     'title:"Rename session"' not in js,
        "new session btn":      "ccPatchNewSessionBtn" in js and ".ccPatchNewSessionBtn" in css,
        "double-click rename":  "onDoubleClick" in js,
        "edit mode wrap fix":   "ccPatchRowInnerEdit" in js and ".ccPatchRowInnerEdit" in css,
        "time format":          "days ago" in js,
        "now time":             "return`now`" in js,
        "activity text":        "ccPatchActivityText" in js,
        "archive section":      "ccPatchArchiveSectionHeader" in js and ".ccPatchArchiveSectionHeader" in css,
        "pin section":          "ccPatchPinState" in js,
        "star section":         "ccPatchStarState" in js,
        "sessions section":     "ccPatchSessState" in js and ".ccPatchSessionsSection" in css,
        "filter system":        "ccPatchFilterSort" in js and "ccPatchShowFilterMenu" in js,
        "sidebar header":       "ccPatchSidebarHeader" in js and ".ccPatchSidebarHeader" in css,
        "header btn css":       ".ccPatchHeaderBtn" in css,
        "localStorage states":  "ccPatchGetSS" in js and "ccPatchTogglePin" in js,
        "star hover button":    "ccPatchStarBtn" in js and ".ccPatchStarBtn" in css,
        "search toggle":        "ccPatchToggleSearch" in js and ".ccPatchSearchRow" in css,
        "svg actions":          "ccPatchIsPinned(Z)" in js and "polygon" in js,
        "filter button":        "ccPatchShowFilterMenu" in js and ".ccPatchFilterButton" in css,
        "filter menu css":      ".claudePatchFilterMenu" in css,
        "waiting indicator":    "ccPatchIsWaiting" in js and ".claudePatchStatusWaiting" in css,
        "pane toggle":          "ccPatchTogglePane" in js and "ccPatchHeaderBtn" in js,
        "overlay fix":          ".overlay_W2z5EA" in css,
        "row height":           "min-height:48px" in css,
        "usage button":         "ccPatchUsageBtn" in js and ".ccPatchUsageBtn" in css,
        "filter svg icons":     "ccPatchFilterIconSVG" in js and ".ccPatchFilterItemIcon" in css,
        "built-in search hide": '[class*="searchRow_"]' in css,
        "yolo helpers":         "ccPatchYoloToggle" in js and "ccPatchYoloDefault" in js,
        "yolo perm init":       "permissionMode=O0(ccPatchYoloDefault())" in js,
        "yolo button":          "ccPatchYoloBtn" in js and ".ccPatchYoloBtn" in css,
        "yolo on state css":    ".ccPatchYoloMode .ccPatchYoloBtn" in css,
        "toggle icon svg":      "ccPatchToggleIcon" in js and 'viewBox:"0 0 18 18"' in js,
        "root wrapper class":   "claudePatchRoot" in js and ".claudePatchRoot" in css,
        "header wrapper class": "claudePatchHeader" in js and ".claudePatchHeader" in css,
        "panel absolute":       "position:absolute;top:0;right:0;bottom:0" in css.replace(" ", ""),
        "build version marker": 'var ccPatchBuildVersion="' in js,
    }
    missing = [name for name, ok in checks.items() if not ok]
    if missing:
        raise RuntimeError("Verification failed: " + ", ".join(missing))
    node = shutil.which("node")
    if node:
        log("Running JS syntax check")
        subprocess.check_call([node, "--check", str(extension_dir / "webview" / "index.js")])
    log(f"Verification passed ({len(checks)} checks)")


def zip_dir(src: Path, dest: Path) -> None:
    if dest.exists():
        dest.unlink()
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in src.rglob("*"):
            if path.is_file():
                zf.write(path, path.relative_to(src).as_posix())


def main() -> int:
    global LOG_PATH, PATCHER_VERSION
    parser = argparse.ArgumentParser(description="Patch Claude Code VSIX (v2.1.147 — archive + yolo + Cursor-style UI).")
    parser.add_argument("target", nargs="?", default=DEFAULT_MARKETPLACE_ITEM)
    parser.add_argument("--out", default="")
    parser.add_argument("--version", default="")
    parser.add_argument("--download-dir", default=".")
    parser.add_argument("--log", default="claude-vsix-patch.log")
    parser.add_argument(
        "--download-only",
        action="store_true",
        help="Download the marketplace VSIX without patching. Prints STOCK_VSIX_PATH: <path>.",
    )
    parser.add_argument(
        "--patcher-version",
        default=PATCHER_VERSION,
        help="Version string embedded in the patched webview as ccPatchBuildVersion. "
             "Orbit passes its package.json version so the marker doubles as the "
             "Marketplace version.",
    )
    args = parser.parse_args()
    PATCHER_VERSION = args.patcher_version

    LOG_PATH = Path(args.log).expanduser().resolve()
    LOG_PATH.write_text("", encoding="utf-8")
    log("Starting Claude VSIX patcher (v2.1.147 — archive + yolo + Cursor UI)")

    raw = args.target
    raw_path = Path(raw).expanduser()
    if raw_path.exists() or raw_path.suffix.lower() == ".vsix":
        target = raw_path.resolve()
        if not target.exists():
            raise RuntimeError(f"VSIX file not found: {target}")
        log(f"Using local target: {target}")
    else:
        item = marketplace_item_from_target(raw)
        if item is None:
            raise RuntimeError(f"Target does not exist and is not a Marketplace item: {raw}")
        target = download_marketplace_vsix(
            item,
            Path(args.download_dir).expanduser().resolve(),
            args.version or None,
        )

    if args.download_only:
        # Emit a machine-parseable marker so the Orbit wrapper can install from file URI.
        print(f"STOCK_VSIX_PATH: {target}", flush=True)
        log(f"Download-only mode — skipping patch. Stock VSIX at: {target}")
        return 0

    builds_dir = Path(__file__).parent / "builds"
    builds_dir.mkdir(exist_ok=True)
    if args.out:
        out = Path(args.out).resolve()
    else:
        n = 1
        while (builds_dir / f"anthropic-claude-code-patch-{n}.vsix").exists():
            n += 1
        out = builds_dir / f"anthropic-claude-code-patch-{n}.vsix"
    log(f"Output VSIX: {out}")

    with tempfile.TemporaryDirectory(prefix="claude-vsix-patch-") as temp:
        root = Path(temp) / "vsix"
        log("Extracting VSIX")
        with zipfile.ZipFile(target) as zf:
            zf.extractall(root)
        changed = patch_extension_dir(root / "extension")
        log("Writing patched VSIX")
        zip_dir(root, out)

    log(f"Patched VSIX written: {out}")
    log(f"Overall status: {'updated files' if changed else 'already patched'}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        log(f"Patch run failed: {exc}")
        raise
