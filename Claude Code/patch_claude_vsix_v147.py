#!/usr/bin/env python
"""Post-build patcher for the Claude Code VS Code extension.

Adds a persistent session pane and task controls to Claude Code. The patcher
uses dynamic anchors so nearby Claude Code releases can be patched without
hard-coding minified variable names.

Features:
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
  python patch_claude_vsix_tasks.py
  python patch_claude_vsix_tasks.py anthropic.claude-code-2.1.148.vsix
  python patch_claude_vsix_tasks.py anthropic.claude-code-2.1.148.vsix --out my-patched.vsix
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import platform
import re
import shutil
import sys
import subprocess
import tempfile
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

__version__ = "0.4.3"

# Pattern fragment for a minified JS identifier (e.g. `e`, `Nee`, `_Ye`).
# The Claude webview is minified with single/short letter identifiers that
# change every release. We anchor patches on stable strings (literal aria
# labels, CSS class keys, property names like `summary.value`) and capture
# the surrounding minified identifiers via regex named groups.
JS_ID = r"[A-Za-z_$][\w$]*"

DEFAULT_MARKETPLACE_ITEM = "anthropic.claude-code"
MARKETPLACE_QUERY_URL = (
    "https://marketplace.visualstudio.com/_apis/public/gallery/" "extensionquery?api-version=7.2-preview.1"
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
        item = urllib.parse.parse_qs(urllib.parse.urlparse(target).query).get("itemName", [""])[0].strip()
        return item or None
    if "." in target and not any(sep in target for sep in ("/", "\\")) and not target.lower().endswith(".vsix"):
        return target
    return None


def detect_target_platform() -> str | None:
    system = sys.platform
    machine = platform.machine().lower()
    arch = "arm64" if machine in ("arm64", "aarch64") else "x64"
    if system.startswith("win"):
        return f"win32-{arch}"
    if system == "darwin":
        return f"darwin-{arch}"
    if system.startswith("linux"):
        return f"linux-{arch}"
    return None


def download_marketplace_vsix(
    item: str,
    dest_dir: Path,
    version: str | None = None,
    target_platform: str | None = None,
) -> Path:
    target_platform = target_platform or detect_target_platform()
    # flags MUST NOT include IncludeLatestVersionOnly (0x200). The old value 914
    # set it, so the gallery returned ONLY the newest version — meaning any
    # specific pinned version (e.g. stable's pinned Claude Code) failed with
    # "Version X not found" as soon as a newer release shipped, even though the
    # version was still fully available. 403 = IncludeVersions(0x1) |
    # IncludeFiles(0x2) | IncludeVersionProperties(0x10) | IncludeAssetUri(0x80) |
    # 0x100, which returns the full version history with downloadable files.
    body = {"filters": [{"criteria": [{"filterType": 7, "value": item}]}], "flags": 403}
    req = urllib.request.Request(
        MARKETPLACE_QUERY_URL,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", "Accept": "application/json;api-version=7.2-preview.1"},
    )
    with urllib.request.urlopen(req, timeout=60) as response:
        data = json.load(response)
    extension = data["results"][0]["extensions"][0]
    candidates = [v for v in extension["versions"] if not version or v["version"] == version]
    selected = next(
        (v for v in candidates if target_platform and v.get("targetPlatform") == target_platform),
        None,
    )
    if selected is None and target_platform:
        log(f"No {target_platform} VSIX found for {item} {version or 'latest'}; trying platform-neutral package")
    if selected is None:
        selected = next((v for v in candidates if not v.get("targetPlatform")), None)
    if selected is None and candidates:
        selected = candidates[0]
    if selected is None:
        if version:
            raise RuntimeError(f"Version {version} not found for {item}")
        selected = extension["versions"][0] if extension["versions"] else None
        if selected is None:
            raise RuntimeError(f"No versions found for {item}")
    package = next(f for f in selected["files"] if f.get("assetType", "").endswith("VSIXPackage"))
    publisher = extension["publisher"]["publisherName"]
    name = extension["extensionName"]
    dest_dir.mkdir(parents=True, exist_ok=True)
    selected_platform = selected.get("targetPlatform")
    platform_suffix = f"-{selected_platform}" if selected_platform else ""
    dest = dest_dir / f"{publisher}.{name}-{selected['version']}{platform_suffix}.vsix"
    log(f"Downloading {publisher}.{name} {selected['version']} {selected_platform or 'platform-neutral'} to {dest}")
    urllib.request.urlretrieve(package["source"], dest)
    log(f"Downloaded VSIX size: {dest.stat().st_size} bytes")
    return dest


# --------------------------------------------------------------------------- #
# Helper JS — injected once at the session-list constant block                #
# --------------------------------------------------------------------------- #
# All helper code is wrapped in an IIFE-try/catch so a single helper
# bug never prevents the rest of the Claude Code webview from loading.
CLAUDE_HELPER_JS = r"""(function(){try{"use strict";function ccPatchTitle(e){return(e.summary?.value||`Untitled`).trim()}
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
function ccPatchShowFilterMenu(e){if(document.querySelector(`.claudePatchFilterMenu`)){ccPatchCloseFilterMenu();return}let t=e.currentTarget;if(!t)return;t.classList.add(`ccPatchFilterButtonOpen`);let n=t.getBoundingClientRect(),r=document.createElement(`div`);r.className=`claudePatchFilterMenu`,r.style.top=`${Math.round(n.bottom+4)}px`,r.style.left=`${Math.min(Math.round(n.left),window.innerWidth-260)}px`;let i=ccPatchReadFilters(),s=(g,b)=>{let m=document.createElement(`div`);m.className=`claudePatchFilterGroup`;let f=document.createElement(`div`);f.className=`claudePatchFilterGroupTitle`,f.textContent=g,m.appendChild(f);for(let[v,w]of b){let A=g===`Type`?`types`:`ages`;let y=document.createElement(`div`);y.className=`claudePatchFilterOption`;if(i[A].includes(v))y.classList.add(`ccPatchFilterOn`);let ck=document.createElement(`span`);ck.className=`ccPatchFilterCheck`;ck.innerHTML='<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><path d="M5 13l4 4L19 7"/></svg>';y.appendChild(ck);let _=document.createElement(`span`);_.textContent=w;_.className=`ccPatchFilterItemLabel`;y.appendChild(_);y.onclick=()=>{let z=ccPatchReadFilters();let on=z[A].includes(v);if(on)z[A]=z[A].filter((q)=>q!==v);else z[A].push(v);ccPatchWriteFilters(z);i=z;y.classList.toggle(`ccPatchFilterOn`,!on)};m.appendChild(y)}r.appendChild(m)};s(`Type`,[[`pinned`,`Pinned`,`pinned`],[`starred`,`Starred`,`starred`],[`running`,`Running`,`running`],[`waiting`,`Waiting`,`waiting`]]);s(`Age`,[[`1h`,`Last 1 hour`,`1h`],[`24h`,`Last 24 hours`,`24h`],[`7d`,`Last 7 days`,`7d`],[`30d`,`Last 30 days`,`30d`]]);(function(){let hg=document.createElement(`div`);hg.className=`claudePatchFilterGroup`;let ht=document.createElement(`div`);ht.className=`claudePatchFilterGroupTitle`;ht.textContent=`Hide`;hg.appendChild(ht);let hl=document.createElement(`div`);hl.className=`claudePatchFilterOption`;if(i.hideUntitled)hl.classList.add(`ccPatchFilterOn`);let hck=document.createElement(`span`);hck.className=`ccPatchFilterCheck`;hck.innerHTML='<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><path d="M5 13l4 4L19 7"/></svg>';hl.appendChild(hck);let hs=document.createElement(`span`);hs.textContent=`Untitled chats`;hs.className=`ccPatchFilterItemLabel`;hl.appendChild(hs);hl.onclick=function(){let z=ccPatchReadFilters();z.hideUntitled=!z.hideUntitled;ccPatchWriteFilters(z);i=z;hl.classList.toggle(`ccPatchFilterOn`,z.hideUntitled)};hg.appendChild(hl);r.appendChild(hg)})();let o=document.createElement(`div`);o.className=`claudePatchFilterFooter`;let c=document.createElement(`button`);c.textContent=`Clear all`,c.onclick=(g)=>{g.preventDefault(),g.stopPropagation(),ccPatchWriteFilters(ccPatchDefaultFilters()),ccPatchCloseFilterMenu()},o.appendChild(c),r.appendChild(o),document.body.appendChild(r);setTimeout(()=>{let h=(g)=>{if(!r.contains(g.target)&&!t.contains(g.target))ccPatchCloseFilterMenu()};r._ccPatchOutsideHandler=h;document.addEventListener(`mousedown`,h)},0)}
function ccPatchCloseSettingsMenu(){let m=document.querySelector(`.ccPatchSettingsMenu`);if(m&&m._ccPatchOutsideHandler){document.removeEventListener(`mousedown`,m._ccPatchOutsideHandler)}m?.remove();document.querySelector(`.ccPatchSettingsBtn.ccPatchSettingsBtnOpen`)?.classList.remove(`ccPatchSettingsBtnOpen`)}
function ccPatchShowSettingsMenu(e,ctx,s){if(document.querySelector(`.ccPatchSettingsMenu`)){ccPatchCloseSettingsMenu();return}let t=e.currentTarget;if(!t)return;t.classList.add(`ccPatchSettingsBtnOpen`);let n=t.getBoundingClientRect(),r=document.createElement(`div`);r.className=`ccPatchSettingsMenu`,r.style.top=`${Math.round(n.bottom+4)}px`,r.style.left=`-9999px`;function a(l,i,o,c,k){let d=document.createElement(`div`);d.className=`ccPatchSettingsItem`;let s=document.createElement(`span`);s.className=`ccPatchSettingsItemIcon`;s.innerHTML=i;d.appendChild(s);let p=document.createElement(`span`);p.className=`ccPatchSettingsItemLabel`;p.textContent=l;d.appendChild(p);if(o){let u=document.createElement(`span`);u.className=`ccPatchSettingsItemExtra`;u.textContent=o;d.appendChild(u)}d.onmousedown=function(v){v.preventDefault();v.stopPropagation();if(!k)ccPatchCloseSettingsMenu();if(c)c()};r.appendChild(d)}(function(){let d=document.createElement(`div`);d.className=`ccPatchSettingsItem`;let ic=document.createElement(`span`);ic.className=`ccPatchSettingsItemIcon`;ic.innerHTML=`<svg width="14" height="14" viewBox="0 0 14 14" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linejoin="round" stroke-linecap="round"><polygon points="7.5,1 3.5,7.5 6.5,7.5 5,13 11,5.8 7.5,5.8"/></svg>`;d.appendChild(ic);let p=document.createElement(`span`);p.className=`ccPatchSettingsItemLabel`;p.textContent=`YOLO mode`;d.appendChild(p);let lb=document.createElement(`label`);lb.className=`ccPatchYoloToggle`;let cb=document.createElement(`input`);cb.type=`checkbox`;cb.checked=ccPatchYoloOn();let sl=document.createElement(`span`);sl.className=`ccPatchYoloSlider`;lb.appendChild(cb);lb.appendChild(sl);d.appendChild(lb);d.onmousedown=function(v){v.preventDefault();v.stopPropagation();cb.checked=!cb.checked;ccPatchYoloToggle(s)};r.appendChild(d)})();a(`Account & usage`,`<svg width="14" height="14" viewBox="0 0 14 14" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"><circle cx="7" cy="7" r="5.5"/><path d="M8.7 5.2c-.4-.5-1.1-.8-1.8-.8-1 0-1.8.55-1.8 1.3 0 .8.8 1.1 1.8 1.3 1 .25 1.8.55 1.8 1.35 0 .75-.8 1.3-1.8 1.3-.75 0-1.4-.3-1.8-.8"/><line x1="7" y1="3.4" x2="7" y2="4.4"/><line x1="7" y1="9.6" x2="7" y2="10.6"/></svg>`,null,function(){try{ctx.commandRegistry.executeCommand(`account-usage`)}catch(e){}});a(`Switch model`,`<svg width="14" height="14" viewBox="0 0 14 14" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"><rect x="4" y="4" width="6" height="6" rx="1"/><path d="M6 2v2M8 2v2M6 10v2M8 10v2M2 6h2M2 8h2M10 6h2M10 8h2"/></svg>`,null,function(){try{ctx.commandRegistry.executeCommand(`model`)}catch(e){}});a(`Switch account`,`<svg width="14" height="14" viewBox="0 0 14 14" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"><circle cx="7" cy="5" r="2.4"/><path d="M2.5 12.2c.7-2.2 2.5-3.4 4.5-3.4 1.3 0 2.4.5 3.3 1.3"/><polyline points="10,9.5 12,11.5 10,13.5"/><line x1="8.4" y1="11.5" x2="12" y2="11.5"/></svg>`,null,function(){ccPatchSwitchAccountModal(ctx)});a(`Custom instructions`,`<svg width="14" height="14" viewBox="0 0 14 14" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round"><path d="M10 1H4a1 1 0 0 0-1 1v10a1 1 0 0 0 1 1h6a1 1 0 0 0 1-1V2a1 1 0 0 0-1-1z"/><line x1="4.5" y1="4" x2="9.5" y2="4"/><line x1="4.5" y1="6.5" x2="9.5" y2="6.5"/><line x1="4.5" y1="9" x2="7.5" y2="9"/></svg>`,null,function(){ccPatchInstructionsModal(ctx)});a(`Color theme`,`<svg width="14" height="14" viewBox="0 0 14 14" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round"><path d="M7 1.3C3.8 1.3 1.3 3.7 1.3 6.9c0 2.8 2.1 4.8 4.7 4.8.8 0 1.3-.6 1.3-1.3 0-.4-.2-.6-.4-.9-.2-.2-.3-.5-.3-.8 0-.7.5-1.2 1.2-1.2h1.4c2 0 3.5-1.5 3.5-3.5C12.7 2.9 10.2 1.3 7 1.3z"/><circle cx="4" cy="6.4" r=".75" fill="currentColor" stroke="none"/><circle cx="6" cy="3.9" r=".75" fill="currentColor" stroke="none"/><circle cx="9" cy="4.3" r=".75" fill="currentColor" stroke="none"/></svg>`,null,function(){ccPatchThemeModal()});document.body.appendChild(r);var ccMW=r.offsetWidth||210,ccML=Math.round(n.right)-ccMW;if(ccML+ccMW>window.innerWidth-8)ccML=window.innerWidth-8-ccMW;if(ccML<8)ccML=8;r.style.left=`${ccML}px`;setTimeout(function(){let h=function(v){if(!r.contains(v.target)&&!t.contains(v.target))ccPatchCloseSettingsMenu()};r._ccPatchOutsideHandler=h;document.addEventListener(`mousedown`,h)},0)}
function ccPatchYoloOn(){return document.documentElement.classList.contains(`ccPatchYoloMode`)}
function ccPatchYoloDefault(){return ccPatchYoloOn()?`bypassPermissions`:`default`}
function ccPatchYoloApplyArr(arr,on){if(!arr)return;arr.forEach(function(s){try{if(s&&s.permissionMode)s.permissionMode.value=on?`bypassPermissions`:`default`}catch(e){}})}
// YOLO always starts OFF on every page load and reinstall. The previous version
// persisted state via localStorage, but the init only re-added the visual class —
// it didn't flip existing sessions' permissionMode signals, which caused a
// silent desync: button looked ON, prompts still appeared. Starting OFF means
// the visual state and functional state can only change together via the toggle.
function ccPatchYoloToggle($){var on=!ccPatchYoloOn();document.documentElement.classList.toggle(`ccPatchYoloMode`,on);if($){try{ccPatchYoloApplyArr($.sessions&&$.sessions.value,on)}catch(e){}try{ccPatchYoloApplyArr($.remoteSessions&&$.remoteSessions.value,on)}catch(e){}}}
function ccPatchRpc(ctx){try{var c=ctx&&ctx.comms&&ctx.comms.connection&&ctx.comms.connection.value;if(c&&typeof c.sendRequest==="function")return c}catch(e){}try{if(ctx&&typeof ctx.sendRequest==="function")return ctx}catch(e){}return null}
function ccPatchOpenClaudeMd(ctx,filePath,title){var r=ccPatchRpc(ctx);try{if(r){r.sendRequest({type:"open_claude_md",filePath:filePath,title:title||"CLAUDE.md"}).catch(function(e){console.error(e)});return}}catch(e){}try{if(ctx&&ctx.openFile){ctx.openFile(filePath);return}}catch(e){}}
function ccPatchReadClaudeMd(ctx,filePath,cb){var r=ccPatchRpc(ctx);try{if(r){r.sendRequest({type:"read_claude_md",filePath:filePath}).then(function(o){if(o&&typeof o.content==="string")cb(null,o.content,o.exists);else cb(null,"",false)}).catch(function(e){cb(e,"",false)})}else{cb(new Error("No ctx.sendRequest (comms.connection.value unavailable)"),"",false)}}catch(e){cb(e,"",false)}}
function ccPatchWriteClaudeMd(ctx,filePath,content,cb){var r=ccPatchRpc(ctx);try{if(r){r.sendRequest({type:"write_claude_md",filePath:filePath,content:content}).then(function(o){cb(null,o&&o.ok)}).catch(function(e){cb(e,false)})}else{cb(new Error("No ctx.sendRequest (comms.connection.value unavailable)"),false)}}catch(e){cb(e,false)}}
function ccPatchSwitchAccount(ctx,cb){var r=ccPatchRpc(ctx);try{if(r){r.sendRequest({type:"switch_account"}).then(function(o){cb(null,o&&o.ok!==false)}).catch(function(e){cb(e,false)});return}}catch(e){}cb(new Error("No ctx.sendRequest (comms.connection.value unavailable)"),false)}
function ccPatchSwitchAccountModal(ctx){try{ccPatchCloseMenu();ccPatchCloseFilterMenu();var existing=document.querySelector(".ccPatchConfirmOverlay");if(existing){existing.remove();return}var overlay=document.createElement("div");overlay.className="ccPatchConfirmOverlay";var box=document.createElement("div");box.className="ccPatchConfirmBox";var iconWrap=document.createElement("div");iconWrap.className="ccPatchConfirmIcon";iconWrap.innerHTML='<svg width="22" height="22" viewBox="0 0 22 22" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="8" r="3.6"/><path d="M3.5 19c1.1-3.5 4-5.2 7.5-5.2 2 0 3.8.6 5.2 1.8"/><polyline points="15.5,14 19,17.5 15.5,21"/><line x1="13.2" y1="17.5" x2="19" y2="17.5"/></svg>';var title=document.createElement("div");title.className="ccPatchConfirmTitle";title.textContent="Switch account?";var desc=document.createElement("div");desc.className="ccPatchConfirmDesc";desc.textContent="This will log you out of Claude Code. You will be prompted to sign in again on the next request.";var status=document.createElement("div");status.className="ccPatchConfirmStatus";var actions=document.createElement("div");actions.className="ccPatchConfirmActions";var cancelBtn=document.createElement("button");cancelBtn.className="ccPatchConfirmCancelBtn";cancelBtn.type="button";cancelBtn.textContent="Cancel";var confirmBtn=document.createElement("button");confirmBtn.className="ccPatchConfirmConfirmBtn";confirmBtn.type="button";confirmBtn.textContent="Yes, log out";function close(){overlay.remove();document.removeEventListener("keydown",onKey)}function onKey(e){if(e.key==="Escape"){e.preventDefault();close()}else if(e.key==="Enter"){e.preventDefault();confirmBtn.click()}}cancelBtn.onclick=close;confirmBtn.onclick=function(){confirmBtn.disabled=true;cancelBtn.disabled=true;status.textContent="Logging out...";ccPatchSwitchAccount(ctx,function(err,ok){if(err){status.textContent="Logout failed: "+(err.message||err);confirmBtn.disabled=false;cancelBtn.disabled=false;return}status.textContent="Logged out.";setTimeout(close,500)})};overlay.onclick=function(e){if(e.target===overlay)close()};document.addEventListener("keydown",onKey);actions.appendChild(cancelBtn);actions.appendChild(confirmBtn);box.appendChild(iconWrap);box.appendChild(title);box.appendChild(desc);box.appendChild(status);box.appendChild(actions);overlay.appendChild(box);document.body.appendChild(overlay);setTimeout(function(){confirmBtn.focus()},80)}catch(err){console.error("ccPatchSwitchAccountModal error:",err)}}
function ccPatchInstructionsModal(ctx){try{ccPatchCloseMenu();ccPatchCloseFilterMenu();var existing=document.querySelector(".ccPatchInstructionsOverlay");if(existing){existing.remove();return}var cwd=(ctx&&ctx.defaultCwd&&ctx.defaultCwd.value)||".";var projPath=cwd.replace(/\\/g,"/")+"/CLAUDE.md";var globalPath="~/.claude/CLAUDE.md";var tabs=[{id:"project",label:"Project",path:projPath,desc:"Workspace CLAUDE.md"},{id:"global",label:"Global",path:globalPath,desc:"User-wide CLAUDE.md"}];var overlay=document.createElement("div");overlay.className="ccPatchInstructionsOverlay";var box=document.createElement("div");box.className="ccPatchInstructionsBox";var closeBtn=document.createElement("button");closeBtn.className="ccPatchInstructionsClose";closeBtn.innerHTML='<svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"><line x1="3" y1="3" x2="13" y2="13"/><line x1="13" y1="3" x2="3" y2="13"/></svg>';closeBtn.onclick=function(){overlay.remove()};box.appendChild(closeBtn);var tabBar=document.createElement("div");tabBar.className="ccPatchInstructionsTabs";var contentArea=document.createElement("div");contentArea.className="ccPatchInstructionsContent";var statusEl=document.createElement("div");statusEl.className="ccPatchInstructionsStatus";var textarea=document.createElement("textarea");textarea.className="ccPatchInstructionsTextarea";textarea.placeholder="Loading...";textarea.spellcheck=false;var actions=document.createElement("div");actions.className="ccPatchInstructionsActions";var saveBtn=document.createElement("button");saveBtn.className="ccPatchInstructionsSaveBtn";saveBtn.textContent="Save";var openBtn=document.createElement("button");openBtn.className="ccPatchInstructionsOpenBtn";openBtn.textContent="Open in Editor";var statusText=document.createElement("span");statusEl.appendChild(statusText);actions.appendChild(saveBtn);actions.appendChild(openBtn);var activeTab=tabs[0];var dirty=false;function loadTab(tab){activeTab=tab;dirty=false;statusText.textContent="Loading...";textarea.value="";textarea.placeholder="Loading...";textarea.disabled=true;saveBtn.disabled=true;ccPatchReadClaudeMd(ctx,tab.path,function(err,content,exists){if(err){statusText.textContent="Error: "+err.message;textarea.placeholder="Failed to load file.";textarea.disabled=true;saveBtn.disabled=true;return}if(!exists){statusText.textContent="File does not exist yet. Start typing to create it.";textarea.placeholder="# CLAUDE.md\\n\\nAdd custom instructions for Claude here...";textarea.value="";textarea.disabled=false;saveBtn.disabled=false;dirty=false;return}statusText.textContent=exists?"File loaded ("+content.length+" chars)":"File not found";textarea.value=content;textarea.disabled=false;saveBtn.disabled=false;dirty=false});Array.from(tabBar.children).forEach(function(el){el.classList.toggle("ccPatchInstructionsTabActive",el.dataset.tabId===tab.id)})}tabs.forEach(function(tab){var btn=document.createElement("button");btn.className="ccPatchInstructionsTab";btn.dataset.tabId=tab.id;btn.textContent=tab.label;btn.title=tab.path;btn.onclick=function(){if(dirty&&!confirm("You have unsaved changes. Discard?"))return;loadTab(tab)};tabBar.appendChild(btn)});textarea.oninput=function(){dirty=true;statusText.textContent="Unsaved changes..."};saveBtn.onclick=function(){if(!activeTab)return;saveBtn.disabled=true;statusText.textContent="Saving...";ccPatchWriteClaudeMd(ctx,activeTab.path,textarea.value,function(err,ok){if(err){statusText.textContent="Save failed: "+err.message;saveBtn.disabled=false;return}statusText.textContent="Saved!";dirty=false;saveBtn.disabled=false;setTimeout(function(){if(statusText.textContent==="Saved!")statusText.textContent="File saved ("+textarea.value.length+" chars)"},2000)})};openBtn.onclick=function(){if(activeTab){ccPatchOpenClaudeMd(ctx,activeTab.path,activeTab.label);overlay.remove()}};overlay.onclick=function(e){if(e.target===overlay)overlay.remove()};contentArea.appendChild(statusEl);contentArea.appendChild(textarea);contentArea.appendChild(actions);box.appendChild(tabBar);box.appendChild(contentArea);overlay.appendChild(box);document.body.appendChild(overlay);loadTab(tabs[0]);setTimeout(function(){textarea.focus()},150)}catch(err){console.error("ccPatchInstructionsModal error:",err)}}
var ccPatchImgPreviewEl=null,ccPatchImgPreviewTimer=null;
function ccPatchImgPreviewHide(){if(ccPatchImgPreviewTimer){clearTimeout(ccPatchImgPreviewTimer);ccPatchImgPreviewTimer=null}if(ccPatchImgPreviewEl){ccPatchImgPreviewEl.remove();ccPatchImgPreviewEl=null}}
function ccPatchImgHoverPill(t){if(!t||!t.closest)return null;var pill=t.closest('[class*="pill"]');if(!pill||!pill.closest('[class*="userMessageAttachments"]'))return null;var img=pill.querySelector?pill.querySelector("img"):null;return(img&&(img.currentSrc||img.src))?pill:null}
function ccPatchImgPreviewPos(p,r){var vw=window.innerWidth,vh=window.innerHeight,m=10,pw=p.offsetWidth||300,ph=p.offsetHeight||200,left=r.left+r.width/2-pw/2;if(left<m)left=m;if(left+pw>vw-m)left=vw-m-pw;var top;if(vh-r.bottom>=ph+m||vh-r.bottom>=r.top){top=r.bottom+8;if(top+ph>vh-m)top=vh-m-ph}else{top=r.top-ph-8;if(top<m)top=m}p.style.left=Math.round(left)+"px";p.style.top=Math.round(top)+"px"}
function ccPatchImgPreviewShow(pill){if(!pill)return;var img=pill.querySelector("img"),src=img&&(img.currentSrc||img.src);if(!src)return;if(ccPatchImgPreviewEl&&ccPatchImgPreviewEl.getAttribute("data-src")===src)return;ccPatchImgPreviewHide();ccPatchImgPreviewTimer=setTimeout(function(){ccPatchImgPreviewTimer=null;try{var r=pill.getBoundingClientRect();var p=document.createElement("div");p.className="ccPatchImgPreview";p.setAttribute("data-src",src);var b=document.createElement("img");b.onload=function(){ccPatchImgPreviewPos(p,r)};b.src=src;p.appendChild(b);document.body.appendChild(p);ccPatchImgPreviewEl=p;ccPatchImgPreviewPos(p,r)}catch(e){}},500)}
document.addEventListener("mouseover",function(e){ccPatchImgPreviewShow(ccPatchImgHoverPill(e.target))},true);
document.addEventListener("mouseout",function(e){var to=e.relatedTarget;if(to&&to.closest&&to.closest('[class*="pill"]'))return;ccPatchImgPreviewHide()},true);
document.addEventListener("mousedown",function(){ccPatchImgPreviewHide()},true);
globalThis.ccPatchQueue=Array.isArray(globalThis.ccPatchQueue)?globalThis.ccPatchQueue:[];
globalThis.ccPatchQueueListeners=globalThis.ccPatchQueueListeners||new Set;
globalThis.ccPatchQueueEditId=globalThis.ccPatchQueueEditId||null;
try{var ccPSM0=localStorage.getItem(`ccPatchSendMode`);globalThis.ccPatchSendMode=(ccPSM0===`steer`||ccPSM0===`stopsend`||ccPSM0===`queue`)?ccPSM0:`queue`}catch(e){globalThis.ccPatchSendMode=`queue`}
try{var ccPQ0=JSON.parse(localStorage.getItem(`ccPatchQueue`)||`[]`);if(Array.isArray(ccPQ0))globalThis.ccPatchQueue=ccPQ0}catch(e){}
function ccPatchActiveSession(){return globalThis.ccPatchChatSession||globalThis.ccPatchComposerSession||null}
function ccPatchIsBusy(){var s=ccPatchActiveSession();try{return!!(s&&s.busy&&s.busy.value)}catch(e){return!1}}
function ccPatchSendText(t,files,sel){var s=ccPatchActiveSession();if(!s||typeof s.send!==`function`)return!1;if(!t&&!(files&&files.length))return!1;try{s.send(t||``,files||[],!!sel);return!0}catch(e){console.error(`Orbit queue send failed:`,e);return!1}}
function ccPatchQueueAddFull(t,files,sel){t=(t||``).trim();if(!t&&!(files&&files.length))return;globalThis.ccPatchQueue=(globalThis.ccPatchQueue||[]).concat([{id:`q`+Date.now()+`_`+Math.round(Math.random()*1e6),text:t,files:files||[],sel:!!sel}]);ccPatchQueueNotify()}
function ccPatchComposerInput(form){try{return(form||document).querySelector(`[contenteditable="plaintext-only"][aria-label="Message input"]`)}catch(e){return null}}
function ccPatchComposerClear(inp){if(!inp)return;try{inp.textContent=``;inp.dispatchEvent(new Event(`input`,{bubbles:!0}))}catch(e){}}
function ccPatchQueueNotify(){try{localStorage.setItem(`ccPatchQueue`,JSON.stringify((globalThis.ccPatchQueue||[]).map(function(it){return{id:it.id,text:it.text,sel:it.sel}})))}catch(e){}globalThis.ccPatchQueueListeners.forEach(function(f){try{f()}catch(err){}})}
function ccPatchSetSendMode(m){globalThis.ccPatchSendMode=m;try{localStorage.setItem(`ccPatchSendMode`,m)}catch(e){}}
function ccPatchQueueTick(){var s=ccPatchActiveSession();if(s!==globalThis.ccPatchQueueSessRef){globalThis.ccPatchQueueSessRef=s;globalThis.ccPatchQueueWasBusy=!1}var busy=!1;try{busy=!!(s&&s.busy&&s.busy.value)}catch(e){}if(busy){globalThis.ccPatchQueueWasBusy=!0;return}var q=globalThis.ccPatchQueue||[];if(!q.length||!globalThis.ccPatchQueueWasBusy)return;globalThis.ccPatchQueueWasBusy=!1;var item=q[0];globalThis.ccPatchQueue=q.slice(1);ccPatchQueueNotify();ccPatchSendText(item.text,item.files,item.sel)}
if(!globalThis.ccPatchQueueTimer)globalThis.ccPatchQueueTimer=setInterval(ccPatchQueueTick,250);
function ccPatchQueueAdd(t){t=(t||``).trim();if(!t)return;globalThis.ccPatchQueue=(globalThis.ccPatchQueue||[]).concat([{id:`q`+Date.now()+`_`+Math.round(Math.random()*1e6),text:t}]);ccPatchQueueNotify()}
function ccPatchQueueRemove(id){globalThis.ccPatchQueue=(globalThis.ccPatchQueue||[]).filter(function(x){return x.id!==id});if(globalThis.ccPatchQueueEditId===id)globalThis.ccPatchQueueEditId=null;ccPatchQueueNotify()}
function ccPatchQueueEdit(id){globalThis.ccPatchQueueEditId=id;ccPatchQueueNotify()}
function ccPatchQueueCancelEdit(){globalThis.ccPatchQueueEditId=null;ccPatchQueueNotify()}
function ccPatchQueueCommitEdit(id,t){t=(t||``).trim();globalThis.ccPatchQueueEditId=null;if(!t){ccPatchQueueRemove(id);return}globalThis.ccPatchQueue=(globalThis.ccPatchQueue||[]).map(function(x){return x.id===id?Object.assign({},x,{text:t}):x});ccPatchQueueNotify()}
function ccPatchQueueSendNow(id){var item=null;globalThis.ccPatchQueue=(globalThis.ccPatchQueue||[]).filter(function(x){if(x.id===id){item=x;return!1}return!0});ccPatchQueueNotify();if(!item)return;globalThis.ccPatchQueueWasBusy=!1;var s=ccPatchActiveSession();try{if(s&&s.interrupt)s.interrupt()}catch(e){}setTimeout(function(){ccPatchSendText(item.text,item.files,item.sel)},90)}
function ccPatchComposerAction(mode,form){form=form||ccPatchComposerForm(document.activeElement);var inp=ccPatchComposerInput(form);var text=inp?(inp.textContent||``).trim():``;if(!text)return;if(mode===`stopsend`){globalThis.ccPatchQueueIntent=!1;ccPatchStopAndSendForm(form);return}globalThis.ccPatchQueueIntent=(mode===`queue`);ccPatchTriggerSend(form);globalThis.ccPatchQueueIntent=!1}
function ccPatchRenderQueue(R,G2,sess){try{if(sess)globalThis.ccPatchChatSession=sess;var q=globalThis.ccPatchQueue||[];if(!q.length)return null;var ce=R.default.createElement,editId=globalThis.ccPatchQueueEditId;function ic(inner){return ce(`svg`,{width:13,height:13,viewBox:`0 0 13 13`,fill:`none`,stroke:`currentColor`,strokeWidth:1.4,strokeLinecap:`round`,strokeLinejoin:`round`,"aria-hidden":!0},inner)}var rows=q.map(function(it){var ctrls=ce(`div`,{className:`ccPatchQueueControls`},ce(`button`,{type:`button`,className:`ccPatchQueueCtl`,title:`Edit`,onClick:function(){ccPatchQueueEdit(it.id)}},ic(ce(`path`,{d:`M8.4 2l2.6 2.6L4.6 11H2V8.4z`}))),ce(`button`,{type:`button`,className:`ccPatchQueueCtl`,title:`Send now`,onClick:function(){ccPatchQueueSendNow(it.id)}},ic(ce(`g`,null,ce(`line`,{x1:6.5,y1:10.5,x2:6.5,y2:2.5}),ce(`polyline`,{points:`3,6 6.5,2.5 10,6`})))),ce(`button`,{type:`button`,className:`ccPatchQueueCtl`,title:`Remove`,onClick:function(){ccPatchQueueRemove(it.id)}},ic(ce(`g`,null,ce(`line`,{x1:3,y1:3,x2:10,y2:10}),ce(`line`,{x1:10,y1:3,x2:3,y2:10})))));var att=(Array.isArray(it.files)&&it.files.length)?ce(`div`,{className:`ccPatchQueueAtt`},it.files.map(function(f,fi){var nm=(f&&(f.name||(f.file&&f.file.name)||f.fileName||f.filename))||`attachment`;var src=f&&(f.url||f.dataUrl||f.dataURL||f.src||f.data);var kids=[ce(`span`,{className:`ccPatchQueueAttName`,key:`n`},nm)];if(src&&typeof src===`string`&&src.indexOf(`data:`)===0)kids.unshift(ce(`img`,{className:`ccPatchQueueAttImg`,src:src,key:`i`}));return ce(`div`,{className:`ccPatchQueueAttPill`,key:`a`+fi},kids)})):null;var body;if(editId===it.id){body=ce(`textarea`,{className:G2.userMessage+` ccPatchQueueEdit`,defaultValue:it.text,rows:1,ref:function(el){if(el)setTimeout(function(){try{el.focus();el.setSelectionRange(el.value.length,el.value.length)}catch(e){}},0)},onKeyDown:function(ev){if(ev.key===`Enter`&&!ev.shiftKey){ev.preventDefault();ccPatchQueueCommitEdit(it.id,ev.target.value)}else if(ev.key===`Escape`){ev.preventDefault();ccPatchQueueCancelEdit()}},onBlur:function(ev){ccPatchQueueCommitEdit(it.id,ev.target.value)}})}else{body=ce(`div`,{className:G2.userMessage},ce(`div`,{className:`expandableContainer_ccpatch`},it.text))}return ce(`div`,{className:G2.message+` ccPatchQueuedMsg`,key:`ccq-`+it.id},ce(`div`,{className:G2.userMessageContainer},ctrls,body,att))});return ce(`div`,{className:`ccPatchQueueWrap`,key:`ccPatchQueueWrap`},[ce(`div`,{className:`ccPatchQueueHeader`,key:`ccqh`},`QUEUED`)].concat(rows))}catch(ccRE){console.error(`Orbit queue render error:`,ccRE);return null}}
function ccPatchCloseSendMenu(){let m=document.querySelector(`.ccPatchSendMenu`);if(m&&m._ccPatchOutsideHandler){document.removeEventListener(`mousedown`,m._ccPatchOutsideHandler)}m?.remove();document.querySelector(`.ccPatchSendChevron.ccPatchSendChevronOpen`)?.classList.remove(`ccPatchSendChevronOpen`)}
function ccPatchComposerForm(node){try{return node&&node.closest?node.closest(`form`):null}catch(e){return null}}
function ccPatchTriggerSend(form){if(!form)return;try{if(form.requestSubmit)form.requestSubmit();else form.dispatchEvent(new Event(`submit`,{cancelable:!0,bubbles:!0}))}catch(e){}}
function ccPatchStopAndSendForm(form){var s=ccPatchActiveSession();try{if(s&&s.interrupt)s.interrupt()}catch(e){}setTimeout(function(){globalThis.ccPatchQueueIntent=!1;ccPatchTriggerSend(form)},80)}
function ccPatchQueueForm(form){ccPatchComposerAction(`queue`,form)}
function ccPatchSendMenu(e){if(document.querySelector(`.ccPatchSendMenu`)){ccPatchCloseSendMenu();return}let t=e.currentTarget;if(!t)return;let form=ccPatchComposerForm(t);t.classList.add(`ccPatchSendChevronOpen`);let n=t.getBoundingClientRect(),r=document.createElement(`div`);r.className=`ccPatchSendMenu`;r.style.left=`-9999px`;r.style.top=`0px`;function a(mode,l,sc,ic){var on=globalThis.ccPatchSendMode===mode;var chk=`<svg width="11" height="11" viewBox="0 0 12 12" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><polyline points="2.5,6.5 5,9 9.5,3.5"/></svg>`;var d=document.createElement(`div`);d.className=`ccPatchSendItem`;d.dataset.mode=mode;var main=document.createElement(`div`);main.className=`ccPatchSendItemMain`;var s=document.createElement(`span`);s.className=`ccPatchSendItemIcon`;s.innerHTML=ic;main.appendChild(s);var p=document.createElement(`span`);p.className=`ccPatchSendItemLabel`;p.textContent=l;main.appendChild(p);if(sc){var u=document.createElement(`span`);u.className=`ccPatchSendItemKey`;u.textContent=sc;main.appendChild(u)}main.onmousedown=function(v){v.preventDefault();v.stopPropagation();ccPatchCloseSendMenu();ccPatchComposerAction(mode,form)};d.appendChild(main);var cb=document.createElement(`button`);cb.type=`button`;cb.className=on?`ccPatchSendCheck ccPatchSendCheckOn`:`ccPatchSendCheck`;cb.title=`Set as default`;cb.innerHTML=on?chk:``;cb.onmousedown=function(v){v.preventDefault();v.stopPropagation();ccPatchSetSendMode(mode);Array.from(r.querySelectorAll(`.ccPatchSendCheck`)).forEach(function(x){var ison=!!(x.parentNode&&x.parentNode.dataset&&x.parentNode.dataset.mode===mode);x.classList.toggle(`ccPatchSendCheckOn`,ison);x.innerHTML=ison?chk:``})};d.appendChild(cb);r.appendChild(d)}a(`stopsend`,`Stop and Send`,``,`<svg width="13" height="13" viewBox="0 0 13 13" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><line x1="2.5" y1="6.5" x2="9.5" y2="6.5"/><polyline points="6.5,3.5 10,6.5 6.5,9.5"/></svg>`);a(`queue`,`Add to Queue`,`Alt+Enter`,`<svg width="13" height="13" viewBox="0 0 13 13" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><line x1="6.5" y1="2.5" x2="6.5" y2="10.5"/><line x1="2.5" y1="6.5" x2="10.5" y2="6.5"/></svg>`);a(`steer`,`Steer with Message`,`Enter`,`<svg width="13" height="13" viewBox="0 0 13 13" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><line x1="6.5" y1="10.5" x2="6.5" y2="3"/><polyline points="3.5,6 6.5,3 9.5,6"/></svg>`);document.body.appendChild(r);var mw=r.offsetWidth||212,mh=r.offsetHeight||124,ml=Math.round(n.right)-mw,mt=Math.round(n.top)-mh-6;if(ml+mw>window.innerWidth-8)ml=window.innerWidth-8-mw;if(ml<8)ml=8;if(mt<8)mt=Math.round(n.bottom)+6;r.style.left=`${ml}px`;r.style.top=`${mt}px`;setTimeout(function(){let h=function(v){if(!r.contains(v.target)&&!t.contains(v.target))ccPatchCloseSendMenu()};r._ccPatchOutsideHandler=h;document.addEventListener(`mousedown`,h)},0)}
document.addEventListener(`keydown`,function(e){if(e.key!==`Enter`||e.shiftKey||e.isComposing)return;let inp=e.target&&e.target.closest&&e.target.closest(`[contenteditable="plaintext-only"][aria-label="Message input"]`);if(!inp)return;let text=(inp.textContent||``).trim();if(!text)return;if(!ccPatchIsBusy())return;let mode=e.altKey?`queue`:(globalThis.ccPatchSendMode||`queue`);let form=inp.closest(`form`);e.preventDefault();e.stopImmediatePropagation();ccPatchComposerAction(mode,form)},!0);
var ccPatchThemes={default:{name:`Default`,sw:[`#1e1e1e`,`#c6613f`],vars:null},black:{name:`Pure Black`,sw:[`#000000`,`#c6613f`],vars:{"--vscode-sideBar-background":"#000000","--vscode-editor-background":"#050505","--vscode-panel-background":"#000000","--vscode-editorWidget-background":"#0c0c0c","--vscode-menu-background":"#0c0c0c","--vscode-input-background":"#141414","--vscode-foreground":"#e6e6e6","--vscode-descriptionForeground":"#9a9a9a","--vscode-list-hoverBackground":"#161616","--vscode-list-activeSelectionBackground":"#222222","--app-primary-border-color":"#1c1c1c","--app-input-border":"#262626"}},bright:{name:`Bright`,sw:[`#ffffff`,`#c6613f`],vars:{"--vscode-sideBar-background":"#ffffff","--vscode-editor-background":"#f7f7f7","--vscode-panel-background":"#ffffff","--vscode-editorWidget-background":"#ffffff","--vscode-menu-background":"#ffffff","--vscode-menu-foreground":"#1a1a1a","--vscode-input-background":"#ffffff","--vscode-input-foreground":"#1a1a1a","--vscode-foreground":"#1a1a1a","--vscode-descriptionForeground":"#5a5a5a","--vscode-list-hoverBackground":"#eaeaea","--vscode-list-activeSelectionBackground":"#dcdcdc","--vscode-list-activeSelectionForeground":"#1a1a1a","--app-primary-border-color":"#e2e2e2","--app-input-border":"#d4d4d4"}},neon:{name:`Neon`,sw:[`#0a0a14`,`#00e5ff`],vars:{"--vscode-sideBar-background":"#0a0a14","--vscode-editor-background":"#0d0d1a","--vscode-panel-background":"#0a0a14","--vscode-editorWidget-background":"#12122a","--vscode-menu-background":"#12122a","--vscode-input-background":"#15152e","--vscode-foreground":"#e8e8ff","--vscode-descriptionForeground":"#8a8ac0","--vscode-list-hoverBackground":"#1a1a3a","--vscode-list-activeSelectionBackground":"#26265a","--vscode-button-background":"#ff2d95","--vscode-button-foreground":"#ffffff","--app-accent-color":"#00e5ff","--app-claude-clay-button-orange":"#00e5ff","--app-primary-border-color":"#26264d","--app-input-border":"#33336a"}},midnight:{name:`Midnight`,sw:[`#0d1117`,`#58a6ff`],vars:{"--vscode-sideBar-background":"#0d1117","--vscode-editor-background":"#0d1117","--vscode-panel-background":"#0d1117","--vscode-editorWidget-background":"#161b22","--vscode-menu-background":"#161b22","--vscode-input-background":"#161b22","--vscode-foreground":"#c9d1d9","--vscode-descriptionForeground":"#8b949e","--vscode-list-hoverBackground":"#161b22","--vscode-list-activeSelectionBackground":"#1f6feb44","--app-accent-color":"#58a6ff","--app-primary-border-color":"#30363d","--app-input-border":"#30363d"}},crimson:{name:`Crimson`,sw:[`#140809`,`#ff4d5e`],vars:{"--vscode-sideBar-background":"#140809","--vscode-editor-background":"#1a0a0c","--vscode-panel-background":"#140809","--vscode-editorWidget-background":"#220e11","--vscode-menu-background":"#220e11","--vscode-input-background":"#260f12","--vscode-foreground":"#f0d8da","--vscode-descriptionForeground":"#b08a8e","--vscode-list-hoverBackground":"#2a1316","--vscode-list-activeSelectionBackground":"#3a1a1e","--app-accent-color":"#ff4d5e","--app-claude-clay-button-orange":"#ff4d5e","--app-primary-border-color":"#3a1418","--app-input-border":"#4a1a20"}},magenta:{name:`Magenta`,sw:[`#140a14`,`#ff3db5`],vars:{"--vscode-sideBar-background":"#140a14","--vscode-editor-background":"#1a0d1a","--vscode-panel-background":"#140a14","--vscode-editorWidget-background":"#20122a","--vscode-menu-background":"#20122a","--vscode-input-background":"#241324","--vscode-foreground":"#f0dcf0","--vscode-descriptionForeground":"#b08ab0","--vscode-list-hoverBackground":"#2a162a","--vscode-list-activeSelectionBackground":"#3a1f3a","--app-accent-color":"#ff3db5","--app-claude-clay-button-orange":"#ff3db5","--app-primary-border-color":"#3a1f3a","--app-input-border":"#4a284a"}},forest:{name:`Forest`,sw:[`#0b1410`,`#3ddc84`],vars:{"--vscode-sideBar-background":"#0b1410","--vscode-editor-background":"#0d1a13","--vscode-panel-background":"#0b1410","--vscode-editorWidget-background":"#122018","--vscode-menu-background":"#122018","--vscode-input-background":"#14241a","--vscode-foreground":"#d6ead8","--vscode-descriptionForeground":"#8ab094","--vscode-list-hoverBackground":"#16291d","--vscode-list-activeSelectionBackground":"#1f3a28","--app-accent-color":"#3ddc84","--app-claude-clay-button-orange":"#3ddc84","--app-primary-border-color":"#1f3a28","--app-input-border":"#284a32"}},ai:{name:`Glow`,sw:[`#0a0e1a`,`#7c5cff`],vars:{"--vscode-sideBar-background":"#0a0e1a","--vscode-editor-background":"#0c1020","--vscode-panel-background":"#0a0e1a","--vscode-editorWidget-background":"#11162a","--vscode-menu-background":"#11162a","--vscode-input-background":"#0e1326","--vscode-foreground":"#e6ebff","--vscode-descriptionForeground":"#8b94c0","--vscode-list-hoverBackground":"#141a33","--vscode-list-activeSelectionBackground":"#1c2547","--app-accent-color":"#7c5cff","--app-claude-clay-button-orange":"#7c5cff","--app-claude-orange":"#7c5cff","--app-primary-border-color":"#1f2747","--app-input-border":"#2a3358"},css:`@keyframes ccPatchAiGlow{0%{box-shadow:0 0 0 1.5px rgba(0,229,255,.55),0 0 16px rgba(0,229,255,.32)}33%{box-shadow:0 0 0 1.5px rgba(124,92,255,.6),0 0 18px rgba(124,92,255,.4)}66%{box-shadow:0 0 0 1.5px rgba(255,45,149,.55),0 0 16px rgba(255,45,149,.32)}100%{box-shadow:0 0 0 1.5px rgba(0,229,255,.55),0 0 16px rgba(0,229,255,.32)}}@keyframes ccPatchAiPulse{0%,100%{box-shadow:inset 2.5px 0 0 rgba(0,229,255,.9),0 0 10px rgba(0,229,255,.22)}50%{box-shadow:inset 2.5px 0 0 rgba(124,92,255,.95),0 0 12px rgba(124,92,255,.28)}}[class*="inputContainer_"]{border-color:transparent!important;box-shadow:none!important;position:relative;overflow:visible!important}[class*="inputContainer_"]::after{content:"";position:absolute;inset:-1px;border-radius:inherit;pointer-events:none;animation:ccPatchAiGlow 8s ease-in-out infinite}[class*="sessionItem"][class*="active"]{animation:ccPatchAiPulse 5s ease-in-out infinite;border-radius:6px}`}};
function ccPatchThemeStyleEl(){var el=document.getElementById(`ccPatchThemeStyle`);if(!el){el=document.createElement(`style`);el.id=`ccPatchThemeStyle`;(document.head||document.documentElement).appendChild(el)}return el}
function ccPatchApplyTheme(key){var t=ccPatchThemes[key]||ccPatchThemes.default;var el=ccPatchThemeStyleEl();var out=``;if(t.vars){var body=``;for(var k in t.vars){if(Object.prototype.hasOwnProperty.call(t.vars,k))body+=k+`:`+t.vars[k]+`!important;`}out=`:root{`+body+`}`}if(t.css)out+=t.css;el.textContent=out;globalThis.ccPatchThemeKey=ccPatchThemes[key]?key:`default`;try{localStorage.setItem(`ccPatchTheme`,globalThis.ccPatchThemeKey)}catch(e){}var g=document.querySelector(`.ccPatchThemeGrid`);if(g)Array.from(g.children).forEach(function(c){c.classList.toggle(`ccPatchThemeCardActive`,c.dataset.theme===globalThis.ccPatchThemeKey)})}
function ccPatchThemeModal(){try{ccPatchCloseSettingsMenu()}catch(e){}var ex=document.querySelector(`.ccPatchThemeOverlay`);if(ex){ex.remove();return}var cur=globalThis.ccPatchThemeKey||`default`;var overlay=document.createElement(`div`);overlay.className=`ccPatchConfirmOverlay ccPatchThemeOverlay`;var box=document.createElement(`div`);box.className=`ccPatchConfirmBox ccPatchThemeBox`;var title=document.createElement(`div`);title.className=`ccPatchConfirmTitle`;title.textContent=`Color theme`;var desc=document.createElement(`div`);desc.className=`ccPatchConfirmDesc`;desc.textContent=`Recolor the whole interface. Applies instantly and is remembered.`;var grid=document.createElement(`div`);grid.className=`ccPatchThemeGrid`;[`default`,`ai`,`black`,`bright`,`neon`,`midnight`,`crimson`,`magenta`,`forest`].forEach(function(key){var t=ccPatchThemes[key];if(!t)return;var card=document.createElement(`button`);card.type=`button`;card.className=key===cur?`ccPatchThemeCard ccPatchThemeCardActive`:`ccPatchThemeCard`;card.dataset.theme=key;var sw=document.createElement(`span`);sw.className=`ccPatchThemeSw`;sw.style.background=t.sw[0];var dot=document.createElement(`span`);dot.className=`ccPatchThemeDot`;dot.style.background=t.sw[1];sw.appendChild(dot);var nm=document.createElement(`span`);nm.className=`ccPatchThemeName`;nm.textContent=t.name;card.appendChild(sw);card.appendChild(nm);card.onclick=function(){ccPatchApplyTheme(key)};grid.appendChild(card)});var actions=document.createElement(`div`);actions.className=`ccPatchConfirmActions`;var done=document.createElement(`button`);done.className=`ccPatchConfirmConfirmBtn`;done.type=`button`;done.textContent=`Done`;done.onclick=function(){overlay.remove()};actions.appendChild(done);box.appendChild(title);box.appendChild(desc);box.appendChild(grid);box.appendChild(actions);overlay.appendChild(box);overlay.onclick=function(e){if(e.target===overlay)overlay.remove()};document.addEventListener(`keydown`,function ccPTK(e){if(e.key===`Escape`){overlay.remove();document.removeEventListener(`keydown`,ccPTK)}});document.body.appendChild(overlay)}
try{ccPatchApplyTheme(localStorage.getItem(`ccPatchTheme`)||`default`)}catch(e){}
Object.assign(globalThis,{ccPatchTitle,ccPatchImgPreviewShow,ccPatchImgPreviewHide,ccPatchGetSS,ccPatchSetSS,ccPatchIsArchived,ccPatchIsPinned,ccPatchIsStarred,ccPatchToggleArchive,ccPatchTogglePin,ccPatchToggleStar,ccPatchSortSessions,ccPatchSessionId,ccPatchTrackSessionStatus,ccPatchClearDone,ccPatchIsWaiting,ccPatchSessionIndicator,ccPatchActivityText,ccPatchCloseMenu,ccPatchShowMenu,ccPatchTogglePane,ccPatchSetSearch,ccPatchToggleSearch,ccPatchStartResize,ccPatchFilterListeners,ccPatchAgeMsMap,ccPatchDefaultFilters,ccPatchReadFilters,ccPatchIsUntitledEmpty,ccPatchWriteFilters,ccPatchFiltersActive,ccPatchSessionMatchesFilters,ccPatchFilterSort,ccPatchCloseFilterMenu,ccPatchFilterIconSVG,ccPatchShowFilterMenu,ccPatchCloseSettingsMenu,ccPatchShowSettingsMenu,ccPatchYoloOn,ccPatchYoloDefault,ccPatchYoloApplyArr,ccPatchYoloToggle,ccPatchRpc,ccPatchOpenClaudeMd,ccPatchReadClaudeMd,ccPatchWriteClaudeMd,ccPatchInstructionsModal,ccPatchSwitchAccount,ccPatchSwitchAccountModal,ccPatchSendMenu,ccPatchCloseSendMenu,ccPatchTriggerSend,ccPatchQueueForm,ccPatchStopAndSendForm,ccPatchComposerForm,ccPatchComposerAction,ccPatchRenderQueue,ccPatchSetSendMode,ccPatchQueueAdd,ccPatchQueueRemove,ccPatchQueueEdit,ccPatchQueueCommitEdit,ccPatchQueueCancelEdit,ccPatchQueueSendNow,ccPatchQueueTick,ccPatchActiveSession,ccPatchIsBusy,ccPatchQueueAddFull,ccPatchThemeModal,ccPatchApplyTheme});
Object.defineProperty(globalThis,"ccPatchSearchQ",{configurable:true,get:function(){return ccPatchSearchQ},set:function(v){ccPatchSearchQ=String(v||"")}});
}catch(e){console.error('Orbit patch init error:',e)}})();
"""


# ──────────────────────────────────────────────────────────────────────────
# Anchor capture
#
# Claude's webview/index.js is minified — identifiers like `S0`, `w2`, `OR0`,
# `Rs`, `fe1`, `h6`, `p0`, `NR0`, `Kk`, `Le1`, `Ne1`, `sa`, `QQ`, `in1`,
# `nn1`, `gn1` change every release. Rather than hard-coding them, we capture
# them at runtime via regex on stable strings (literal aria-labels, CSS class
# keys like `sessionItem`, property accesses like `.summary.value`,
# `.lastModifiedTime.value`, etc.).
#
# The whole feature graph clusters into three components in the bundle:
#   • Rs            — outer "sessions" list (contains the b.filter sort and
#                     the sessionsList map block).
#   • OR0           — inner session-row forwardRef (contains the rename/delete
#                     button, the worktree pill, the time formatter call).
#   • fe1           — top-level chat-tab container (contains h6.body/content
#                     and the toolbar with the history toggle button).
# We capture one regex per component and look up every minified identifier
# we need from the captured groups.
# ──────────────────────────────────────────────────────────────────────────


def _capture_rs_component(text: str) -> dict[str, str]:
    """Capture the outer sessions-list component (`function Rs(...)`).

    Returns a dict mapping logical-name → captured minified identifier:
      {fn, S0, w2, _R0, wR0, localSessions, localSessionsLoaded,
       remoteSessions, remoteConnected, remoteReconnecting,
       remoteSessionsLoaded, onReconnectRemote, activeSession,
       onSessionClick, onRenameSession, onDeleteSession, onOpenInNewWindow,
       currentCwd, authMethod, onRefresh, autoFocusSearch, isSessionListOnly,
       onOpenURL, b, _1, F, t, K1, H1, s, p, o, $1, w, O, N, E, y, _, U, V,
       H, B, q, z}
    """
    # 1. The `var w2={root:"...",sessionItem:"...",...};var _R0=N,wR0=N;`
    #    declaration just before `function Rs`. Anchor on the timing const
    #    pair (16/1000 — used to drive a spinner at 60fps over 1s) and walk
    #    back to grab the class-name object whose declaration ends right
    #    before it. Key names can appear in any order so we don't try to
    #    match them inside the object literal.
    m_timing = re.search(
        rf"\}};var\s+(?P<R0>{JS_ID})=16,(?P<R1>{JS_ID})=1000;",
        text,
    )
    if m_timing is None:
        raise RuntimeError("Could not find timing-constant pair (var _R0=16,wR0=1000)")
    # Walk back to find the matching `var <w2>={` whose closing `}` precedes
    # the `;var <R0>=16,<R1>=1000;` we just found. Scan braces inside strings
    # safely by skipping `"..."` and `'...'` literals.
    depth = 1
    i = m_timing.start() - 1  # last `}` of the object
    while i >= 0 and depth > 0:
        c = text[i]
        if c == "}":
            depth += 1
        elif c == "{":
            depth -= 1
            if depth == 0:
                break
        i -= 1
    if depth != 0 or i < 0:
        raise RuntimeError("Could not bracket the class-name object before the timing constants")
    # i is now at the `{` of the object. The `var <id>=` precedes it.
    m_w2 = re.search(
        rf"var\s+(?P<w2>{JS_ID})=$",
        text[max(0, i - 40) : i],
    )
    if m_w2 is None:
        raise RuntimeError("Could not find sessions-list class-name object + timing const declaration")

    # 2. The `function Rs({localSessions:$, ..., onOpenURL:A})` signature.
    #    Anchored on the prop-name order, which is part of the source TS
    #    interface (rename-safe across releases).
    m_rs = re.search(
        rf"function\s+(?P<fn>{JS_ID})\(\{{"
        rf"localSessions:(?P<localSessions>{JS_ID}),"
        rf"localSessionsLoaded:(?P<localSessionsLoaded>{JS_ID}),"
        rf"remoteSessions:(?P<remoteSessions>{JS_ID}),"
        rf"remoteConnected:(?P<remoteConnected>{JS_ID}),"
        rf"remoteReconnecting:(?P<remoteReconnecting>{JS_ID}),"
        rf"remoteSessionsLoaded:(?P<remoteSessionsLoaded>{JS_ID}),"
        rf"onReconnectRemote:(?P<onReconnectRemote>{JS_ID}),"
        rf"activeSession:(?P<activeSession>{JS_ID}),"
        rf"onSessionClick:(?P<onSessionClick>{JS_ID}),"
        rf"onRenameSession:(?P<onRenameSession>{JS_ID}),"
        rf"onDeleteSession:(?P<onDeleteSession>{JS_ID}),"
        rf"onOpenInNewWindow:(?P<onOpenInNewWindow>{JS_ID}),"
        rf"currentCwd:(?P<currentCwd>{JS_ID}),"
        rf"authMethod:(?P<authMethod>{JS_ID}),"
        rf"onRefresh:(?P<onRefresh>{JS_ID}),"
        rf"autoFocusSearch:(?P<autoFocusSearch>{JS_ID}),"
        rf"isSessionListOnly:(?P<isSessionListOnly>{JS_ID}),"
        rf"onOpenURL:(?P<onOpenURL>{JS_ID})"
        rf"\}}\)\{{",
        text,
    )
    if m_rs is None:
        raise RuntimeError("Could not find Rs sessions-list component signature")

    # 3. The Rs body locals up through `t=S0.useRef(F);`.
    body_start = m_rs.end()
    # Match the full prelude:
    #   z6();let P=W==="claudeai",[_,M]=S0.useState("local"),[w,O]=S0.useState(0),
    #   [N,E]=S0.useState(null),[y,x]=S0.useState(""),[p,o]=S0.useState(null),
    #   $1=S0.useRef(new Map),u=S0.useRef(null),
    #   s=S0.useCallback(...),K1=S0.useCallback(...),H1=S0.useCallback(...),
    #   _1=_==="local"?$:J,
    #   b=y?_1.filter(...):_1,
    #   t=S0.useRef(F);
    m_body = re.search(
        rf"(?P<init>{JS_ID})\(\);"
        rf"let\s+(?P<P>{JS_ID})=(?P<authMethod2>{JS_ID})===\"claudeai\","
        rf"\[(?P<tab>{JS_ID}),(?P<setTab>{JS_ID})\]=(?P<S0>{JS_ID})\.useState\(\"local\"\),"
        rf"\[(?P<w>{JS_ID}),(?P<O>{JS_ID})\]=(?P=S0)\.useState\(0\),"
        rf"\[(?P<N>{JS_ID}),(?P<E>{JS_ID})\]=(?P=S0)\.useState\(null\),"
        rf"\[(?P<y>{JS_ID}),(?P<x>{JS_ID})\]=(?P=S0)\.useState\(\"\"\),"
        rf"\[(?P<p>{JS_ID}),(?P<o>{JS_ID})\]=(?P=S0)\.useState\(null\),"
        rf"(?P<refMap>{JS_ID})=(?P=S0)\.useRef\(new Map\),"
        rf"(?P<inputRef>{JS_ID})=(?P=S0)\.useRef\(null\),"
        rf"(?P<s>{JS_ID})=(?P=S0)\.useCallback\(",
        text[body_start : body_start + 2000],
    )
    if m_body is None:
        raise RuntimeError("Could not parse Rs component body prelude")

    # 4. Capture K1, H1, _1, b, F, t from the rest of the prelude. After
    #    the `s=useCallback(...)` we have `K1=useCallback(...),H1=useCallback(...)`
    #    then `_1=<tab>==="local"?<localSessions>:<remoteSessions>,`
    #    then `b=<y>?<_1>.filter(...):<_1>,t=useRef(<F>);`. We anchor on the
    #    stable parts.
    s_var = m_body.group("s")
    S0 = m_body.group("S0")
    y_var = m_body.group("y")
    tab_var = m_body.group("tab")
    localSessions = m_rs.group("localSessions")
    remoteSessions = m_rs.group("remoteSessions")
    # Search forward from just past the `s=useCallback(` opening we already
    # matched. The first thing past that opening is the s-callback body
    # (a lambda or arrow function); after it closes we hit `,K1=useCallback(`.
    rest_start = body_start + m_body.end()
    m_rest = re.search(
        rf",(?P<K1>{JS_ID})={re.escape(S0)}\.useCallback\("
        rf".*?,(?P<H1>{JS_ID})={re.escape(S0)}\.useCallback\("
        rf".*?,(?P<_1>{JS_ID})={re.escape(tab_var)}===\"local\"\?"
        rf"{re.escape(localSessions)}:{re.escape(remoteSessions)},"
        rf"(?P<b>{JS_ID})={re.escape(y_var)}\?(?P=_1)\.filter\(",
        text[rest_start : rest_start + 4000],
        re.DOTALL,
    )
    if m_rest is None:
        raise RuntimeError("Could not parse Rs callbacks / b / _1 locals")

    # 5. The sort anchor itself: `}):<_1>,<t>=<S0>.useRef(<F>);`. This locks
    #    in the `F`, `t` identifiers and gives us a single point to splice
    #    block 2 against.
    _1 = m_rest.group("_1")
    m_sort = re.search(
        rf"\}}\):{re.escape(_1)},(?P<t>{JS_ID})={re.escape(S0)}\.useRef\((?P<F>{JS_ID})\);",
        text[body_start : body_start + 6000],
    )
    if m_sort is None:
        raise RuntimeError("Could not find Rs sort anchor `}):<_1>,t=S0.useRef(F);`")

    return {
        "fn": m_rs.group("fn"),
        "w2": m_w2.group("w2"),
        "_R0": m_timing.group("R0"),
        "wR0": m_timing.group("R1"),
        "S0": S0,
        "init": m_body.group("init"),
        "tab": tab_var,
        "w": m_body.group("w"),
        "O": m_body.group("O"),
        "N": m_body.group("N"),
        "E": m_body.group("E"),
        "y": y_var,
        "p": m_body.group("p"),
        "o": m_body.group("o"),
        "$1": m_body.group("refMap"),
        "s": s_var,
        "K1": m_rest.group("K1"),
        "H1": m_rest.group("H1"),
        "_1": _1,
        "b": m_rest.group("b"),
        "t": m_sort.group("t"),
        "F": m_sort.group("F"),
        # Rs props
        "localSessions": localSessions,
        "localSessionsLoaded": m_rs.group("localSessionsLoaded"),
        "remoteSessions": remoteSessions,
        "remoteConnected": m_rs.group("remoteConnected"),
        "remoteReconnecting": m_rs.group("remoteReconnecting"),
        "remoteSessionsLoaded": m_rs.group("remoteSessionsLoaded"),
        "onReconnectRemote": m_rs.group("onReconnectRemote"),
        "activeSession": m_rs.group("activeSession"),
        "onSessionClick": m_rs.group("onSessionClick"),
        "onRenameSession": m_rs.group("onRenameSession"),
        "onDeleteSession": m_rs.group("onDeleteSession"),
        "onOpenInNewWindow": m_rs.group("onOpenInNewWindow"),
        "currentCwd": m_rs.group("currentCwd"),
        "authMethod": m_rs.group("authMethod"),
        "onRefresh": m_rs.group("onRefresh"),
        "autoFocusSearch": m_rs.group("autoFocusSearch"),
        "isSessionListOnly": m_rs.group("isSessionListOnly"),
        "onOpenURL": m_rs.group("onOpenURL"),
        # Indices into the text for splicing block 2 (sort)
        "sort_anchor_start": body_start + m_sort.start(),
        "sort_anchor_end": body_start + m_sort.end(),
    }


def _capture_or0_component(text: str, S0: str, w2: str) -> dict[str, str]:
    """Capture the inner session-row forwardRef (`OR0`).

    Returns a dict with the row component identifier and every destructured
    param the patch references inside the row body.
    """
    # `var OR0=S0.default.forwardRef(function({session:Z,isActive:J,...},F){`
    m = re.search(
        rf"var\s+(?P<OR0>{JS_ID})={re.escape(S0)}\.default\.forwardRef\(function\(\{{"
        rf"session:(?P<session>{JS_ID}),"
        rf"isActive:(?P<isActive>{JS_ID}),"
        rf"isFocused:(?P<isFocused>{JS_ID}),"
        rf"isRenaming:(?P<isRenaming>{JS_ID}),"
        rf"searchQuery:(?P<searchQuery>{JS_ID}),"
        rf"onClick:(?P<onClick>{JS_ID}),"
        rf"onMouseMove:(?P<onMouseMove>{JS_ID}),"
        rf"onStartRename:(?P<onStartRename>{JS_ID}),"
        rf"onFinishRename:(?P<onFinishRename>{JS_ID}),"
        rf"onCancelRename:(?P<onCancelRename>{JS_ID}),"
        rf"onDelete:(?P<onDelete>{JS_ID}),"
        rf"onOpenInNewWindow:(?P<onOpenInNewWindow>{JS_ID}),"
        rf"currentCwd:(?P<currentCwd>{JS_ID})"
        rf"\}},(?P<F>{JS_ID})\)\{{",
        text,
    )
    if m is None:
        raise RuntimeError("Could not find OR0 forwardRef destructure")

    body_start = m.end()
    body_end = min(len(text), body_start + 8000)
    body = text[body_start:body_end]

    # Status hook prelude: `<init>();let <j>=<S0>.useRef(null),<D>=<S0>.useRef(!0);`
    m_hook = re.search(
        rf"(?P<init>{JS_ID})\(\);"
        rf"let\s+(?P<j>{JS_ID})={re.escape(S0)}\.useRef\(null\),"
        rf"(?P<D>{JS_ID})={re.escape(S0)}\.useRef\(!0\);",
        body,
    )
    if m_hook is None:
        raise RuntimeError("Could not find OR0 status-hook prelude")

    # Locals used in the rename text-input: `onKeyDown:A,onBlur:P`.
    m_kb = re.search(
        rf"contentEditable:!0,suppressContentEditableWarning:!0," rf"onKeyDown:(?P<A>{JS_ID}),onBlur:(?P<P>{JS_ID}),",
        body,
    )
    if m_kb is None:
        raise RuntimeError("Could not find OR0 rename-input keydown/blur params")

    # Title fn (`Kk`) and search-highlight fn (`Le1`) from
    # `},Kk(Z)):S0.default.createElement("span",{className:w2.sessionName},Le1(Kk(Z),Q))`
    session = m.group("session")
    isRenaming = m.group("isRenaming")
    searchQuery = m.group("searchQuery")
    m_titlefn = re.search(
        rf"\}},(?P<Kk>{JS_ID})\({re.escape(session)}\)\):"
        rf"{re.escape(S0)}\.default\.createElement\(\"span\","
        rf"\{{className:{re.escape(w2)}\.sessionName\}},"
        rf"(?P<Le1>{JS_ID})\((?P=Kk)\({re.escape(session)}\),{re.escape(searchQuery)}\)\),",
        body,
    )
    if m_titlefn is None:
        raise RuntimeError("Could not find OR0 title/highlight fn names")

    # Rename and delete icon components from the action buttons.
    # `title:"Rename session"},S0.default.createElement(<sa>,{className:w2.actionIcon}))`
    m_renameicon = re.search(
        rf'title:"Rename session"\}},{re.escape(S0)}\.default\.createElement\((?P<sa>{JS_ID}),'
        rf"\{{className:{re.escape(w2)}\.actionIcon\}}\)\)",
        body,
    )
    m_delicon = re.search(
        rf'title:"Delete session"\}},{re.escape(S0)}\.default\.createElement\((?P<Ne1>{JS_ID}),'
        rf"\{{className:{re.escape(w2)}\.actionIcon\}}\)",
        body,
    )
    if m_renameicon is None or m_delicon is None:
        raise RuntimeError("Could not find OR0 rename/delete icon component refs")

    # Status-var anchor `let _=<isActive>&&!<session>.summary.value&&...;return`
    isActive = m.group("isActive")
    m_status_var = re.search(
        rf"let\s+(?P<underscore>{JS_ID})={re.escape(isActive)}"
        rf"&&!{re.escape(session)}\.summary\.value"
        rf"&&!{re.escape(session)}\.messages\.value\.length"
        rf"&&!{re.escape(session)}\.teleportedMessageCount\.value;return",
        body,
    )
    if m_status_var is None:
        raise RuntimeError("Could not find OR0 status-var anchor")

    return {
        "OR0": m.group("OR0"),
        "Z": session,
        "J": m.group("isActive"),
        "Y": m.group("isFocused"),
        "X": isRenaming,
        "Q": searchQuery,
        "G": m.group("onClick"),
        "q": m.group("onMouseMove"),
        "z": m.group("onStartRename"),
        "U": m.group("onFinishRename"),
        "V": m.group("onCancelRename"),
        "H": m.group("onDelete"),
        "B": m.group("onOpenInNewWindow"),
        "W": m.group("currentCwd"),
        "F": m.group("F"),
        "j": m_hook.group("j"),
        "D": m_hook.group("D"),
        "A": m_kb.group("A"),
        "P": m_kb.group("P"),
        "Kk": m_titlefn.group("Kk"),
        "Le1": m_titlefn.group("Le1"),
        "sa": m_renameicon.group("sa"),
        "Ne1": m_delicon.group("Ne1"),
        "underscore": m_status_var.group("underscore"),
        "body_start": body_start,
    }


def _capture_nr0(text: str) -> str:
    r"""Capture the minified name of the time-format function `NR0`.

    The bundle ships `function NR0($){let J=Date.now()-$,Y=Math.floor(J/1000),
    X=Math.floor(Y/60),Q=Math.floor(X/60),G=Math.floor(Q/24),q=Math.floor(G/30),
    z=Math.floor(G/365);if(z>0)return\`${z}y\`;...}`. The function name is
    minified but the body structure is stable. We allow the local var letters
    inside the body to differ because the minifier can vary them.
    """
    m = re.search(
        rf"function\s+(?P<NR0>{JS_ID})\((?P<p>{JS_ID})\)\{{"
        rf"let\s+(?P<J>{JS_ID})=Date\.now\(\)-(?P=p),"
        rf"(?P<Y>{JS_ID})=Math\.floor\((?P=J)/1000\),"
        rf"(?P<X>{JS_ID})=Math\.floor\((?P=Y)/60\),"
        rf"(?P<Q>{JS_ID})=Math\.floor\((?P=X)/60\),"
        rf"(?P<G>{JS_ID})=Math\.floor\((?P=Q)/24\),"
        rf"(?P<q>{JS_ID})=Math\.floor\((?P=G)/30\),"
        rf"(?P<z>{JS_ID})=Math\.floor\((?P=G)/365\);"
        rf"if\((?P=z)>0\)return`\$\{{(?P=z)\}}y`;",
        text,
    )
    if m is None:
        raise RuntimeError("Could not find NR0 time-format function")
    return m.group("NR0")


def _capture_fe1_component(text: str) -> dict[str, str | int]:
    """Capture the top-level chat-tab container (`fe1`) and its key locals.

    Returns identifiers for:
      • p0           — React alias used by fe1
      • h6           — class-name object alias used by fe1
      • $, Z         — the {sessions:$,context:Z} destructure
      • QQ           — the toolbar IconButton component
      • in1          — the Session-history icon component
      • G, q         — `let[G,q]=p0.useState(!1)` toggling the history sheet
      • X            — `X=p0.useRef(null)` used as ref:X on the history button
      • Re1          — the existing recent-sessions panel component invoked
                       further down (we reuse its props to drive our inline Rs)
      • re1_call_start/end — offsets of the existing `Re1` createElement call
      • gn1, an1     — milestone helpers used in block 13 filter hook
      • body_anchor  — text offset of `createElement("div",{className:h6.body},`
    """
    # h6 declaration: a class-name object that contains all of `body`,
    # `content`, and `sessionBody` keys (in any order). We scan candidate
    # `var <id>={...};` declarations and pick the first one whose body
    # contains all three keys.
    h6 = None
    for m in re.finditer(rf"\bvar\s+(?P<id>{JS_ID})=\{{", text):
        i = m.end() - 1  # at the `{`
        depth = 0
        end_idx = -1
        k = i
        while k < len(text):
            c = text[k]
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    end_idx = k + 1
                    break
            k += 1
        if end_idx < 0:
            continue
        body = text[i:end_idx]
        if 'sessionBody:"' in body and 'body:"' in body and 'content:"' in body and 'teleportErrorBanner:"' in body:
            h6 = m.group("id")
            break
    if h6 is None:
        raise RuntimeError("Could not find h6 class-name object declaration")

    # fe1 signature: `function fe1({sessions:$,context:Z}){...let[G,q]=p0.useState(!1)...}`
    m_fe1 = re.search(
        rf"function\s+(?P<fn>{JS_ID})\(\{{sessions:(?P<S>{JS_ID}),context:(?P<C>{JS_ID})\}}\)\{{"
        rf"(?P<init>{JS_ID})\(\);"
        rf"let\s+(?P<refJ>{JS_ID})=(?P<p0>{JS_ID})\.useRef\(null\),"
        rf"(?P<refY>{JS_ID})=(?P=p0)\.useRef\(null\),"
        rf"(?P<refX>{JS_ID})=(?P=p0)\.useRef\(null\),"
        rf"(?P<refQ>{JS_ID})=(?P=p0)\.useRef\(null\),"
        rf"\[(?P<G>{JS_ID}),(?P<setG>{JS_ID})\]=(?P=p0)\.useState\(!1\),",
        text,
    )
    if m_fe1 is None:
        raise RuntimeError("Could not find fe1 chat-tab container signature")

    p0 = m_fe1.group("p0")
    fe1_start = m_fe1.start()
    fe1_end = min(len(text), m_fe1.end() + 14000)
    body = text[fe1_start:fe1_end]

    # The history toggle button:
    # `p0.default.createElement(QQ,{ref:X,ariaLabel:"Session history",iconSize:20,onClick:()=>q(!G)},p0.default.createElement(in1,null))`
    m_hist = re.search(
        rf"{re.escape(p0)}\.default\.createElement\((?P<QQ>{JS_ID}),"
        rf"\{{ref:(?P<refUsed>{JS_ID}),ariaLabel:\"Session history\",iconSize:20,"
        rf"onClick:\(\)=>(?P<setterUsed>{JS_ID})\(!(?P<stateUsed>{JS_ID})\)\}},"
        rf"{re.escape(p0)}\.default\.createElement\((?P<in1>{JS_ID}),null\)\)",
        body,
    )
    if m_hist is None:
        raise RuntimeError("Could not find session-history toggle createElement")

    # The body/content anchor in fe1.
    body_anchor_rel = body.find(f'createElement("div",{{className:{h6}.body}},')
    if body_anchor_rel < 0:
        raise RuntimeError("Could not find h6.body anchor inside fe1")
    body_anchor = fe1_start + body_anchor_rel

    # The existing Re1 recent-sessions invocation, which we mirror for our
    # inline Rs invocation in block 12.
    # `p0.default.createElement(Re1,{isOpen:G,onClose:()=>q(!1),onOpen:...,localSessions:[...$.sessions.value].sort(...),...})`
    m_re1 = re.search(
        rf"{re.escape(p0)}\.default\.createElement\((?P<Re1>{JS_ID}),"
        rf"\{{isOpen:{re.escape(m_fe1.group('G'))},"
        rf"onClose:\(\)=>{re.escape(m_hist.group('setterUsed'))}\(!1\),",
        body,
    )
    if m_re1 is None:
        raise RuntimeError("Could not find Re1 recent-sessions component invocation")

    # Walk the parenthesis depth from m_re1.start() to find the matching close.
    # m_re1 begins with `p0.default.createElement(`. Count parens from there.
    re1_open_idx = fe1_start + m_re1.start()
    re1_paren_idx = text.index("(", re1_open_idx)
    depth = 0
    re1_close_idx = -1
    i = re1_paren_idx
    while i < fe1_end:
        c = text[i]
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                re1_close_idx = i + 1
                break
        i += 1
    if re1_close_idx < 0:
        raise RuntimeError("Could not bracket the Re1 createElement call")

    re1_args = text[re1_paren_idx + 1 : re1_close_idx - 1]

    # Filter hook anchor: `[N,E]=p0.useState(null),y=p0.useCallback((b)=>{setTimeout(()=>{let t=gn1(b.milestoneId)`
    m_filter = re.search(
        rf"\[(?P<N>{JS_ID}),(?P<E>{JS_ID})\]={re.escape(p0)}\.useState\(null\),"
        rf"(?P<y>{JS_ID})={re.escape(p0)}\.useCallback\(\((?P<bp>{JS_ID})\)=>"
        rf"\{{setTimeout\(\(\)=>\{{let\s+(?P<t>{JS_ID})=(?P<gn1>{JS_ID})\((?P=bp)\.milestoneId\)",
        body,
    )
    if m_filter is None:
        raise RuntimeError("Could not find filter useCallback / gn1 anchor")
    gn1 = m_filter.group("gn1")

    return {
        "p0": p0,
        "h6": h6,
        "fn": m_fe1.group("fn"),
        "$": m_fe1.group("S"),
        "Z": m_fe1.group("C"),
        "QQ": m_hist.group("QQ"),
        "in1": m_hist.group("in1"),
        "G_state": m_hist.group("stateUsed"),
        "q_setter": m_hist.group("setterUsed"),
        "X_ref": m_hist.group("refUsed"),
        "Re1": m_re1.group("Re1"),
        "re1_call_start": re1_open_idx,
        "re1_call_end": re1_close_idx,
        "re1_args": re1_args,
        "body_anchor": body_anchor,
        "gn1": gn1,
        "filter_anchor_start": fe1_start + m_filter.start(),
        "filter_anchor_end": fe1_start + m_filter.end(),
        # Header toggle absolute offset for block 10
        "hist_call_start": fe1_start + m_hist.start(),
        "hist_call_end": fe1_start + m_hist.end(),
    }


def _re1_pick(args: str, prop: str) -> str | None:
    """Extract a top-level value for ``prop:`` from a flat React-props block.

    The Re1 invocation we mirror is one big `{k1:v1,k2:v2,...}` literal. We
    walk paren/brace/bracket depth so we can grab a value even if it contains
    commas (e.g. `[...x].sort((a,b)=>a-b)`).
    """
    idx = 0
    needle = prop + ":"
    while True:
        i = args.find(needle, idx)
        if i < 0:
            return None
        # Make sure this `prop:` is at top depth (preceded by `,` or start).
        prev = args[i - 1] if i > 0 else ","
        if prev not in (",", "{"):
            idx = i + 1
            continue
        j = i + len(needle)
        depth_paren = depth_brace = depth_brack = 0
        in_str: str | None = None
        in_tpl = False
        k = j
        while k < len(args):
            c = args[k]
            if in_str is not None:
                if c == "\\":
                    k += 2
                    continue
                if c == in_str:
                    in_str = None
                k += 1
                continue
            if in_tpl:
                if c == "\\":
                    k += 2
                    continue
                if c == "`":
                    in_tpl = False
                k += 1
                continue
            if c in "\"'":
                in_str = c
                k += 1
                continue
            if c == "`":
                in_tpl = True
                k += 1
                continue
            if c == "(":
                depth_paren += 1
            elif c == ")":
                if depth_paren == 0:
                    return args[j:k]
                depth_paren -= 1
            elif c == "{":
                depth_brace += 1
            elif c == "}":
                if depth_brace == 0:
                    return args[j:k]
                depth_brace -= 1
            elif c == "[":
                depth_brack += 1
            elif c == "]":
                if depth_brack == 0:
                    return args[j:k]
                depth_brack -= 1
            elif c == "," and depth_paren == depth_brace == depth_brack == 0:
                return args[j:k]
            k += 1
        return args[j:]


PATCHER_VERSION: str = "dev"


def patch_webview_js(webview_js: Path) -> bool:
    text = read(webview_js)

    # If the helper is already injected we treat the file as patched.
    if "function ccPatchTitle(" in text:
        return False

    rs = _capture_rs_component(text)
    or0 = _capture_or0_component(text, S0=rs["S0"], w2=rs["w2"])
    nr0 = _capture_nr0(text)
    fe1 = _capture_fe1_component(text)

    # Sanity-check that the React aliases inside fe1 and Rs are independent of
    # each other (different bundles split them into different chunks).
    if fe1["p0"] == rs["S0"]:
        # Acceptable; some bundles share the alias.
        pass

    # ──────────────────────────────────────────────────────────────────────
    # Helper aliases — these shorten the f-strings below.
    # ──────────────────────────────────────────────────────────────────────
    S0 = rs["S0"]
    w2 = rs["w2"]
    Rs_fn = rs["fn"]
    OR0 = or0["OR0"]
    p0 = fe1["p0"]
    h6 = fe1["h6"]

    # Row vars (OR0).
    Z = or0["Z"]
    J = or0["J"]
    Y = or0["Y"]
    X = or0["X"]
    Q = or0["Q"]
    G = or0["G"]
    q = or0["q"]
    z = or0["z"]
    H = or0["H"]
    B = or0["B"]
    W = or0["W"]
    F_row = or0["F"]
    j = or0["j"]
    A = or0["A"]
    P = or0["P"]
    Kk = or0["Kk"]
    Le1 = or0["Le1"]
    sa = or0["sa"]
    Ne1 = or0["Ne1"]
    underscore = or0["underscore"]

    # Outer (Rs) locals.
    b_var = rs["b"]
    _1 = rs["_1"]
    F_outer = rs["F"]
    t_outer = rs["t"]
    K1 = rs["K1"]
    H1 = rs["H1"]
    s_var = rs["s"]
    p_var = rs["p"]
    w_var = rs["w"]
    O_var = rs["O"]
    E_var = rs["E"]
    y_var = rs["y"]
    tab_var = rs["tab"]
    U_outer = rs["onRenameSession"]
    V_outer = rs["onDeleteSession"]
    H_outer = rs["onOpenInNewWindow"]
    B_outer = rs["currentCwd"]
    z_outer = rs["onSessionClick"]
    refMap = rs["$1"]

    # ──────────────────────────────────────────────────────────────────────
    # 1. Inject the helper JS + build version marker just before
    #    `var <_R0>=<n>,<wR0>=<n>;`.
    # ──────────────────────────────────────────────────────────────────────
    helper_anchor_re = re.compile(rf"var\s+{re.escape(rs['_R0'])}=\d+,{re.escape(rs['wR0'])}=\d+;")
    m_helper_anchor = helper_anchor_re.search(text)
    if m_helper_anchor is None:
        raise RuntimeError("Lost helper-injection anchor after capture")
    version_marker = f'var ccPatchBuildVersion="{PATCHER_VERSION}";'
    text = text[: m_helper_anchor.start()] + version_marker + CLAUDE_HELPER_JS + text[m_helper_anchor.start() :]

    # All captured indices into the OLD text are now invalid; we re-locate by
    # regex from this point forward.

    # ──────────────────────────────────────────────────────────────────────
    # 2. Sort block — `}):<_1>,<t>=<S0>.useRef(<F>);`
    # ──────────────────────────────────────────────────────────────────────
    sort_re = re.compile(
        rf"\}}\):{re.escape(_1)},{re.escape(t_outer)}={re.escape(S0)}\.useRef\({re.escape(F_outer)}\);"
    )
    m_sort = sort_re.search(text)
    if m_sort is None:
        raise RuntimeError("Lost sort anchor after helper injection")
    new_sort = (
        f"}}):{_1};{b_var}=ccPatchFilterSort({b_var});"
        f"let [ccPatchArchState,ccPatchSetArchState]={S0}.useState(()=>localStorage.getItem('ccPatchArchiveOpen')==='true');"
        f"let [ccPatchPinState,ccPatchSetPinState]={S0}.useState(()=>localStorage.getItem('ccPatchPinOpen')!=='false');"
        f"let [ccPatchStarState,ccPatchSetStarState]={S0}.useState(()=>localStorage.getItem('ccPatchStarOpen')!=='false');"
        f"let [ccPatchSessState,ccPatchSetSessState]={S0}.useState(()=>localStorage.getItem('ccPatchSessionsOpen')!=='false');"
        f"{{let[ccPFT,ccPFS]={S0}.useState(0);"
        f"{S0}.useEffect(()=>{{let M=()=>ccPFS((v)=>v+1);ccPatchFilterListeners.add(M);return()=>ccPatchFilterListeners.delete(M)}},[])}}"
        f"let {t_outer}={S0}.useRef({F_outer});"
    )
    text = text[: m_sort.start()] + new_sort + text[m_sort.end() :]

    # ──────────────────────────────────────────────────────────────────────
    # 3. Status hook — `<init>();let <j>=<S0>.useRef(null),<D>=<S0>.useRef(!0);`
    # ──────────────────────────────────────────────────────────────────────
    D = or0["D"]
    hook_re = re.compile(
        rf"(?P<init>{JS_ID})\(\);let\s+{re.escape(j)}={re.escape(S0)}\.useRef\(null\),"
        rf"{re.escape(D)}={re.escape(S0)}\.useRef\(!0\);"
    )
    m_hook = hook_re.search(text)
    if m_hook is None:
        raise RuntimeError("Lost OR0 status-hook anchor")
    new_hook = (
        f"{m_hook.group('init')}();"
        f"let {j}={S0}.useRef(null),{D}={S0}.useRef(!0),"
        f"[L,I]={S0}.useState(0);"
        f"{S0}.useEffect(()=>{{ccPatchTrackSessionStatus({Z},()=>I(M=>M+1))}},[{Z},{Z}.busy?.value]);"
    )
    text = text[: m_hook.start()] + new_hook + text[m_hook.end() :]

    # ──────────────────────────────────────────────────────────────────────
    # 5. Capture status indicator variable — must precede block 4 because the
    #    new block-4 button-onClick references `I` and we want our `R1` set
    #    up before that. Block 5's anchor sits just BEFORE the button start.
    # ──────────────────────────────────────────────────────────────────────
    status_var_re = re.compile(
        rf"let\s+{re.escape(underscore)}={re.escape(J)}&&!{re.escape(Z)}\.summary\.value"
        rf"&&!{re.escape(Z)}\.messages\.value\.length"
        rf"&&!{re.escape(Z)}\.teleportedMessageCount\.value;return"
    )
    m_sv = status_var_re.search(text)
    if m_sv is None:
        raise RuntimeError("Lost OR0 status-var anchor")
    new_sv = (
        f"let R1=ccPatchSessionIndicator({Z},{J}),"
        f"{underscore}={J}&&!{Z}.summary.value&&!{Z}.messages.value.length"
        f"&&!{Z}.teleportedMessageCount.value;return"
    )
    text = text[: m_sv.start()] + new_sv + text[m_sv.end() :]

    # ──────────────────────────────────────────────────────────────────────
    # 4. Right-click context menu — patches the row <button> opening.
    # ──────────────────────────────────────────────────────────────────────
    btn_re = re.compile(
        rf'return {re.escape(S0)}\.default\.createElement\("button",'
        rf"\{{ref:{re.escape(F_row)},className:`\$\{{{re.escape(w2)}\.sessionItem\}} "
        rf'\$\{{{re.escape(J)}\?{re.escape(w2)}\.active:""\}} '
        rf'\$\{{{re.escape(Y)}\?{re.escape(w2)}\.focused:""\}}`,'
        rf"onClick:{re.escape(X)}\?void 0:{re.escape(G)},onMouseMove:{re.escape(q)}\}},"
    )
    m_btn = btn_re.search(text)
    if m_btn is None:
        raise RuntimeError("Lost OR0 row-button anchor")
    new_btn = (
        f'return {S0}.default.createElement("button",'
        f"{{ref:{F_row},className:`${{{w2}.sessionItem}} ccPatchSessionItem "
        f'${{{J}?{w2}.active:""}} ${{{Y}?{w2}.focused:""}}`,'
        f"onClick:{X}?void 0:(M)=>{{ccPatchClearDone({Z}),I(W5=>W5+1),{G}(M)}},onMouseMove:{q},"
        f"onContextMenu:(M)=>{{if({z}&&{Z}.sessionId.value)M.preventDefault(),M.stopPropagation(),"
        f"ccPatchShowMenu(M.clientX,M.clientY,"
        f"ccPatchIsPinned({Z})?`Unpin`:`Pin`,()=>ccPatchTogglePin({Z}),"
        f"ccPatchIsStarred({Z})?`Unstar`:`Star`,()=>ccPatchToggleStar({Z}),"
        f"ccPatchIsArchived({Z})?{{label:`Unarchive`,fn:()=>ccPatchToggleArchive({Z})}}:{{label:`Archive`,fn:()=>ccPatchToggleArchive({Z})}})}}}},"
    )
    text = text[: m_btn.start()] + new_btn + text[m_btn.end() :]

    # ──────────────────────────────────────────────────────────────────────
    # 6. Status dot + stacked name/time row.
    # ──────────────────────────────────────────────────────────────────────
    si_re = re.compile(
        rf'{re.escape(X)}\?{re.escape(S0)}\.default\.createElement\("span",'
        rf"\{{ref:{re.escape(j)},className:`\$\{{{re.escape(w2)}\.sessionName\}} "
        rf"\$\{{{re.escape(w2)}\.sessionNameEditing\}}`,"
        rf"contentEditable:!0,suppressContentEditableWarning:!0,"
        rf"onKeyDown:{re.escape(A)},onBlur:{re.escape(P)},"
        rf"onClick:\(M\)=>M\.stopPropagation\(\)\}},{re.escape(Kk)}\({re.escape(Z)}\)\):"
        rf'{re.escape(S0)}\.default\.createElement\("span",\{{className:{re.escape(w2)}\.sessionName\}},'
        rf"{re.escape(Le1)}\({re.escape(Kk)}\({re.escape(Z)}\),{re.escape(Q)}\)\),"
        rf"{re.escape(B)}&&{re.escape(Z)}\.worktree\.value&&{re.escape(Z)}\.worktree\.value\.path!=={re.escape(W)}&&"
    )
    m_si = si_re.search(text)
    if m_si is None:
        raise RuntimeError("Lost OR0 status-insert anchor")
    new_si = (
        f'{S0}.default.createElement("span",{{className:"claudePatchStatus "'
        f'+(R1==="running"?"claudePatchStatusRunning":R1==="waiting"?"claudePatchStatusWaiting":R1==="done"?"claudePatchStatusDone":"claudePatchStatusIdle"),'
        f'title:R1==="running"?"Running":R1==="waiting"?"Waiting for input":R1==="done"?"Completed":""}}),'
        f"{X}?"
        f'{S0}.default.createElement("div",{{className:"ccPatchRowInner ccPatchRowInnerEdit"}},'
        f'{S0}.default.createElement("span",{{ref:{j},className:`${{{w2}.sessionName}} ${{{w2}.sessionNameEditing}}`,'
        f"contentEditable:!0,suppressContentEditableWarning:!0,onKeyDown:{A},onBlur:{P},"
        f"onClick:(M)=>M.stopPropagation()}},{Kk}({Z})))"
        f":"
        f'{S0}.default.createElement("div",{{className:"ccPatchRowInner"}},'
        f'{S0}.default.createElement("span",{{className:{w2}.sessionName+" ccPatchRowName",'
        f"onDoubleClick:(M)=>{{M.stopPropagation(),{z}&&{z}({Z})}}}},{Le1}({Kk}({Z}),{Q})),"
        f'{S0}.default.createElement("span",{{className:"ccPatchRowTime"}},'
        f'R1==="running"||R1==="waiting"?ccPatchActivityText({Z})||{nr0}({Z}.lastModifiedTime.value):{nr0}({Z}.lastModifiedTime.value))'
        f"),"
        f"{B}&&{Z}.worktree.value&&{Z}.worktree.value.path!=={W}&&"
    )
    text = text[: m_si.start()] + new_si + text[m_si.end() :]

    # ──────────────────────────────────────────────────────────────────────
    # 7. Replace the entire sessionMeta hover-actions block.
    # ──────────────────────────────────────────────────────────────────────
    # Inline SVG icons (dynamic on S0 + Z).
    icon_pin = (
        f'{S0}.default.createElement("svg",{{width:12,height:12,viewBox:"0 0 12 12","aria-hidden":true}},'
        f'{S0}.default.createElement("circle",{{cx:6,cy:4.5,r:2.5,'
        f'fill:ccPatchIsPinned({Z})?"currentColor":"none",'
        f'stroke:"currentColor",strokeWidth:1.5}}),'
        f'{S0}.default.createElement("line",{{x1:6,y1:7,x2:6,y2:11.5,'
        f'stroke:"currentColor",strokeWidth:1.5}}))'
    )
    icon_star = (
        f'{S0}.default.createElement("svg",{{width:12,height:12,viewBox:"0 0 12 12","aria-hidden":true}},'
        f'{S0}.default.createElement("polygon",{{points:"6,1.5 7.2,4.5 10.5,4.9 8.2,7.1 8.8,10.5 6,9 3.2,10.5 3.8,7.1 1.5,4.9 4.8,4.5",'
        f'fill:ccPatchIsStarred({Z})?"currentColor":"none",'
        f'stroke:"currentColor",strokeWidth:1.2,strokeLinejoin:"round"}}))'
    )
    icon_archive = (
        f'{S0}.default.createElement("svg",{{width:12,height:12,viewBox:"0 0 12 12",fill:"none",'
        f'stroke:"currentColor",strokeWidth:1.2,strokeLinecap:"round","aria-hidden":true}},'
        f'{S0}.default.createElement("rect",{{x:1,y:2,width:10,height:2.5,rx:0.5}}),'
        f'{S0}.default.createElement("path",{{d:"M2 4.5v5h8v-5"}}),'
        f'{S0}.default.createElement("line",{{x1:6,y1:6,x2:6,y2:8.5}}),'
        f'{S0}.default.createElement("polyline",{{points:"4.5,7 6,8.5 7.5,7"}}))'
    )
    icon_unarchive = (
        f'{S0}.default.createElement("svg",{{width:12,height:12,viewBox:"0 0 12 12",fill:"none",'
        f'stroke:"currentColor",strokeWidth:1.2,strokeLinecap:"round","aria-hidden":true}},'
        f'{S0}.default.createElement("rect",{{x:1,y:2,width:10,height:2.5,rx:0.5}}),'
        f'{S0}.default.createElement("path",{{d:"M2 4.5v5h8v-5"}}),'
        f'{S0}.default.createElement("line",{{x1:6,y1:8.5,x2:6,y2:6}}),'
        f'{S0}.default.createElement("polyline",{{points:"4.5,7.5 6,6 7.5,7.5"}}))'
    )
    icon_trash = (
        f'{S0}.default.createElement("svg",{{width:12,height:12,viewBox:"0 0 12 12",fill:"none",'
        f'stroke:"currentColor",strokeWidth:1.2,strokeLinecap:"round","aria-hidden":true}},'
        f'{S0}.default.createElement("line",{{x1:1.5,y1:3,x2:10.5,y2:3}}),'
        f'{S0}.default.createElement("path",{{d:"M3 3l.8 8h4.4l.8-8"}}),'
        f'{S0}.default.createElement("path",{{d:"M4.5 3V2h3v1"}}))'
    )
    meta_re = re.compile(
        rf',{re.escape(S0)}\.default\.createElement\("span",\{{className:{re.escape(w2)}\.sessionMeta\}},'
        rf'{re.escape(S0)}\.default\.createElement\("span",\{{className:{re.escape(w2)}\.sessionTime\}},'
        rf"{re.escape(nr0)}\({re.escape(Z)}\.lastModifiedTime\.value\)\),"
        rf"!{re.escape(X)}&&!{re.escape(underscore)}&&\({re.escape(z)}\|\|{re.escape(H)}\)"
        rf'&&{re.escape(S0)}\.default\.createElement\("span",\{{className:{re.escape(w2)}\.sessionActions\}},'
        rf"{re.escape(z)}&&{re.escape(Z)}\.sessionId\.value"
        rf'&&{re.escape(S0)}\.default\.createElement\("span",\{{role:"button",tabIndex:0,'
        rf"className:{re.escape(w2)}\.actionButton,"
        rf"onClick:\(M\)=>\{{M\.stopPropagation\(\),{re.escape(z)}\({re.escape(Z)}\)\}},"
        rf'onKeyDown:\(M\)=>\{{if\(M\.key==="Enter"\|\|M\.key===" "\)M\.preventDefault\(\),M\.stopPropagation\(\),{re.escape(z)}\({re.escape(Z)}\)\}},'
        rf'title:"Rename session"\}},'
        rf"{re.escape(S0)}\.default\.createElement\({re.escape(sa)},\{{className:{re.escape(w2)}\.actionIcon\}}\)\),"
        rf'{re.escape(H)}&&{re.escape(S0)}\.default\.createElement\("span",\{{role:"button",tabIndex:0,'
        rf"className:`\$\{{{re.escape(w2)}\.actionButton\}} \$\{{{re.escape(w2)}\.deleteButton\}}`,"
        rf"onClick:\(M\)=>\{{M\.stopPropagation\(\),{re.escape(H)}\({re.escape(Z)}\)\}},"
        rf'onKeyDown:\(M\)=>\{{if\(M\.key==="Enter"\|\|M\.key===" "\)M\.preventDefault\(\),M\.stopPropagation\(\),{re.escape(H)}\({re.escape(Z)}\)\}},'
        rf'title:"Delete session"\}},'
        rf"{re.escape(S0)}\.default\.createElement\({re.escape(Ne1)},\{{className:{re.escape(w2)}\.actionIcon\}}\)"
        rf"\)\)\)\)\}}\);"
    )
    m_meta = meta_re.search(text)
    if m_meta is None:
        raise RuntimeError("Lost OR0 sessionMeta hover-actions anchor")
    new_meta = (
        f',{S0}.default.createElement("div",{{className:"ccPatchRowActions"}},'
        f'{S0}.default.createElement("span",{{role:"button",tabIndex:0,'
        f"className:`${{{w2}.actionButton}} ccPatchActionBtn ccPatchStarBtn`,"
        f"onClick:(M)=>{{M.stopPropagation(),ccPatchToggleStar({Z})}},"
        f'title:ccPatchIsStarred({Z})?"Unstar":"Star"}},' + icon_star + "),"
        f'{S0}.default.createElement("span",{{role:"button",tabIndex:0,'
        f"className:`${{{w2}.actionButton}} ccPatchActionBtn ccPatchPinBtn`,"
        f"onClick:(M)=>{{M.stopPropagation(),ccPatchTogglePin({Z})}},"
        f'title:ccPatchIsPinned({Z})?"Unpin":"Pin"}},' + icon_pin + "),"
        # Unarchive button — only on archived sessions, sits left of delete
        f"ccPatchIsArchived({Z})&&{S0}.default.createElement(" + '"span"'
        f",{{role:" + '"button"' + ",tabIndex:0,"
        f"className:`${{{w2}.actionButton}} ccPatchActionBtn ccPatchUnarchiveBtn`,"
        f"onClick:(M)=>{{M.stopPropagation(),ccPatchToggleArchive({Z})}},"
        f'title:"Unarchive"}},' + icon_unarchive + "),"
        f"(ccPatchIsArchived({Z})"
        f'?{H}&&{S0}.default.createElement("span",{{role:"button",tabIndex:0,'
        f"className:`${{{w2}.actionButton}} ccPatchActionBtn ccPatchDeleteBtn`,"
        f"onClick:(M)=>{{M.stopPropagation(),{H}({Z})}},"
        f'title:"Delete permanently"}},' + icon_trash + ")"
        f':{S0}.default.createElement("span",{{role:"button",tabIndex:0,'
        f"className:`${{{w2}.actionButton}} ccPatchActionBtn ccPatchArchiveBtn`,"
        f"onClick:(M)=>{{M.stopPropagation(),ccPatchToggleArchive({Z})}},"
        f'title:"Archive"}},' + icon_archive + "))"
        ")"
        ")});"
    )
    text = text[: m_meta.start()] + new_meta + text[m_meta.end() :]

    # ──────────────────────────────────────────────────────────────────────
    # 8. Rewrite the time formatter. We don't pin local var letters because
    #    they may differ across builds — instead capture them in the same
    #    regex and reuse them.
    # ──────────────────────────────────────────────────────────────────────
    nro_re = re.compile(
        rf"function\s+{re.escape(nr0)}\((?P<p>{JS_ID})\)\{{"
        rf"let\s+(?P<J>{JS_ID})=Date\.now\(\)-(?P=p),"
        rf"(?P<Y>{JS_ID})=Math\.floor\((?P=J)/1000\),"
        rf"(?P<X>{JS_ID})=Math\.floor\((?P=Y)/60\),"
        rf"(?P<Q>{JS_ID})=Math\.floor\((?P=X)/60\),"
        rf"(?P<G>{JS_ID})=Math\.floor\((?P=Q)/24\),"
        rf"(?P<q>{JS_ID})=Math\.floor\((?P=G)/30\),"
        rf"(?P<z>{JS_ID})=Math\.floor\((?P=G)/365\);"
        rf"if\((?P=z)>0\)return`\$\{{(?P=z)\}}y`;"
        rf"if\((?P=q)>0\)return`\$\{{(?P=q)\}}mo`;"
        rf"if\((?P=G)>0\)return`\$\{{(?P=G)\}}d`;"
        rf"if\((?P=Q)>0\)return`\$\{{(?P=Q)\}}h`;"
        rf"if\((?P=X)>0\)return`\$\{{(?P=X)\}}m`;"
        rf'return"now"\}}'
    )
    m_nro = nro_re.search(text)
    if m_nro is None:
        raise RuntimeError("Lost NR0 time-formatter anchor")
    p_var_nro = m_nro.group("p")
    Jn = m_nro.group("J")
    Yn = m_nro.group("Y")
    Xn = m_nro.group("X")
    Qn = m_nro.group("Q")
    Gn = m_nro.group("G")
    qn = m_nro.group("q")
    zn = m_nro.group("z")
    new_nro = (
        f"function {nr0}({p_var_nro}){{let {Jn}=Date.now()-{p_var_nro},"
        f"{Yn}=Math.floor({Jn}/1000),{Xn}=Math.floor({Yn}/60),"
        f"{Qn}=Math.floor({Xn}/60),{Gn}=Math.floor({Qn}/24),"
        f"{qn}=Math.floor({Gn}/30),{zn}=Math.floor({Gn}/365);"
        f"if({zn}>0)return {zn}===1?`1 yr ago`:`${{{zn}}} yrs ago`;"
        f"if({qn}>0)return {qn}===1?`1 mo ago`:`${{{qn}}} mo ago`;"
        f"if({Gn}>0)return {Gn}===1?`1 day ago`:`${{{Gn}}} days ago`;"
        f"if({Qn}>0)return {Qn}===1?`1 hr ago`:`${{{Qn}}} hrs ago`;"
        f"if({Xn}>0)return {Xn}===1?`1 min ago`:`${{{Xn}}} min ago`;"
        f"return`now`}}"
    )
    text = text[: m_nro.start()] + new_nro + text[m_nro.end() :]

    # ──────────────────────────────────────────────────────────────────────
    # 10. Pane-toggle button — inject before the Session-history button.
    # ──────────────────────────────────────────────────────────────────────
    QQ = fe1["QQ"]
    in1 = fe1["in1"]
    X_ref = fe1["X_ref"]
    q_setter = fe1["q_setter"]
    G_state = fe1["G_state"]
    hist_re = re.compile(
        rf"{re.escape(p0)}\.default\.createElement\({re.escape(QQ)},"
        rf'\{{ref:{re.escape(X_ref)},ariaLabel:"Session history",iconSize:20,'
        rf"onClick:\(\)=>{re.escape(q_setter)}\(!{re.escape(G_state)}\)\}},"
        rf"{re.escape(p0)}\.default\.createElement\({re.escape(in1)},null\)\)"
    )
    m_hist = hist_re.search(text)
    if m_hist is None:
        raise RuntimeError("Lost Session-history toggle anchor")
    pane_toggle = (
        f'{p0}.default.createElement({QQ},{{ariaLabel:"Toggle session pane",iconSize:20,'
        f"onClick:ccPatchTogglePane}},"
        f'{p0}.default.createElement("svg",{{width:20,height:20,viewBox:"0 0 20 20",fill:"none",'
        f'stroke:"currentColor",strokeWidth:1.6,strokeLinecap:"round",strokeLinejoin:"round","aria-hidden":true}},'
        f'{p0}.default.createElement("rect",{{x:3,y:4,width:14,height:12,rx:1.5}}),'
        f'{p0}.default.createElement("line",{{x1:8,y1:4,x2:8,y2:16}})))'
    )
    # Remove the stock history popover button. The inline sessions sidebar is
    # now the primary session browser, and the pane is controlled from inside
    # the sessions panel itself.
    text = text[: m_hist.start()] + "null" + text[m_hist.end() :]

    # ──────────────────────────────────────────────────────────────────────
    # 12. Inline sessions panel — splice between h6.body and h6.content.
    #
    # We reuse the prop-bindings from the existing Re1 invocation so we get
    # the parent's callback names without hard-coding them.
    # ──────────────────────────────────────────────────────────────────────
    re1_args = fe1["re1_args"]
    # Pull the bindings we need from the Re1 args.
    inline_props = {
        "localSessions": _re1_pick(re1_args, "localSessions"),
        "localSessionsLoaded": _re1_pick(re1_args, "localSessionsLoaded"),
        "remoteSessions": _re1_pick(re1_args, "remoteSessions"),
        "remoteConnected": _re1_pick(re1_args, "remoteConnected"),
        "remoteReconnecting": _re1_pick(re1_args, "remoteReconnecting"),
        "remoteSessionsLoaded": _re1_pick(re1_args, "remoteSessionsLoaded"),
        "onReconnectRemote": _re1_pick(re1_args, "onReconnectRemote"),
        "activeSession": _re1_pick(re1_args, "activeSession"),
        "onSessionClick": _re1_pick(re1_args, "onSessionClick"),
        "onRenameSession": _re1_pick(re1_args, "onRenameSession"),
        "onDeleteSession": _re1_pick(re1_args, "onDeleteSession"),
        "onOpenInNewWindow": _re1_pick(re1_args, "onOpenInNewWindow"),
        "currentCwd": _re1_pick(re1_args, "currentCwd"),
        "onOpenURL": _re1_pick(re1_args, "onOpenURL"),
    }
    missing_props = [k for k, v in inline_props.items() if v is None]
    if missing_props:
        raise RuntimeError(f"Re1 call missing props we mirror: {missing_props}")

    fe1_S = fe1["$"]
    inline_localSessions = f"[...{fe1_S}.sessions.value].sort(ccPatchSortSessions)"
    inline_remoteSessions = f"[...{fe1_S}.remoteSessions.value].sort(ccPatchSortSessions)"
    inline_refresh = f"()=>{{{fe1_S}.listSessions(),{fe1_S}.listRemoteSessions()}}"
    inline_reconnect = f"()=>{{{fe1_S}.listRemoteSessions()}}"

    inline_body = (
        f'{p0}.default.createElement("div",{{className:{h6}.body}},'
        f'{p0}.default.createElement("div",{{className:"claudePatchInlineSessions"}},'
        f'{p0}.default.createElement("div",{{className:"ccPatchSidebarHeader"}},'
        f'{p0}.default.createElement("span",{{className:"ccPatchSidebarTitle"}},"Sessions"),'
        # Collapsed-only "+" — creates a new session when the pane is collapsed
        # (hidden while expanded; the full "New Session" button covers that state).
        f'{p0}.default.createElement("button",{{className:"ccPatchHeaderBtn ccPatchCollapsedNewBtn",title:"New session",'
        f'onClick:()=>{{if(!{fe1["Z"]}.startNewConversationTab()){fe1_S}.createSession()}}}},'
        f'{p0}.default.createElement("svg",{{width:14,height:14,viewBox:"0 0 14 14",fill:"none",'
        f'stroke:"currentColor",strokeWidth:1.6,strokeLinecap:"round",strokeLinejoin:"round","aria-hidden":true}},'
        f'{p0}.default.createElement("line",{{x1:7,y1:2.5,x2:7,y2:11.5}}),'
        f'{p0}.default.createElement("line",{{x1:2.5,y1:7,x2:11.5,y2:7}}))),'
        # Search
        f'{p0}.default.createElement("button",{{className:"ccPatchHeaderBtn ccPatchSearchBtn",title:"Search",'
        f"onClick:ccPatchToggleSearch}},"
        f'{p0}.default.createElement("svg",{{width:14,height:14,viewBox:"0 0 14 14",fill:"none",'
        f'stroke:"currentColor",strokeWidth:1.4,strokeLinecap:"round","aria-hidden":true}},'
        f'{p0}.default.createElement("circle",{{cx:6,cy:6,r:4}}),'
        f'{p0}.default.createElement("line",{{x1:9,y1:9,x2:12.5,y2:12.5}}))),'
        # Filter
        f'{p0}.default.createElement("button",{{'
        f'className:ccPatchFiltersActive()?"ccPatchFilterButton ccPatchHeaderBtn ccPatchFilterButtonActive":"ccPatchFilterButton ccPatchHeaderBtn",'
        f'title:"Filter",onClick:ccPatchShowFilterMenu}},'
        f'{p0}.default.createElement("span",{{className:"ccPatchFilterIcon","aria-hidden":true}})),'
        # Settings gear -> dropdown: YOLO, Account & Usage, Switch Account, Custom Instructions
        f'{p0}.default.createElement("button",{{className:"ccPatchHeaderBtn ccPatchSettingsBtn",title:"Settings",'
        f"onClick:function(e){{ccPatchShowSettingsMenu(e,{fe1['Z']},{fe1_S})}}}}," 
        f'{p0}.default.createElement("svg",{{width:14,height:14,viewBox:"0 0 14 14",fill:"none",'
        f'stroke:"currentColor",strokeWidth:1.4,strokeLinecap:"round",strokeLinejoin:"round","aria-hidden":true}},'
        f'{p0}.default.createElement("circle",{{cx:7,cy:7,r:2.8}}),'
        f'{p0}.default.createElement("path",{{d:"M7 1.5v1.3M7 11.2v1.3M1.5 7h1.3M11.2 7h1.3M3.1 3.1l.92.92M9.98 9.98l.92.92M3.1 10.9l.92-.92M9.98 4.02l.92-.92"}}))),'
        # Collapse/expand toggle — last button in the header, icon reverses when collapsed (Copilot-style)
        f'{p0}.default.createElement("button",{{className:"ccPatchHeaderBtn ccPatchPaneCollapseBtn",title:"Toggle sidebar",'
        f"onClick:ccPatchTogglePane}},"
        f'{p0}.default.createElement("svg",{{width:14,height:14,viewBox:"0 0 14 14",fill:"none",'
        f'stroke:"currentColor",strokeWidth:1.4,strokeLinecap:"round",strokeLinejoin:"round","aria-hidden":true}},'
        f'{p0}.default.createElement("polyline",{{points:"9.5,2.5 5,7 9.5,11.5"}})))),'
        f'{p0}.default.createElement("div",{{className:"ccPatchSearchRow"}},'
        f'{p0}.default.createElement("input",{{type:"text",className:"ccPatchSearchInput",'
        f'placeholder:"Search sessions...",'
        f"onInput:(e)=>ccPatchSetSearch(e.target.value),"
        f"onKeyDown:(e)=>{{if(e.key===`Escape`)ccPatchToggleSearch()}}}})),"
        f'{p0}.default.createElement("button",{{className:"ccPatchNewSessionBtn",'
        f'onClick:()=>{{if(!{fe1["Z"]}.startNewConversationTab()){fe1_S}.createSession()}}}},'
        f'"New Session"),'
        f"{p0}.default.createElement({Rs_fn},{{"
        f"localSessions:{inline_localSessions},"
        f'localSessionsLoaded:{inline_props["localSessionsLoaded"]},'
        f"remoteSessions:{inline_remoteSessions},"
        f'remoteConnected:{inline_props["remoteConnected"]},'
        f'remoteReconnecting:{inline_props["remoteReconnecting"]},'
        f'remoteSessionsLoaded:{inline_props["remoteSessionsLoaded"]},'
        f"onReconnectRemote:{inline_reconnect},"
        f"activeSession:{fe1_S}.activeSession.value||null,"
        f'onSessionClick:{inline_props["onSessionClick"]},'
        f'onRenameSession:{inline_props["onRenameSession"]},'
        f'onDeleteSession:{inline_props["onDeleteSession"]},'
        f'onOpenInNewWindow:{inline_props["onOpenInNewWindow"]},'
        f'currentCwd:{inline_props["currentCwd"]},'
        f'authMethod:"local",'
        f"onRefresh:{inline_refresh},"
        f'onOpenURL:{inline_props["onOpenURL"]}}})),'
        f'{p0}.default.createElement("div",{{className:"claudePatchResizeHandle",onPointerDown:ccPatchStartResize}}),'
        f'{p0}.default.createElement("div",{{className:`${{{h6}.content}} claudePatchMainContent`}},'
    )
    body_marker = f'{p0}.default.createElement("div",{{className:{h6}.body}},{p0}.default.createElement("div",{{className:{h6}.content}},'
    if body_marker not in text:
        raise RuntimeError("Lost h6.body/h6.content marker")
    text = text.replace(body_marker, inline_body, 1)

    # ──────────────────────────────────────────────────────────────────────
    # 14. Sessions list — wrap the b.map block in star/pin/active/archive sections.
    # ──────────────────────────────────────────────────────────────────────
    map_re = re.compile(
        rf"{re.escape(w2)}\.sessionsList\}},{re.escape(b_var)}\.map\(\((?P<g1>{JS_ID}),(?P<o1>{JS_ID})\)=>\{{"
        rf"let\s+(?P<h5>{JS_ID})=(?P=o1)==={re.escape(w_var)},"
        rf"(?P<k2>{JS_ID})={re.escape(p_var)}===(?P=g1)\.sessionId\.value;"
        rf"return {re.escape(S0)}\.default\.createElement\({re.escape(OR0)},\{{"
        rf"key:(?P=g1)\.sessionId\.value\?\?(?P=o1),"
        rf"ref:\((?P<W5>{JS_ID})\)=>\{{if\((?P=W5)\){re.escape(refMap)}\.current\.set\((?P=o1),(?P=W5)\)\}},"
        rf'session:(?P=g1),isActive:(?P=g1)==={re.escape(rs["activeSession"])},isFocused:(?P=h5),'
        rf"isRenaming:(?P=k2),searchQuery:{re.escape(y_var)},"
        rf"onClick:\(\)=>{re.escape(z_outer)}\((?P=g1)\),"
        rf"onMouseMove:\(\)=>\{{{re.escape(O_var)}\((?P=o1)\),{re.escape(E_var)}\(null\)\}},"
        rf'onStartRename:{re.escape(tab_var)}==="local"&&{re.escape(U_outer)}\?{re.escape(s_var)}:void 0,'
        rf"onFinishRename:{re.escape(K1)},onCancelRename:{re.escape(H1)},"
        rf'onDelete:{re.escape(tab_var)}==="local"&&{re.escape(V_outer)}\?{re.escape(V_outer)}:void 0,'
        rf'onOpenInNewWindow:{re.escape(tab_var)}==="local"&&{re.escape(H_outer)}\?{re.escape(H_outer)}:void 0,'
        rf"currentCwd:{re.escape(B_outer)}\}}\)\}}\)\)\)"
    )
    m_map = map_re.search(text)
    if m_map is None:
        raise RuntimeError("Lost sessions-list b.map anchor")
    g1 = m_map.group("g1")
    o1 = m_map.group("o1")
    h5 = m_map.group("h5")
    k2 = m_map.group("k2")
    W5 = m_map.group("W5")

    or0_row = (
        f"{S0}.default.createElement({OR0},{{"
        f"key:{g1}.sessionId.value??{o1},"
        f"ref:function({W5}){{if({W5}){refMap}.current.set({o1},{W5})}},"
        f'session:{g1},isActive:{g1}==={rs["activeSession"]},isFocused:{h5},'
        f"isRenaming:{k2},searchQuery:ccPatchSearchQ||{y_var},"
        f"onClick:function(){{{z_outer}({g1})}},"
        f"onMouseMove:function(){{{O_var}({o1}),{E_var}(null)}},"
        f'onStartRename:{tab_var}==="local"&&{U_outer}?{s_var}:void 0,'
        f"onFinishRename:{K1},onCancelRename:{H1},"
        f'onDelete:{tab_var}==="local"&&{V_outer}?{V_outer}:void 0,'
        f'onOpenInNewWindow:{tab_var}==="local"&&{H_outer}?{H_outer}:void 0,'
        f"currentCwd:{B_outer}}})"
    )
    section_header = (
        f'{S0}.default.createElement("button",{{key:__KEY__,'
        f'className:__CLS__+(__STATE__?" ccPatchArchiveSectionOpen":""),'
        f"onClick:function(e){{e.stopPropagation();__SETTER__(function(v){{"
        f"localStorage.setItem(__LS_KEY__,String(!v));return!v}})}}}},"
        f'{S0}.default.createElement("span",{{className:"ccPatchArchiveLabel"}},__LABEL__),'
        f'{S0}.default.createElement("span",{{className:"ccPatchArchiveCount"}},__COUNT__)'
        f")"
    )

    def hdr(key: str, cls: str, state_var: str, setter: str, ls_key: str, label: str, count_expr: str) -> str:
        return (
            section_header.replace("__KEY__", key)
            .replace("__CLS__", cls)
            .replace("__STATE__", state_var)
            .replace("__SETTER__", setter)
            .replace("__LS_KEY__", ls_key)
            .replace("__LABEL__", label)
            .replace("__COUNT__", count_expr)
        )

    new_map = (
        f"{w2}.sessionsList}},(function(){{"
        f"var ccStar={b_var}.filter(function(s){{return!ccPatchIsArchived(s)&&ccPatchIsStarred(s)}}),"
        f"ccPin={b_var}.filter(function(s){{return!ccPatchIsArchived(s)&&!ccPatchIsStarred(s)&&ccPatchIsPinned(s)}}),"
        f"ccAct={b_var}.filter(function(s){{return!ccPatchIsArchived(s)&&!ccPatchIsStarred(s)&&!ccPatchIsPinned(s)}}),"
        f"ccArch={b_var}.filter(ccPatchIsArchived),"
        f"items=[],idx=0;"
        f"if(ccStar.length>0){{items.push("
        + hdr(
            '"__star_hdr__"',
            '"ccPatchArchiveSectionHeader ccPatchStarSection"',
            "ccPatchStarState",
            "ccPatchSetStarState",
            '"ccPatchStarOpen"',
            '"⭐ Starred"',
            "ccStar.length",
        )
        + ");}"
        f"ccStar.forEach(function({g1}){{var {o1}=idx++,{h5}={o1}==={w_var},{k2}={p_var}==={g1}.sessionId.value;"
        f"if(ccPatchStarState)items.push(" + or0_row + ")});"
        "if(ccPin.length>0){items.push("
        + hdr(
            '"__pin_hdr__"',
            '"ccPatchArchiveSectionHeader ccPatchPinSection"',
            "ccPatchPinState",
            "ccPatchSetPinState",
            '"ccPatchPinOpen"',
            '"\U0001f4cc Pinned"',
            "ccPin.length",
        )
        + ");}"
        f"ccPin.forEach(function({g1}){{var {o1}=idx++,{h5}={o1}==={w_var},{k2}={p_var}==={g1}.sessionId.value;"
        f"if(ccPatchPinState)items.push(" + or0_row + ")});"
        # ── SESSIONS section (unpinned/unstarred/unarchived) ─────────────────
        # Always shown so users have a stable "Sessions" header above the main
        # list — even when no pins/stars/archives exist.
        "items.push("
        + hdr(
            '"__sess_hdr__"',
            '"ccPatchArchiveSectionHeader ccPatchSessionsSection"',
            "ccPatchSessState",
            "ccPatchSetSessState",
            '"ccPatchSessionsOpen"',
            '"Sessions"',
            "ccAct.length",
        )
        + ");"
        f"ccAct.forEach(function({g1}){{var {o1}=idx++,{h5}={o1}==={w_var},{k2}={p_var}==={g1}.sessionId.value;"
        f"if(ccPatchSessState)items.push(" + or0_row + ")});"
        "if(ccArch.length>0){items.push("
        + hdr(
            '"__arch_hdr__"',
            '"ccPatchArchiveSectionHeader"',
            "ccPatchArchState",
            "ccPatchSetArchState",
            '"ccPatchArchiveOpen"',
            '"Archived"',
            "ccArch.length",
        )
        + ");}"
        f"ccArch.forEach(function({g1}){{var {o1}=idx++,{h5}={o1}==={w_var},{k2}={p_var}==={g1}.sessionId.value;"
        f'if(ccPatchArchState)items.push({S0}.default.createElement("div",{{key:"__arch_item_"+{o1},className:"ccPatchArchivedItem"}},'
        + or0_row
        + "))});"
        "return items})()"
        "))"
    )
    text = text[: m_map.start()] + new_map + text[m_map.end() :]

    # ──────────────────────────────────────────────────────────────────────
    # 13. Filter-subscription hook in fe1.
    # ──────────────────────────────────────────────────────────────────────
    gn1 = fe1["gn1"]
    filter_re = re.compile(
        rf"\[(?P<N>{JS_ID}),(?P<E>{JS_ID})\]={re.escape(p0)}\.useState\(null\),"
        rf"(?P<y>{JS_ID})={re.escape(p0)}\.useCallback\(\((?P<bp>{JS_ID})\)=>"
        rf"\{{setTimeout\(\(\)=>\{{let\s+(?P<t>{JS_ID})={re.escape(gn1)}\((?P=bp)\.milestoneId\)"
    )
    m_filter = filter_re.search(text)
    if m_filter is None:
        raise RuntimeError("Lost fe1 filter useCallback anchor")
    N = m_filter.group("N")
    E = m_filter.group("E")
    y_f = m_filter.group("y")
    bp = m_filter.group("bp")
    t_f = m_filter.group("t")
    new_filter = (
        f"[{N},{E}]={p0}.useState(null),"
        f"[ccPatchFTick,ccPatchFSet]={p0}.useState(0),"
        f"ccPatchFEffect={p0}.useEffect(()=>{{"
        f"let ccPFL=()=>ccPatchFSet((v)=>v+1);"
        f"ccPatchFilterListeners.add(ccPFL);"
        f"return()=>{{ccPatchFilterListeners.delete(ccPFL)}}}},[]),"
        f"{y_f}={p0}.useCallback(({bp})=>{{setTimeout(()=>{{let {t_f}={gn1}({bp}.milestoneId)"
    )
    text = text[: m_filter.start()] + new_filter + text[m_filter.end() :]

    # ──────────────────────────────────────────────────────────────────────
    # 16. YOLO mode — new sessions default to bypassPermissions when toggled.
    #     Captures the signal constructor used for session class fields
    #     (e.g. `O0`, `M1`, etc.) via its `("default")` call pattern.
    # ──────────────────────────────────────────────────────────────────────
    m_perm = re.search(
        rf"permissionMode=(?P<O0>{JS_ID})\(\"default\"\)",
        text,
    )
    if m_perm is None:
        log("Note: permissionMode init anchor not found; skipping YOLO perm patch")
    else:
        O0_perm = m_perm.group("O0")
        perm_anchor = f'permissionMode={O0_perm}("default")'
        perm_replace = f'permissionMode={O0_perm}(ccPatchYoloDefault())'
        if perm_anchor in text and perm_replace not in text:
            text = text.replace(perm_anchor, perm_replace, 1)

    # ──────────────────────────────────────────────────────────────────────
    # 17. Hoist sessions panel: add wrapper classes so CSS can position it.
    #     Adds `claudePatchRoot` to the root div and `claudePatchHeader` to
    #     the header div.
    # ──────────────────────────────────────────────────────────────────────
    root_cls_re = re.compile(
        rf'`\${{{re.escape(h6)}\.root\}} \$\{{window\.IS_FULL_EDITOR\?{re.escape(h6)}\.editorMode:""\}}`'
    )
    m_root = root_cls_re.search(text)
    if m_root is None:
        log("Note: root className anchor not found; skipping wrapper classes")
    else:
        old_root = m_root.group(0)
        new_root = old_root + '+" claudePatchRoot"'
        text = text[: m_root.start()] + new_root + text[m_root.end() :]

        header_re = re.compile(
            rf'createElement\("div",\{{className:{re.escape(h6)}\.header\}}'
        )
        m_header = header_re.search(text)
        if m_header:
            old_header = m_header.group(0)
            new_header = f'createElement("div",{{className:`${{{h6}.header}} claudePatchHeader`}}'
            text = text[: m_header.start()] + new_header + text[m_header.end() :]

    # ──────────────────────────────────────────────────────────────────────
    # 18. Top toolbar cleanup — remove the small "New session" button.
    #     The big "New Session" button in the sessions panel makes this
    #     redundant.
    # ──────────────────────────────────────────────────────────────────────
    QQ = fe1["QQ"]
    new_sesh_re = re.compile(
        rf',{re.escape(p0)}\.default\.createElement\({re.escape(QQ)},\{{ariaLabel:"New session",iconSize:\d+,'
        rf'onClick:\(\)=>\{{if\(!{re.escape(fe1["Z"])}\.startNewConversationTab\(\)\){re.escape(fe1["$"])}\.createSession\(\)\}}\}},'
        rf'{re.escape(p0)}\.default\.createElement\([^,]+,null\)\)'
    )
    m_new_sesh = new_sesh_re.search(text)
    if m_new_sesh:
        text = text[: m_new_sesh.start()] + text[m_new_sesh.end() :]
    else:
        log("Note: New session toolbar button anchor not found; skipping removal")

    # Note: there is intentionally no "rewind fix" here. Rewind / Fork+rewind
    # are left on Anthropic's native availability gate — the fork action row
    # below already renders them only when that gate (`P`) allows, while
    # keeping Fork itself always visible. We do not override the native gate.

    # ──────────────────────────────────────────────────────────────────────
    # 17. Fork/rewind action row — replace the single hover-only dropdown
    #     button + its conditionally-rendered popup with three always-visible
    #     inline buttons (Fork / Rewind / Fork + rewind) above each user
    #     message. The native menu lives in a popup that only exists in the
    #     DOM while open, so CSS alone cannot surface the three actions; we
    #     swap the render for an inline row that reuses the component's own
    #     handlers (fork-from-here `S`, rewind `U`, fork+rewind `K`) and the
    #     `P` (message-uuid) gate. The confirm modal that follows is untouched.
    #     Anchored on the stable popup option strings; identifiers captured.
    # ──────────────────────────────────────────────────────────────────────
    fork_re = re.compile(
        rf'(?P<RE>{JS_ID})\.default\.createElement\("div",\{{className:`\$\{{(?P<CZ>{JS_ID})\.container\}} '
        rf'.*?'
        rf'onClick:(?P<S>{JS_ID})\}},(?P=RE)\.default\.createElement\("span",\{{className:(?P=CZ)\.optionText\}},"Fork conversation from here"\)\),'
        rf'(?P<P>{JS_ID})&&(?P=RE)\.default\.createElement\((?P=RE)\.default\.Fragment,null,'
        rf'(?P=RE)\.default\.createElement\("button",\{{className:(?P=CZ)\.popupOption,onClick:(?P<U>{JS_ID})\}},'
        rf'(?P=RE)\.default\.createElement\("span",\{{className:(?P=CZ)\.optionText\}},"Rewind code to here"\)\),'
        rf'(?P=RE)\.default\.createElement\("button",\{{className:(?P=CZ)\.popupOption,onClick:(?P<K>{JS_ID})\}},'
        rf'(?P=RE)\.default\.createElement\("span",\{{className:(?P=CZ)\.optionText\}},"Fork conversation and rewind code"\)\)\)\)\)',
        re.S,
    )
    m_fork = fork_re.search(text)
    if m_fork is None:
        log("Note: fork/rewind action-row anchor not found; skipping")
    else:
        RE = m_fork.group("RE")
        S = m_fork.group("S")
        U = m_fork.group("U")
        K = m_fork.group("K")
        P = m_fork.group("P")
        ico = (
            f'{RE}.default.createElement("svg",{{className:"ccPatchForkIco",width:"12",'
            f'height:"12",viewBox:"0 0 24 24",fill:"none",stroke:"currentColor",'
            f'strokeWidth:"2",strokeLinecap:"round",strokeLinejoin:"round"}},'
        )
        new_fork = (
            f'{RE}.default.createElement("div",{{className:"ccPatchForkRow"}},'
            f'{RE}.default.createElement("button",{{className:"ccPatchForkBtn",onClick:{S},'
            f'title:"Fork conversation from here"}},'
            f'{ico}{RE}.default.createElement("path",{{d:"M9 15 3 9m0 0 6-6M3 9h12a6 6 0 0 1 0 12h-3"}})),'
            f'{RE}.default.createElement("span",null,"Fork")),'
            f'{P}&&{RE}.default.createElement("span",{{className:"ccPatchForkDot"}},"\\u00b7"),'
            f'{P}&&{RE}.default.createElement("button",{{className:"ccPatchForkBtn",onClick:{U},'
            f'title:"Rewind code to here"}},'
            f'{ico}{RE}.default.createElement("path",{{d:"M3 4v5h5M3.05 13A9 9 0 1 0 6 5.3L3 9"}})),'
            f'{RE}.default.createElement("span",null,"Rewind")),'
            f'{P}&&{RE}.default.createElement("span",{{className:"ccPatchForkDot"}},"\\u00b7"),'
            f'{P}&&{RE}.default.createElement("button",{{className:"ccPatchForkBtn ccPatchForkBtnAlt",'
            f'onClick:{K},title:"Fork conversation and rewind code"}},'
            f'{ico}{RE}.default.createElement("path",{{d:"M3 4v5h5M3.05 13A9 9 0 1 0 6 5.3L3 9"}}),'
            f'{RE}.default.createElement("path",{{d:"M16 14v6M13 17h6"}})),'
            f'{RE}.default.createElement("span",null,"Fork + rewind"))'
            f')'
        )
        text = text[: m_fork.start()] + new_fork + text[m_fork.end() :]

    # ──────────────────────────────────────────────────────────────────────
    # 18. "Show more" always visible — the native expandable text only renders
    #     its "Show more" button while the row is hovered (the `&&z&&` gate).
    #     Since we hide the dark truncation-fade gradient (CSS), a collapsed
    #     message would otherwise give no affordance, so drop the hover gate
    #     and let the left-aligned "Show more" link always show when the text
    #     is clipped. Anchored on the expandButton "Show more" structure.
    # ──────────────────────────────────────────────────────────────────────
    showmore_re = re.compile(
        rf'(?P<z>{JS_ID})&&(?P<RE>{JS_ID})\.default\.createElement\("div",'
        rf'\{{className:(?P<ZG>{JS_ID})\.buttonContainer\}},'
        rf'(?P=RE)\.default\.createElement\("button",'
        rf'\{{className:(?P=ZG)\.expandButton,"aria-label":"Show more"\}}'
    )
    m_sm = showmore_re.search(text)
    if m_sm is None:
        log("Note: Show-more anchor not found; skipping always-visible patch")
    else:
        RE = m_sm.group("RE")
        ZG = m_sm.group("ZG")
        new_sm = (
            f'{RE}.default.createElement("div",{{className:{ZG}.buttonContainer}},'
            f'{RE}.default.createElement("button",'
            f'{{className:{ZG}.expandButton,"aria-label":"Show more"}}'
        )
        text = text[: m_sm.start()] + new_sm + text[m_sm.end() :]

    # ──────────────────────────────────────────────────────────────────────
    # 19. Composer send button → split control with steer / queue / stop menu.
    #
    #     Claude's footer renders ONE button[type=submit] whose icon + behavior
    #     swap on `$.busy.value`: busy+empty → stop (interrupt), otherwise →
    #     send (submit the form). The harness has no native steer-vs-queue
    #     distinction — both are just an `io_message` onto the live input
    #     stream; the only real primitives are `send` (submit) and
    #     `interrupt()`. We expose all three as genuinely-distinct actions:
    #       • Steer with Message (Enter)     → submit now (inject into the turn)
    #       • Add to Queue       (Alt+Enter) → hold, auto-submit when busy→false
    #       • Stop and Send                  → interrupt(), then submit
    #     plus a dedicated Stop button while busy and a blue "send" state when
    #     the box has text and we're idle (Copilot-style).
    #
    #     (a) Stash the live session on globalThis from the icon ternary so the
    #         menu helpers can reach `.interrupt()` / `.busy`.
    # ──────────────────────────────────────────────────────────────────────
    sendicon_re = re.compile(
        rf'let\s+(?P<W>{JS_ID})=null;'
        rf'if\((?P<S>{JS_ID})\.busy\.value&&!(?P<X>{JS_ID})\)'
        rf'(?P=W)=(?P<react>{JS_ID})\.default\.createElement\((?P<stop>{JS_ID}),\{{className:(?P<cls>{JS_ID})\.stopIcon\}}\);'
        rf'else (?P=W)=(?P=react)\.default\.createElement\((?P<send>{JS_ID}),\{{className:(?P=cls)\.sendIcon\}}\);'
    )
    m_si = sendicon_re.search(text)
    if m_si is None:
        raise RuntimeError("Lost composer send/stop icon-ternary anchor")
    SW = m_si.group("W")
    SS = m_si.group("S")
    SX = m_si.group("X")
    SRE = m_si.group("react")
    SStop = m_si.group("stop")
    SSend = m_si.group("send")
    SCls = m_si.group("cls")
    # The native button is Claude's OWN stop button whenever a turn is in
    # flight (no second/white stop button), and the send arrow when idle. The
    # extra send arrow + toggle (below) appear only once there's text typed.
    new_icon = (
        f"let {SW}=null;try{{globalThis.ccPatchComposerSession={SS}}}catch(ccE){{}}"
        f"if({SS}.busy.value){SW}={SRE}.default.createElement({SStop},{{className:{SCls}.stopIcon}});"
        f"else {SW}={SRE}.default.createElement({SSend},{{className:{SCls}.sendIcon}});"
    )
    text = text[: m_si.start()] + new_icon + text[m_si.end() :]

    # (b) Replace the single submit button with: [Stop][Send+state][Chevron].
    sendbtn_re = re.compile(
        rf'(?P<react>{JS_ID})\.default\.createElement\("button",\{{type:"submit",'
        rf'disabled:!(?P<S>{JS_ID})\.busy\.value&&!(?P<X>{JS_ID}),'
        rf'className:(?P<cls>{JS_ID})\.sendButton,'
        rf'"data-permission-mode":(?P<Z>{JS_ID}),'
        rf'onClick:\((?P<E>{JS_ID})\)=>\{{if\((?P=S)\.busy\.value&&!(?P=X)\)(?P=E)\.preventDefault\(\),(?P=S)\.interrupt\(\)\}}\}},'
        rf'(?P<W>{JS_ID})\)'
    )
    m_sb = sendbtn_re.search(text)
    if m_sb is None:
        raise RuntimeError("Lost composer submit-button anchor")
    BRE = m_sb.group("react")
    BS = m_sb.group("S")
    BX = m_sb.group("X")
    BCls = m_sb.group("cls")
    BZ = m_sb.group("Z")
    BE = m_sb.group("E")
    BW = m_sb.group("W")
    state_expr = (
        f'({BS}.busy.value?({BX}?"busy-text":"busy-empty"):({BX}?"idle-text":"idle-empty"))'
    )
    new_btn = (
        # The NATIVE button: Claude's own stop while busy (no second white stop),
        # the send arrow while idle. Clicking it while busy interrupts; idle it
        # submits normally. Blue idle-text state preserved via data-cc-send-state.
        f'{BRE}.default.createElement("button",{{type:"submit",'
        f'disabled:!{BS}.busy.value&&!{BX},'
        f'className:{BCls}.sendButton,'
        f'"data-permission-mode":{BZ},'
        f'"data-cc-send-state":{state_expr},'
        f'onClick:({BE})=>{{if({BS}.busy.value){{{BE}.preventDefault();{BS}.interrupt()}}}}}},'
        f'{BW}),'
        # Busy AND text typed: a send arrow appears to the RIGHT of the stop,
        # routing to the saved mode (default Add to Queue).
        f'(({BS}.busy.value&&{BX})?{BRE}.default.createElement("button",{{type:"button",'
        f'className:"ccPatchSendArrow","aria-label":"Send",'
        f'onClick:({BE})=>ccPatchComposerAction(globalThis.ccPatchSendMode||"queue",{BE}.currentTarget.closest("form"))}},'
        f'{BRE}.default.createElement({SSend},{{className:{BCls}.sendIcon}})):null),'
        # ... and a single small chevron next to it that opens the mode selector.
        f'(({BS}.busy.value&&{BX})?{BRE}.default.createElement("button",{{type:"button",'
        f'className:"ccPatchSendChevron","aria-label":"Send options",'
        f'onClick:({BE})=>ccPatchSendMenu({BE})}},'
        f'{BRE}.default.createElement("svg",{{width:12,height:12,viewBox:"0 0 12 12",fill:"none",'
        f'stroke:"currentColor",strokeWidth:1.7,strokeLinecap:"round",strokeLinejoin:"round","aria-hidden":true}},'
        f'{BRE}.default.createElement("polyline",{{points:"2.5,8 6,4.5 9.5,8"}}))):null)'
    )
    text = text[: m_sb.start()] + new_btn + text[m_sb.end() :]

    # ──────────────────────────────────────────────────────────────────────
    # 20. Inline queued-message bubbles in the transcript (Codex-style).
    #     Queued messages are held client-side (the harness has no queue) and
    #     rendered as user-style bubbles at the bottom of the message list,
    #     with edit / delete / send-now controls, until the current turn ends.
    #     (a) re-render hook in the chat-area component `he1` so the transcript
    #         re-renders when our external queue array changes;
    #     (b) splice ccPatchRenderQueue(n1,G2,$) into the messagesContainer
    #         children, right after the mapped turns and before the spinner.
    # ──────────────────────────────────────────────────────────────────────
    he1_re = re.compile(
        rf'function (?P<fn>{JS_ID})\(\{{session:(?P<S>{JS_ID}),context:(?P<Z>{JS_ID}),'
        rf'onCreateNewSession:{JS_ID},onTeleportCheckout:{JS_ID}\}}\)\{{'
        rf'(?P<y4>{JS_ID})\(\);let {JS_ID}=(?P<RE>{JS_ID})\.useRef\(null\)'
    )
    m_he1 = he1_re.search(text)
    if m_he1 is None:
        raise RuntimeError("Lost he1 chat-area component anchor")
    he1_S = m_he1.group("S")
    he1_RE = m_he1.group("RE")
    he1_full = m_he1.group(0)
    he1_marker = f'{m_he1.group("y4")}();'
    he1_idx = he1_full.index(he1_marker) + len(he1_marker)
    he1_hook = (
        f"let[,ccPQB]={he1_RE}.useState(0);"
        f"{he1_RE}.useEffect(()=>{{var f=()=>ccPQB(v=>v+1);"
        f"globalThis.ccPatchQueueListeners.add(f);"
        f"return()=>globalThis.ccPatchQueueListeners.delete(f)}},[]);"
    )
    he1_new = he1_full[:he1_idx] + he1_hook + he1_full[he1_idx:]
    text = text[: m_he1.start()] + he1_new + text[m_he1.end() :]

    # (b) capture the chat CSS class-map (G2) via the messagesContainer.
    mc_re = re.compile(
        rf'{re.escape(he1_RE)}\.default\.createElement\("div",\{{ref:{JS_ID},'
        rf'className:`\$\{{(?P<G2>{JS_ID})\.messagesContainer\}}'
    )
    m_mc = mc_re.search(text)
    if m_mc is None:
        raise RuntimeError("Lost messagesContainer anchor")
    he1_G2 = m_mc.group("G2")

    # Injection point: the bottom spacer `,<h5>,<RE>.createElement("div",{ref:<X>,style:{height:`${<U>}px`...`.
    # Splice our queue render in AFTER the spinner row (so the working/status
    # indicator stays above the queued items) and before the bottom spacer.
    inj_re = re.compile(
        rf',(?P<h5>{JS_ID}),(?P<spc>{re.escape(he1_RE)}\.default\.createElement\("div",\{{ref:{JS_ID},'
        rf'style:\{{height:`\$\{{{JS_ID}\}}px`)'
    )
    m_inj = inj_re.search(text)
    if m_inj is None:
        raise RuntimeError("Lost transcript spacer (queue injection) anchor")
    new_seg = f',{m_inj.group("h5")},ccPatchRenderQueue({he1_RE},{he1_G2},{he1_S}),{m_inj.group("spc")}'
    text = text[: m_inj.start()] + new_seg + text[m_inj.end() :]

    # ──────────────────────────────────────────────────────────────────────
    # 21. Route the queue through Claude's OWN submit callback (`C`) so queued
    #     messages carry their attachments (images). `C` builds the message via
    #     `await <session>.send(text, files, includeSelection)`. We wrap that
    #     send: when ccPatchQueueIntent is set, capture (text, files, sel) into
    #     our queue instead of sending; otherwise send normally. Steer and
    #     Stop-and-Send leave the flag clear, so they send (with attachments)
    #     exactly as before. The trailing `<W>([])` (clear-attachments) and
    #     focus call run in BOTH branches, so the composer resets either way.
    # ──────────────────────────────────────────────────────────────────────
    csend_re = re.compile(
        rf'await (?P<S>{JS_ID})\.send\((?P<x1>{JS_ID}),(?P<B>{JS_ID}),(?P<k5>{JS_ID})\),'
        rf'(?P<W>{JS_ID})\(\[\]\)'
    )
    m_csend = csend_re.search(text)
    if m_csend is None:
        raise RuntimeError("Lost composer onSubmit send anchor (queue attachment routing)")
    cS = m_csend.group("S")
    cX1 = m_csend.group("x1")
    cB = m_csend.group("B")
    cK5 = m_csend.group("k5")
    cW = m_csend.group("W")
    new_csend = (
        f'(globalThis.ccPatchQueueIntent?'
        f'(globalThis.ccPatchQueueIntent=!1,ccPatchQueueAddFull({cX1},{cB},{cK5})):'
        f'await {cS}.send({cX1},{cB},{cK5})),{cW}([])'
    )
    text = text[: m_csend.start()] + new_csend + text[m_csend.end() :]

    write(webview_js, text)
    return True


def patch_webview_css(webview_css: Path) -> bool:
    text = read(webview_css)
    changed = False

    # ── Discover the per-build hash suffix used on minified CSS modules. ──
    # Every CSS class generated by the build looks like `<name>_<hash>`. The
    # hash is shared across all classes from the same module, so we anchor
    # on `.dropdown_<hash>{` and capture the hash. We then enumerate all the
    # `.overlay_*` class names that appear with any hash in the stylesheet
    # so the modal-overlay rule we inject lifts them above our split pane.
    m_dropdown = re.search(r"\.dropdown_(?P<hash>[A-Za-z0-9_]+)\{", text)
    if m_dropdown is None:
        raise RuntimeError("Could not find .dropdown_<hash> CSS anchor")
    # NOTE: we intentionally do NOT discover/boost modal overlays anymore. Native
    # overlays are position:fixed with their own z-index (~1000); removing the
    # stacking context from .claudePatchMainContent (below) is enough for them to
    # float over our sidebar (z-index:0). An explicit z-index:10000 boost used to
    # sit a full-viewport scrim/click-catcher ON TOP of menu content (slash command
    # menu, model picker) and ate every click. Leave native stacking alone.

    # Anchor on the OPENING of the `.dropdown_<hash>{` rule. We then walk
    # braces forward to find its closing `}` so we can append our extra
    # rules immediately after it without relying on the exact body of the
    # rule (which changes per release).
    anchor_idx = m_dropdown.start()
    brace_idx = m_dropdown.end() - 1  # at `{`
    depth = 0
    end_idx = -1
    k = brace_idx
    while k < len(text):
        c = text[k]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                end_idx = k + 1
                break
        k += 1
    if end_idx < 0:
        raise RuntimeError("Could not bracket the .dropdown_<hash> rule")
    old = text[anchor_idx:end_idx]
    new = old + (
        # Main content — NO z-index, so it does NOT create a stacking context. Native
        # panels (Account & Usage, model picker, slash command menu) render as
        # position:fixed DESCENDANTS of this element; giving it z-index:0 trapped them in
        # its context, and because the sidebar uses flex `order` it paints last -> the
        # opaque sidebar covered the modal. Without a context here, each overlay's native
        # z-index (~1000) resolves at the root and floats above the sidebar (z-index:0),
        # while keeping the overlays' own internal stacking intact so their buttons stay
        # clickable. overflow:hidden still clips in-flow chat content (fixed escapes).
        ".claudePatchMainContent{position:relative;overflow:hidden;min-width:0;min-height:0}"
        # ── Composer split send button: dedicated Stop, blue Send (idle+text),
        #    and the steer / queue / stop-and-send chevron menu. ──
        # Send arrow — appears (busy+text) to the right of Claude's native stop;
        # accent-colored so it reads clearly as the send action.
        ".ccPatchSendArrow{cursor:pointer;display:inline-flex;align-items:center;justify-content:center;"
        "width:26px;height:26px;padding:0;margin-left:4px;border:none;border-radius:5px;"
        "background:var(--app-accent-color,var(--app-button-background));color:var(--app-button-foreground,#fff)}"
        ".ccPatchSendArrow:hover{filter:brightness(1.12)}"
        # Mode toggle — a single, clearly-visible chevron right of the send arrow.
        ".ccPatchSendChevron{cursor:pointer;display:inline-flex;align-items:center;justify-content:center;"
        "width:18px;height:26px;padding:0;margin-left:1px;border:none;border-radius:5px;"
        "background:transparent;color:var(--app-secondary-foreground);opacity:1}"
        ".ccPatchSendChevron:hover,.ccPatchSendChevron.ccPatchSendChevronOpen{"
        "background:var(--app-ghost-button-hover-background);color:var(--app-primary-foreground)}"
        # NOTE: we intentionally do NOT override the native send button's color.
        # Claude already dims it when empty and brightens its own (clay/orange)
        # accent when there's text. An earlier idle-text background override used
        # --app-accent-color, which is gray in this theme and made the button
        # look gray-when-ready instead of brighter. Left to native styling.
        # The steer / queue dropdown.
        ".ccPatchSendMenu{position:fixed;z-index:100000;min-width:190px;padding:4px;"
        "background:var(--app-primary-background);"
        "border:1px solid var(--app-primary-border-color);border-radius:8px;"
        "box-shadow:0 6px 22px rgba(0,0,0,.42);font-size:12px;color:var(--app-primary-foreground)}"
        # Split mode-menu row: left 'main' executes the action; right checkbox sets the default.
        ".ccPatchSendItem{display:flex;align-items:center;gap:2px;border-radius:5px;white-space:nowrap;"
        "color:var(--app-primary-foreground)}"
        ".ccPatchSendItemMain{display:flex;align-items:center;gap:8px;flex:1;min-width:0;padding:6px 8px;"
        "border-radius:5px;cursor:pointer}"
        ".ccPatchSendItemMain:hover{background:var(--app-ghost-button-hover-background)}"
        ".ccPatchSendItemIcon{display:inline-flex;align-items:center;justify-content:center;"
        "width:16px;height:16px;flex:0 0 16px;opacity:.85}"
        ".ccPatchSendItemLabel{flex:1}"
        ".ccPatchSendItemKey{opacity:.5;font-size:11px;margin-left:16px;letter-spacing:.02em}"
        # Right-side checkbox marking the default mode (orange when checked, always readable).
        ".ccPatchSendCheck{flex:0 0 auto;width:18px;height:18px;margin:0 6px 0 2px;padding:0;border-radius:4px;"
        "border:1.6px solid var(--app-secondary-foreground);background:transparent;color:#fff;cursor:pointer;"
        "display:inline-flex;align-items:center;justify-content:center;opacity:.75}"
        ".ccPatchSendCheck:hover{opacity:1;border-color:var(--app-primary-foreground)}"
        ".ccPatchSendCheckOn{background:var(--app-claude-clay-button-orange,#c6613f);"
        "border-color:var(--app-claude-clay-button-orange,#c6613f);opacity:1}"
        # inline queued-message bubbles in the transcript (Codex-style)
        ".ccPatchQueueWrap{display:flex;flex-direction:column;gap:2px;padding:6px 0 2px}"
        ".ccPatchQueueHeader{font-size:10px;font-weight:600;letter-spacing:.08em;opacity:.5;"
        "padding:0 0 2px 2px;text-transform:uppercase}"
        # Queued message = a real chat bubble, just grayed/pending until it sends.
        ".ccPatchQueuedMsg{opacity:.55;filter:grayscale(.25)}"
        ".ccPatchQueuedMsg:hover{opacity:.85;filter:none}"
        ".ccPatchQueuedMsg .ccPatchQueueControls{display:none;position:absolute;top:-11px;right:0;"
        "gap:1px;padding:2px;border-radius:6px;background:var(--app-primary-background);"
        "border:1px solid var(--app-primary-border-color);box-shadow:0 2px 8px rgba(0,0,0,.3);z-index:3}"
        ".ccPatchQueuedMsg:hover .ccPatchQueueControls{display:flex}"
        ".ccPatchQueueCtl{display:inline-flex;align-items:center;justify-content:center;width:22px;height:22px;"
        "padding:0;border:none;border-radius:4px;background:transparent;"
        "color:var(--app-secondary-foreground);cursor:pointer}"
        ".ccPatchQueueCtl:hover{background:var(--app-ghost-button-hover-background);color:var(--app-primary-foreground)}"
        ".ccPatchQueueEdit{width:100%;min-width:220px;resize:vertical;font:inherit;line-height:inherit;"
        "color:var(--app-primary-foreground);background:transparent;border:none;outline:none}"
        # Queued-message attachment pills (carried images)
        ".ccPatchQueueAtt{display:flex;flex-wrap:wrap;gap:4px;margin:5px 0 0;justify-content:flex-end}"
        ".ccPatchQueueAttPill{display:inline-flex;align-items:center;gap:5px;max-width:220px;"
        "padding:2px 7px;border-radius:6px;background:var(--app-input-background);"
        "border:1px solid var(--app-input-border,var(--app-primary-border-color));font-size:11px;opacity:.9}"
        ".ccPatchQueueAttName{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}"
        ".ccPatchQueueAttImg{width:18px;height:18px;object-fit:cover;border-radius:3px;display:block}"
        # Color-theme picker (Settings dropdown -> popup of whole-app presets)
        ".ccPatchThemeBox{max-width:400px}"
        ".ccPatchThemeGrid{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin:12px 0 4px}"
        ".ccPatchThemeCard{display:flex;align-items:center;gap:9px;padding:8px 9px;border-radius:8px;cursor:pointer;"
        "border:1px solid var(--app-primary-border-color);background:var(--app-input-background);"
        "color:var(--app-primary-foreground);font:inherit;text-align:left}"
        ".ccPatchThemeCard:hover{background:var(--app-ghost-button-hover-background)}"
        ".ccPatchThemeCardActive{border-color:var(--app-accent-color,#c6613f);box-shadow:0 0 0 1px var(--app-accent-color,#c6613f) inset}"
        ".ccPatchThemeSw{position:relative;width:28px;height:28px;border-radius:7px;flex:0 0 28px;border:1px solid rgba(128,128,128,.35)}"
        ".ccPatchThemeDot{position:absolute;right:-3px;bottom:-3px;width:12px;height:12px;border-radius:50%;border:2px solid var(--app-primary-background)}"
        ".ccPatchThemeName{font-size:12.5px}"
        # h6.root becomes positioning anchor for the absolutely-positioned sessions panel
        ".claudePatchRoot{position:relative;min-width:0;min-height:0}"
        # h6.header gets right-padding so its buttons clear the sessions column
        ".claudePatchHeader{display:none!important}"
        # Inline sessions panel — z-index:1 so native panels (modals, usage) float above
        ".claudePatchInlineSessions{order:2;position:relative;z-index:0;"
        "flex:0 0 var(--claude-patch-sessions-width,min(44vw,360px));"
        "min-width:180px;max-width:75%;min-height:0;"
        "border-left:1px solid var(--app-primary-border-color);"
        "display:flex;flex-direction:column;"
        "overflow:hidden;background:var(--app-primary-background)}"
        # NOTE: no flex-basis/min-width transition. It made collapse/expand feel
        # laggy and "glitchy" (the panel animated over .22s while position/flex
        # switched instantly), and it made resize lag the cursor by .22s. Instant
        # is crisp, Copilot-style.
        # Rs component fills remaining height below header
        ".claudePatchInlineSessions>div:last-child{flex:1;min-height:0;overflow:hidden}"
        # Resize handle — absolutely positioned along the left edge of the panel
        ".claudePatchResizeHandle{order:1;position:relative;z-index:0;flex:0 0 4px;"
        "align-self:stretch;"
        "cursor:col-resize;background:var(--app-primary-border-color);opacity:.5}"
        ".claudePatchResizeHandle:hover,.claudePatchResizeHandle:active{"
        "opacity:1;background:var(--vscode-sash-hoverBorder,var(--app-primary-border-color))}"
        # Pane-hidden: panel floats absolute (no flex space), just header buttons remain (Copilot-style)
        ".ccPatchPaneHidden .claudePatchInlineSessions"
        "{position:absolute!important;right:0;top:0;bottom:0;"
        "flex:none!important;width:auto!important;min-width:auto!important;max-width:none!important;"
        "opacity:1!important;pointer-events:auto!important;z-index:40!important;"
        "border-left:none!important;background:transparent!important}"
        ".ccPatchPaneHidden .ccPatchSidebarTitle,"
        ".ccPatchPaneHidden .ccPatchSearchRow,"
        ".ccPatchPaneHidden .ccPatchSearchBtn,"
        ".ccPatchPaneHidden .ccPatchNewSessionBtn,"
        ".ccPatchPaneHidden .claudePatchInlineSessions>div:last-child,"
        ".ccPatchPaneHidden .claudePatchResizeHandle"
        "{display:none!important}"
        # Collapsed-only "+" new-session button: hidden while expanded, shown when collapsed.
        ".ccPatchCollapsedNewBtn{display:none!important}"
        ".ccPatchPaneHidden .ccPatchCollapsedNewBtn{display:inline-flex!important}"
        ".ccPatchPaneHidden .ccPatchSidebarHeader{background:var(--app-primary-background);"
        "border-bottom:none!important;border-radius:0 0 0 8px;"
        "box-shadow:-2px 2px 8px #00000018;padding:2px 4px}"
        # Slim Copilot-style sidebar header
        ".ccPatchSidebarHeader{flex:0 0 auto;display:flex;align-items:center;"
        "padding:2px 4px 2px 8px;border-bottom:1px solid var(--app-primary-border-color);"
        "height:35px;box-sizing:border-box}"
        ".ccPatchSidebarTitle{flex:1;font-size:11px;font-weight:600;text-transform:uppercase;"
        "letter-spacing:.06em;opacity:.65;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}"
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
        # Stacked Copilot-style session row — 48px tall
        ".ccPatchSessionItem{display:flex!important;align-items:center!important;"
        "min-height:48px!important;padding:0 10px!important;gap:6px!important;"
        "transition:background .12s ease!important;box-sizing:border-box!important}"
        "button.ccPatchSessionItem:hover{"
        "background:var(--app-list-hover-background,rgba(255,255,255,.06))!important}"
        ".ccPatchRowInner{flex:1;min-width:0;display:flex;flex-direction:column;gap:2px}"
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
        ".ccPatchActionBtn svg{width:12px;height:12px;display:block}"
        ".ccPatchHeaderBtn svg{width:14px;height:14px;display:block}"
        ".ccPatchStarBtn:hover{color:var(--vscode-charts-yellow,#f59e0b)!important}"
        ".ccPatchPinBtn:hover{color:var(--vscode-charts-blue,#3b82f6)!important}"
        ".ccPatchDeleteBtn:hover{color:var(--vscode-errorForeground,#f87171)!important}"
        ".ccPatchArchiveBtn:hover{color:var(--vscode-charts-orange,#f59e0b)!important}"
        ".ccPatchUnarchiveBtn:hover{color:var(--vscode-charts-green,#10b981)!important}"
        # Search row
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
        # Status dot
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
        # Section headers
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
        ".claudePatchFilterOption{display:flex;align-items:center;gap:9px;"
        "padding:6px 10px 6px 7px;border-radius:6px;cursor:pointer;user-select:none}"
        ".claudePatchFilterOption:hover{background:rgba(255,255,255,.08)}"
        ".ccPatchFilterCheck{flex:0 0 15px;width:15px;height:15px;display:flex;align-items:center;"
        "justify-content:center;color:#4da3ff;opacity:0;transition:opacity .1s ease}"
        ".ccPatchFilterOn .ccPatchFilterCheck{opacity:1}"
        ".ccPatchFilterItemIcon{flex:0 0 auto;width:12px;height:12px;opacity:.8}"
        ".ccPatchFilterItemLabel{flex:1;line-height:1.3;font-size:12.5px;opacity:.82}"
        ".ccPatchFilterOn .ccPatchFilterItemLabel{opacity:1;color:var(--app-primary-foreground)}"
        ".claudePatchFilterFooter{display:flex;justify-content:flex-end;padding:4px 2px 2px}"
        ".claudePatchFilterFooter button{background:transparent;border:0;"
        "color:var(--app-secondary-foreground);font-size:12px;padding:4px 8px;"
        "border-radius:4px;cursor:pointer}"
        ".claudePatchFilterFooter button:hover{background:var(--app-list-hover-background);"
        "color:var(--app-primary-foreground)}"
        # Hide built-in duplicate search row
        ".claudePatchInlineSessions [class*=\"searchRow_\"]{display:none!important}"
        # ── Orbit chat restyle (CSS-only; hash-proof [class*=] selectors) ──
        # Kill the left-hand timeline rail: the dot (:before) + vertical line
        # (:after) Codex doesn't have, and reclaim its 30px indent so replies
        # run wider/bigger.
        "[class*=\"timelineMessage_\"]:before,[class*=\"timelineMessage_\"]:after{display:none!important}"
        "[class*=\"timelineMessage_\"]{padding-left:2px!important}"
        # Tool-call rows: small + dimmed (Codex shrinks actions, keeps prose big)
        # and drop the leading middle-dot bullet.
        "[class*=\"toolItem_\"]:before{display:none!important}"
        "[class*=\"toolItem_\"]{font-size:11.5px!important;opacity:.78;padding:3px 0!important;gap:6px!important}"
        "[class*=\"toolItem_\"]:hover{opacity:1}"
        "[class*=\"toolName_\"]{font-size:11.5px!important}"
        "[class*=\"toolsList_\"]{margin-top:2px!important}"
        # User message = accented chat bubble (Copilot/Codex-style). The bubble
        # lives on the TEXT (expandableContainer) only, so pasted images sit
        # OUTSIDE/below it instead of boxed inside. :has() scopes the strip to
        # prose messages, leaving slash-command messages with their own look.
        "[class*=\"userMessage_\"]:has([class*=\"expandableContainer_\"]){display:flex!important;"
        "flex-direction:column;background:transparent!important;border:none!important;"
        "border-radius:0!important;padding:0!important;overflow:visible!important;"
        "max-width:100%!important;margin-left:20px!important}"
        "[class*=\"userMessage_\"] [class*=\"expandableContainer_\"]{"
        "background:rgba(255,255,255,.11);"
        "border:1px solid rgba(255,255,255,.14);"
        "border-radius:11px;padding:8px 13px;max-width:100%;"
        "color:var(--app-primary-foreground)}"
        # Pasted images: detached row BELOW the text bubble (order:2), no
        # border/bg, wraps, overflow visible so a hovered image can grow.
        "[class*=\"userMessageAttachments\"]{order:2!important;padding:8px 0 0!important;"
        "margin:0!important;background:transparent!important;border:none!important;"
        "overflow:visible!important;flex-wrap:wrap;justify-content:flex-end!important}"
        # Drop the awkward composer divider lines (attached-images<->text, text<->footer buttons).
        "[class*=\"attachedFilesContainerAbove\"]{border-bottom:none!important}"
        "[class*=\"inputFooter\"]{border-top:none!important}"
        # 'Show more' sits on the left of the message bubble.
        "[class*=\"expandableContainer_\"] [class*=\"buttonContainer_\"]{justify-content:flex-start!important;align-items:flex-start!important}"
        # Sent images render as "pill" chips (tiny icon + filename + dims) with
        # a near-black native background (color-mix of --app-input-background).
        # Strip that box so they read clean, slightly enlarge the thumb, and
        # add a subtle hover ring. Hovering shows a big floating preview popup
        # (helper JS ccPatchImgPreview: fixed, centered, reads the chip's img).
        "[class*=\"userMessageAttachments\"] [class*=\"pill\"]{background:transparent!important;"
        "border:1px solid rgba(255,255,255,.2)!important;border-radius:5px!important;cursor:zoom-in;"
        "transition:background .14s ease,border-color .14s ease}"
        "[class*=\"userMessageAttachments\"] [class*=\"pill\"]:hover{"
        "background:rgba(255,255,255,.06)!important;border-color:rgba(255,255,255,.38)!important}"
        "[class*=\"userMessageAttachments\"] [class*=\"pill\"] [class*=\"thumbIcon\"]"
        "{width:18px!important;height:18px!important}"
        ".ccPatchImgPreview{position:fixed;z-index:99999;pointer-events:none;border-radius:10px;"
        "overflow:hidden;border:1px solid rgba(255,255,255,.3);"
        "box-shadow:0 18px 50px #000000cc;animation:ccPatchFadeIn .1s ease}"
        ".ccPatchImgPreview img{display:block;max-width:46vw;max-height:60vh;object-fit:contain}"
        # "Show more / show less": subtle inline links, not chunky filled pills
        # (covers BOTH expandButton and collapseButton).
        # expandButton ("Show more") is natively position:absolute (pinned
        # bottom-right), so flex alignment can't move it -- force it static so
        # it flows into the left-aligned buttonContainer. Bright, bold color.
        "[class*=\"expandButton\"],[class*=\"collapseButton\"]{position:static!important;"
        "background:transparent!important;border:none!important;box-shadow:none!important;"
        "color:#4da3ff!important;padding:2px 0!important;margin:0!important;"
        "font-size:.85em!important;font-weight:600!important;opacity:1}"
        "[class*=\"expandButton\"]:hover,[class*=\"collapseButton\"]:hover{"
        "color:#7cc0ff!important;text-decoration:underline;transform:none!important}"
        # Kill the dark truncation-fade gradient on collapsed messages; the
        # JS patch makes the "Show more" link always visible, left-aligned
        # below the text (images sit on their own row below, so no collision).
        "[class*=\"expandableContainer\"] [class*=\"truncationGradient\"]{display:none!important}"
        "[class*=\"expandableContainer\"] [class*=\"buttonContainer\"]{display:flex!important;"
        "justify-content:flex-start!important;padding:0!important;margin-top:3px!important}"
        "[class*=\"expandableContainer\"] [class*=\"expandButton\"],"
        "[class*=\"expandableContainer\"] [class*=\"collapseButton\"]{margin-left:0!important}"
        # Fork / rewind action row: three inline buttons (Fork / Rewind /
        # Fork + rewind) centered above each user message with a divider line
        # on each side. Clearly visible by default, brighter on hover.
        ".ccPatchForkRow{display:flex;gap:7px;align-items:center;justify-content:center;"
        "flex-wrap:wrap;padding:5px 2px 8px;opacity:.4;transition:opacity .15s ease}"
        ".ccPatchForkRow::before,.ccPatchForkRow::after{content:\"\";flex:1 1 auto;min-width:14px;"
        "height:1px;background:rgba(255,255,255,.16)}"
        ".ccPatchForkRow:hover,.ccPatchForkRow:focus-within{opacity:1}"
        ".ccPatchForkBtn{display:inline-flex;align-items:center;gap:5px;"
        "background:transparent;border:1px solid transparent;"
        "color:#fff;border-radius:7px;padding:3px 10px 3px 8px;"
        "font-size:11px;line-height:1.5;cursor:pointer;white-space:nowrap;"
        "transition:background .12s,border-color .12s}"
        ".ccPatchForkBtn:hover{background:rgba(255,255,255,.13);border-color:rgba(255,255,255,.22)}"
        ".ccPatchForkBtnAlt:hover{background:rgba(55,148,255,.28);border-color:rgba(55,148,255,.55)}"
        ".ccPatchForkIco{flex:0 0 auto;opacity:.95}"
        ".ccPatchForkDot{opacity:.4;user-select:none;pointer-events:none;font-size:11px;line-height:1}"
        # Sticky header DELETED. Claude's sticky-mode pins each user message to
        # the top of the scroll ("the top thing"). Neutralize it so messages
        # just scroll normally — no floating box. This ALSO removes the old
        # sticky-scoped rule that was hiding .ccPatchForkRow on every message
        # (the stickyHeader class is on all user messages), which is why the
        # fork/rewind buttons had gone "perma-gone".
        "[class*=\"stickyHeader\"]{position:static!important;background:transparent!important;"
        "background-image:none!important;border:0!important;box-shadow:none!important}"
        # YOLO toggle slider in settings dropdown
        ".ccPatchYoloToggle{position:relative;display:inline-flex;align-items:center;"
        "width:32px;height:18px;flex-shrink:0;margin-left:auto;cursor:pointer}"
        ".ccPatchYoloToggle input{position:absolute;opacity:0;width:0;height:0;pointer-events:none}"
        ".ccPatchYoloSlider{position:absolute;top:0;left:0;right:0;bottom:0;"
        "background:rgba(255,255,255,.18);border-radius:9px;transition:background .18s}"
        ".ccPatchYoloSlider::before{content:'';position:absolute;height:14px;width:14px;"
        "left:2px;bottom:2px;background:#fff;border-radius:50%;transition:transform .18s}"
        ".ccPatchYoloToggle input:checked+.ccPatchYoloSlider{background:#f97316}"
        ".ccPatchYoloToggle input:checked+.ccPatchYoloSlider::before{transform:translateX(14px)}"
        # Settings gear button -- subtle highlight on open
        ".ccPatchSettingsBtnOpen{opacity:1!important;color:var(--app-primary-foreground)!important}"
        # Settings dropdown menu
        ".ccPatchSettingsMenu{position:fixed;z-index:2000;"
        "background:var(--app-menu-background);border:1px solid var(--app-menu-border);"
        "border-radius:8px;padding:4px;box-shadow:0 6px 20px #00000055;"
        "display:flex;flex-direction:column;min-width:190px;max-width:240px;"
        "font-size:13px;color:var(--app-primary-foreground)}"
        ".ccPatchSettingsItem{display:flex;align-items:center;gap:8px;"
        "box-sizing:border-box;padding:7px 10px;border-radius:5px;cursor:pointer;"
        "user-select:none;background:transparent;border:0;width:100%;text-align:left;"
        "color:var(--app-primary-foreground);font-size:12.5px}"
        ".ccPatchSettingsItem:hover{background:var(--app-list-hover-background)}"
        ".ccPatchSettingsItemIcon{flex:0 0 auto;width:14px;height:14px;display:flex;"
        "align-items:center;justify-content:center;opacity:.7}"
        ".ccPatchSettingsItem:hover .ccPatchSettingsItemIcon{opacity:1}"
        ".ccPatchSettingsItemLabel{flex:1;line-height:1.3}"
        # Instructions modal overlay
        ".ccPatchInstructionsOverlay{position:fixed;inset:0;z-index:10001;"
        "background:rgba(0,0,0,.55);display:flex;align-items:center;justify-content:center;"
        "animation:ccPatchFadeIn .18s ease}"
        ".ccPatchInstructionsBox{position:relative;width:min(680px,92vw);max-height:85vh;"
        "background:var(--app-primary-background);border:1px solid var(--app-primary-border-color);"
        "border-radius:10px;box-shadow:0 8px 40px #00000055;display:flex;flex-direction:column;"
        "overflow:hidden;animation:ccPatchSlideUp .22s ease}"
        "@keyframes ccPatchFadeIn{from{opacity:0}to{opacity:1}}"
        "@keyframes ccPatchSlideUp{from{opacity:0;transform:translateY(16px)}to{opacity:1;transform:none}}"
        ".ccPatchInstructionsClose{position:absolute;top:10px;right:10px;z-index:1;"
        "display:flex;align-items:center;justify-content:center;width:28px;height:28px;"
        "border-radius:6px;border:0;background:transparent;color:var(--app-secondary-foreground);"
        "cursor:pointer;opacity:.7;padding:0}"
        ".ccPatchInstructionsClose:hover{background:var(--app-list-hover-background);opacity:1}"
        ".ccPatchInstructionsTabs{display:flex;flex-shrink:0;border-bottom:1px solid var(--app-primary-border-color);"
        "padding:6px 8px 0;gap:2px}"
        ".ccPatchInstructionsTab{flex:1;padding:9px 12px 7px;background:transparent;border:0;"
        "border-bottom:2px solid transparent;color:var(--app-secondary-foreground);cursor:pointer;"
        "font-size:13px;font-weight:500;transition:color .12s,border-color .12s;margin-bottom:-1px}"
        ".ccPatchInstructionsTab:hover{color:var(--app-primary-foreground)}"
        ".ccPatchInstructionsTabActive{color:var(--vscode-charts-purple,#a855f7)!important;"
        "border-bottom-color:var(--vscode-charts-purple,#a855f7)!important}"
        ".ccPatchInstructionsContent{flex:1;display:flex;flex-direction:column;padding:12px 16px 8px;"
        "min-height:0;overflow:hidden}"
        ".ccPatchInstructionsStatus{font-size:11px;opacity:.6;margin-bottom:8px;flex-shrink:0;min-height:16px}"
        ".ccPatchInstructionsTextarea{flex:1;min-height:180px;width:100%;box-sizing:border-box;"
        "background:var(--vscode-input-background,rgba(0,0,0,.15));border:1px solid var(--vscode-input-border,rgba(255,255,255,.1));"
        "border-radius:6px;color:var(--app-primary-foreground);font-family:ui-monospace,Consolas,monospace;"
        "font-size:12.5px;line-height:1.55;padding:10px;resize:none;outline:none;tab-size:2}"
        ".ccPatchInstructionsTextarea:focus{border-color:var(--vscode-focusBorder,#007acc)}"
        ".ccPatchInstructionsTextarea:disabled{opacity:.5}"
        ".ccPatchInstructionsActions{display:flex;gap:8px;justify-content:flex-end;padding:10px 0 6px;flex-shrink:0}"
        ".ccPatchInstructionsSaveBtn,.ccPatchInstructionsOpenBtn{padding:7px 16px;border-radius:6px;"
        "border:0;cursor:pointer;font-size:12.5px;font-weight:500;transition:background .12s,opacity .12s}"
        ".ccPatchInstructionsSaveBtn{background:var(--vscode-button-background,#3b82f6);"
        "color:var(--vscode-button-foreground,#fff)}"
        ".ccPatchInstructionsSaveBtn:hover{background:var(--vscode-button-hoverBackground,#2563eb)}"
        ".ccPatchInstructionsSaveBtn:disabled{opacity:.4;cursor:not-allowed}"
        ".ccPatchInstructionsOpenBtn{background:var(--vscode-button-secondaryBackground,rgba(127,127,127,.12));"
        "color:var(--vscode-button-secondaryForeground,var(--app-primary-foreground))}"
        ".ccPatchInstructionsOpenBtn:hover{background:var(--vscode-button-secondaryHoverBackground,rgba(127,127,127,.2))}"
        # Switch-account confirmation modal — small, centered, blue accent
        ".ccPatchConfirmOverlay{position:fixed;inset:0;z-index:10002;"
        "background:rgba(0,0,0,.55);display:flex;align-items:center;justify-content:center;"
        "animation:ccPatchFadeIn .18s ease}"
        ".ccPatchConfirmBox{width:min(380px,90vw);padding:22px 22px 18px;"
        "background:var(--app-primary-background);border:1px solid var(--app-primary-border-color);"
        "border-radius:10px;box-shadow:0 12px 40px #00000066;"
        "display:flex;flex-direction:column;align-items:center;text-align:center;"
        "animation:ccPatchSlideUp .22s ease}"
        ".ccPatchConfirmIcon{display:flex;align-items:center;justify-content:center;"
        "width:46px;height:46px;border-radius:50%;margin-bottom:12px;"
        "background:rgba(59,130,246,.14);color:var(--vscode-charts-blue,#3b82f6)}"
        ".ccPatchConfirmTitle{font-size:15px;font-weight:600;margin:0 0 6px;letter-spacing:.01em}"
        ".ccPatchConfirmDesc{font-size:12.5px;opacity:.68;line-height:1.5;margin:0 4px 14px;max-width:320px}"
        ".ccPatchConfirmStatus{font-size:11.5px;opacity:.6;margin:0 0 10px;min-height:14px}"
        ".ccPatchConfirmActions{display:flex;gap:8px;width:100%;justify-content:flex-end}"
        ".ccPatchConfirmCancelBtn,.ccPatchConfirmConfirmBtn{padding:8px 16px;border-radius:6px;"
        "border:0;cursor:pointer;font-size:12.5px;font-weight:500;transition:background .12s,opacity .12s}"
        ".ccPatchConfirmCancelBtn{background:var(--vscode-button-secondaryBackground,rgba(127,127,127,.14));"
        "color:var(--vscode-button-secondaryForeground,var(--app-primary-foreground))}"
        ".ccPatchConfirmCancelBtn:hover{background:var(--vscode-button-secondaryHoverBackground,rgba(127,127,127,.24))}"
        ".ccPatchConfirmConfirmBtn{background:var(--vscode-button-background,#3b82f6);"
        "color:var(--vscode-button-foreground,#fff)}"
        ".ccPatchConfirmConfirmBtn:hover{background:var(--vscode-button-hoverBackground,#2563eb)}"
        ".ccPatchConfirmCancelBtn:disabled,.ccPatchConfirmConfirmBtn:disabled{opacity:.5;cursor:not-allowed}"
        # Collapse toggle — regular header button, icon reverses when collapsed
        ".ccPatchPaneCollapseBtn svg{transition:transform .25s ease}"
        ".ccPatchPaneHidden .ccPatchPaneCollapseBtn svg{transform:rotate(180deg)}"
    )
    css_marker = ".ccPatchHeaderBtn{display:inline-flex;"
    old_media_hide = (
        "@media(max-width:900px){"
        ".claudePatchInlineSessions{flex-basis:0!important;min-width:0!important;"
        "opacity:0;pointer-events:none}"
        ".claudePatchResizeHandle{display:none!important}}"
    )
    if old_media_hide in text:
        text = text.replace(old_media_hide, "", 1)
        changed = True
    if old in text and css_marker not in text:
        text = text.replace(old, new, 1)
        changed = True
    if changed:
        write(webview_css, text)
    return changed


def patch_extension_dir(extension_dir: Path) -> bool:
    changed = False
    extension_js = extension_dir / "extension.js"
    webview_js = extension_dir / "webview" / "index.js"
    webview_css = extension_dir / "webview" / "index.css"
    if not webview_js.exists() or not webview_css.exists():
        raise RuntimeError("Could not find Claude webview index.js / index.css")
    if extension_js.exists():
        log("Patching Claude extension host JS")
        changed |= patch_extension_host_js(extension_js)
    log("Patching Claude webview JS")
    changed |= patch_webview_js(webview_js)
    log("Patching Claude webview CSS")
    changed |= patch_webview_css(webview_css)
    verify_extension_dir(extension_dir)
    return changed


def patch_extension_host_js(path: Path) -> bool:
    text = read(path)
    if "read_claude_md_response" in text:
        return False
    ids = re.search(
        rf"openConfigFile\({JS_ID}\)\{{[\s\S]{{0,700}}?(?P<path>{JS_ID})\.join\([\s\S]{{0,300}}?(?P<fs>{JS_ID})\.writeFileSync",
        text,
    )
    if not ids:
        raise RuntimeError("Could not locate host fs/path identifiers for CLAUDE.md editor patch")
    path_id = ids.group("path")
    fs_id = ids.group("fs")
    anchor_re = re.compile(
        rf'case"open_config_file":return this\.openConfigFile\((?P<msg>{JS_ID})\.request\.configType\),'
        r'\{type:"open_config_file_response"\};'
    )
    match = anchor_re.search(text)
    if not match:
        raise RuntimeError("Could not find open_config_file host request anchor")
    msg = match.group("msg")
    # ccH expands a leading "~/" using os.homedir() so the webview can pass
    # platform-neutral "~/.claude/CLAUDE.md" without trying to resolve $HOME
    # on its own (no os module in the renderer).
    insert = (
        f'case"open_claude_md":{{let ccP={msg}.request.filePath,ccT={msg}.request.title||"CLAUDE.md",ccC="";'
        f'try{{let ccH=require("os").homedir();if(typeof ccP==="string"&&ccP.indexOf("~/")===0)ccP=ccH+ccP.slice(1);'
        f'if({fs_id}.existsSync(ccP))ccC={fs_id}.readFileSync(ccP,"utf8");'
        f'let ccR=await this.openContent(ccC,ccT,!0);'
        f'if(ccR&&typeof ccR.updatedContent==="string"){{{fs_id}.mkdirSync({path_id}.dirname(ccP),{{recursive:!0}});'
        f'{fs_id}.writeFileSync(ccP,ccR.updatedContent,"utf8")}}'
        f'return{{type:"open_claude_md_response"}}}}catch(ccE){{this.output?.error?.(`Failed to edit ${{ccP}}: ${{ccE}}`);throw ccE}}}}'
        f'case"read_claude_md":{{let ccP={msg}.request.filePath,ccE=false,ccC="";'
        f'try{{let ccH=require("os").homedir();if(typeof ccP==="string"&&ccP.indexOf("~/")===0)ccP=ccH+ccP.slice(1);'
        f'if({fs_id}.existsSync(ccP)){{ccC={fs_id}.readFileSync(ccP,"utf8");ccE=true}}}}catch(e){{}}'
        f'return{{type:"read_claude_md_response",content:ccC,exists:ccE}}}}'
        f'case"write_claude_md":{{let ccP={msg}.request.filePath,ccCt={msg}.request.content;'
        f'try{{let ccH=require("os").homedir();if(typeof ccP==="string"&&ccP.indexOf("~/")===0)ccP=ccH+ccP.slice(1);'
        f'{fs_id}.mkdirSync({path_id}.dirname(ccP),{{recursive:!0}});'
        f'{fs_id}.writeFileSync(ccP,ccCt,"utf8");return{{type:"write_claude_md_response",ok:!0}}}}'
        f'catch(ccE){{this.output?.error?.(`Failed to write ${{ccP}}: ${{ccE}}`);return{{type:"write_claude_md_response",ok:!1}}}}}}'
        # switch_account: fire the host-side logout command so the next request
        # forces a fresh sign-in. Webview-only showLogin() leaves valid creds in
        # place and the auth state snaps back to "Signed in" within a second.
        f'case"switch_account":{{try{{await require("vscode").commands.executeCommand("claude-vscode.logout");'
        f'return{{type:"switch_account_response",ok:!0}}}}'
        f'catch(ccE){{this.output?.error?.(`switch_account failed: ${{ccE}}`);return{{type:"switch_account_response",ok:!1,error:String(ccE)}}}}}}'
    )
    text = text[:match.start()] + insert + text[match.start():]
    write(path, text)
    return True


def verify_extension_dir(extension_dir: Path) -> None:
    host = read(extension_dir / "extension.js") if (extension_dir / "extension.js").exists() else ""
    js = read(extension_dir / "webview" / "index.js")
    css = read(extension_dir / "webview" / "index.css")
    checks = {
        "star helper": "ccPatchIsStarred" in js,
        "pin helper": "ccPatchIsPinned" in js,
        "archive helper": "ccPatchToggleArchive" in js,
        "context menu": "ccPatchShowMenu" in js and ".claudePatchContextMenu" in css,
        "pin sort": "ccPatchSortSessions" in js,
        "archive sort": "ccPatchIsArchived(e)" in js,
        "inline sessions": "claudePatchInlineSessions" in js and ".claudePatchInlineSessions" in css,
        "main content overlay": "claudePatchMainContent" in js and ".claudePatchMainContent" in css,
        "resize handle": "ccPatchStartResize" in js and ".claudePatchResizeHandle" in css,
        "stacked row": "ccPatchRowInner" in js and ".ccPatchRowInner" in css,
        "row actions": "ccPatchRowActions" in js and ".ccPatchRowActions" in css,
        "status running": ".claudePatchStatusRunning" in css,
        "pin button": "ccPatchPinBtn" in js and "SVG_PIN" not in js,
        "archive button": 'title:"Archive"' in js and "ccPatchArchiveBtn" in js,
        "unarchive button": 'title:"Unarchive"' in js and "ccPatchUnarchiveBtn" in js,
        "no rename button": 'title:"Rename session"' not in js,
        "new session btn": "ccPatchNewSessionBtn" in js and ".ccPatchNewSessionBtn" in css,
        "double-click rename": "onDoubleClick" in js,
        "edit mode wrap fix": "ccPatchRowInnerEdit" in js and ".ccPatchRowInnerEdit" in css,
        "time format": "days ago" in js,
        "now time": "return`now`" in js,
        "activity text": "ccPatchActivityText" in js,
        "archive section": "ccPatchArchiveSectionHeader" in js and ".ccPatchArchiveSectionHeader" in css,
        "pin section": "ccPatchPinState" in js,
        "star section": "ccPatchStarState" in js,
        "sessions section": "ccPatchSessState" in js and ".ccPatchSessionsSection" in css,
        "filter system": "ccPatchFilterSort" in js and "ccPatchShowFilterMenu" in js,
        "filter checkmarks": "ccPatchFilterCheck" in js and ".ccPatchFilterCheck" in css
                             and ".ccPatchFilterOn" in css,
        "sidebar header": "ccPatchSidebarHeader" in js and ".ccPatchSidebarHeader" in css,
        "header btn css": ".ccPatchHeaderBtn" in css,
        "localStorage states": "ccPatchGetSS" in js and "ccPatchTogglePin" in js,
        "star hover button": "ccPatchStarBtn" in js and ".ccPatchStarBtn" in css,
        "search toggle": "ccPatchToggleSearch" in js and ".ccPatchSearchRow" in css,
        "svg actions": "ccPatchIsPinned(" in js and "polygon" in js,
        "filter button": "ccPatchShowFilterMenu" in js and ".ccPatchFilterButton" in css,
        "filter menu css": ".claudePatchFilterMenu" in css,
        "filter svg icons": "ccPatchFilterIconSVG" in js and ".ccPatchFilterItemIcon" in css,
        "waiting indicator": "ccPatchIsWaiting" in js and ".claudePatchStatusWaiting" in css,
        "pane toggle": "ccPatchTogglePane" in js and "ccPatchPaneCollapseBtn" in js,
        "history removed": 'ariaLabel:"Session history"' not in js,
        "modal trap removed": ".claudePatchMainContent{position:relative;overflow:hidden" in css and "background-color:#000000e6" not in css,
        "no overlay zboost": "{z-index:10000!important}" not in css,
        "collapsed pane z-index": ".ccPatchPaneHidden .claudePatchInlineSessions" in css and "z-index:40!important" in css,
        "switch model item": "executeCommand(`model`)" in js,
        "chat restyle": ('[class*="userMessageAttachments"]{order:2' in css
                         and '[class*="timelineMessage_"]:before' in css
                         and '[class*="userMessageAttachments"] [class*="pill"]' in css),
        "fork action row": ('"ccPatchForkRow"' in js
                            and ".ccPatchForkRow" in css
                            and ".ccPatchForkBtnAlt" in css
                            and ".ccPatchForkDot" in css),
        "fork row dividers": ".ccPatchForkRow::before" in css,
        "image hover preview": ("ccPatchImgPreviewShow" in js
                                and ".ccPatchImgPreview" in css),
        "sticky header neutralized": '[class*="stickyHeader"]{position:static' in css,
        "show-more no-gradient": ('[class*="expandableContainer"] [class*="truncationGradient"]'
                                  in css),
        "row height": "min-height:48px" in css,
        "yolo helpers": "ccPatchYoloToggle" in js and "ccPatchYoloDefault" in js,
        "yolo perm init": "permissionMode=" in js and "ccPatchYoloDefault()" in js,
        "settings menu": "ccPatchShowSettingsMenu" in js and "ccPatchCloseSettingsMenu" in js,
        "settings dropdown css": ".ccPatchSettingsMenu" in css and ".ccPatchSettingsItem" in css,
        "account switch modal": "ccPatchSwitchAccountModal" in js and ".ccPatchConfirmOverlay" in css,
        "account switch rpc": "ccPatchSwitchAccount(" in js and 'case"switch_account"' in host,
        "instructions modal": "ccPatchInstructionsOverlay" in js and ".ccPatchInstructionsOverlay" in css,
        "instructions read": "ccPatchReadClaudeMd" in js and "read_claude_md_response" in host,
        "instructions write": "ccPatchWriteClaudeMd" in js and "write_claude_md_response" in host,
        "pane collapse btn": "ccPatchPaneCollapseBtn" in js and ".ccPatchPaneCollapseBtn" in css,
        "instructions request": "open_claude_md" in js and "open_claude_md_response" in host,
        "built-in search hide": '[class*="searchRow_"]' in css,
        "root wrapper class": "claudePatchRoot" in js and ".claudePatchRoot" in css,
        "header wrapper class": "claudePatchHeader" in js and ".claudePatchHeader" in css,
        "native header hidden": ".claudePatchHeader{display:none!important}" in css,
        "build version marker": 'var ccPatchBuildVersion="' in js,
        "global helper exports": "Object.assign(globalThis" in js and 'Object.defineProperty(globalThis,"ccPatchSearchQ"' in js,
        "hide untitled": "ccPatchIsUntitledEmpty" in js and "hideUntitled" in js,
        "send menu": ("ccPatchSendMenu" in js and "ccPatchSendChevron" in js
                      and "ccPatchComposerSession=" in js
                      and ".ccPatchSendMenu" in css and ".ccPatchSendArrow" in css),
        "queue display": ("ccPatchRenderQueue(" in js and "ccPatchQueueListeners" in js
                          and "ccPatchComposerAction(" in js
                          and ".ccPatchQueueWrap" in css and ".ccPatchQueuedMsg" in css
                          and ".ccPatchQueueCtl" in css),
        "send mode persist": "ccPatchSetSendMode" in js and "ccPatchSendMode" in js,
        "color theme": ("ccPatchThemeModal" in js and "ccPatchApplyTheme" in js
                        and "var ccPatchThemes=" in js and "ccPatchAiGlow" in js
                        and ".ccPatchThemeGrid" in css and ".ccPatchThemeCard" in css),
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
    global LOG_PATH
    parser = argparse.ArgumentParser(description="Patch Claude Code VSIX session list behavior.")
    parser.add_argument("target", nargs="?", default=DEFAULT_MARKETPLACE_ITEM)
    parser.add_argument("--out", default="")
    parser.add_argument("--version", default="")
    parser.add_argument("--target-platform", default="", help="VS Code target platform, e.g. win32-x64.")
    parser.add_argument("--download-dir", default=".")
    parser.add_argument("--log", default="claude-vsix-patch.log")
    parser.add_argument(
        "--download-only",
        action="store_true",
        help="Download the marketplace VSIX without patching. Prints STOCK_VSIX_PATH: <path>.",
    )
    parser.add_argument(
        "--patcher-version",
        default="dev",
        help="Version string embedded in the patched webview as ccPatchBuildVersion. "
             "Orbit passes its package.json version so the marker doubles as the "
             "Marketplace version.",
    )
    args = parser.parse_args()
    global PATCHER_VERSION
    PATCHER_VERSION = args.patcher_version

    LOG_PATH = Path(args.log).expanduser().resolve()
    LOG_PATH.write_text("", encoding="utf-8")
    log(f"Starting Claude VSIX patcher (version-agnostic, patcher v{PATCHER_VERSION})")

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
            args.target_platform or None,
        )

    if args.download_only:
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


def _try_install_vsix(vsix_path: Path) -> bool:
    """Try to install a patched VSIX via the `code` CLI. Returns True on success."""
    code_cli = shutil.which("code") or shutil.which("code.cmd") or shutil.which("code-insiders")
    if code_cli is None:
        log("`code` CLI not found on PATH; skipping auto-install.")
        log(f"Install manually: Extensions -> ... -> Install from VSIX -> {vsix_path}")
        return False
    log(f"Installing patched VSIX via {code_cli}")
    try:
        subprocess.check_call([code_cli, "--install-extension", str(vsix_path), "--force"])
    except subprocess.CalledProcessError as exc:
        log(f"Auto-install failed (exit {exc.returncode}). Install manually from: {vsix_path}")
        return False
    log("Patched extension installed.")
    return True


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        log(f"Patch run failed: {exc}")
        raise
