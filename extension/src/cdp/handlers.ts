// One bundled handler per §5.1 command (§6: declarative cmd → fixed handler;
// the extension never evals a backend expression). dispatch() is the chokepoint.
//
// Every handler returns an OBSERVATION DATA object; the router/host add
// correlation/transport. Media handlers return base64 bytes under a slot key
// (e.g. { media: [{ slot: "screenshot", b64, ext, mime }] }); the host turns
// these into VFS paths before any producer sees them.
//
// NOTE on Runtime.evaluate: the §6 "no remote code" rule forbids evaluating a
// BACKEND-supplied expression. The expressions below are fixed, bundle-local
// program strings; the only backend data they touch (selectors/handles/text)
// is JSON.stringify-embedded as DATA literals, never as code. The DOM-prune
// source (PRUNE_FN_SOURCE) is a constant literal.
import { type Cmd, CMD } from "../shared/commands";
import { PRUNE_FN_SOURCE } from "./prune";
import type { SessionManager } from "./session-manager";
// Industry-standard HTML→Markdown (Turndown). We inject its browser UMD into the
// page (where the DOM lives) once per page and convert there, so only the small
// markdown crosses the wire — no hand-rolled converter, no backend dependency.
import TURNDOWN_UMD from "turndown/lib/turndown.browser.umd.js?raw";
// @medv/finder — robust, unique CSS-selector generation (the "Copy selector"
// algorithm). query() returns each element's `css` so the agent can sediment a
// DURABLE locator into a workflow node (the live `handle` is session-only). It is
// ESM (`export function …`); strip the exports + expose `finder` as a page global.
import FINDER_SRC from "@medv/finder/finder.js?raw";
const FINDER_INJECT =
  `(function(){if(typeof globalThis.__skeinixFinder==="function")return;` +
  FINDER_SRC.replace(/^export /gm, "") +
  `;globalThis.__skeinixFinder=finder;})()`;

/** Ensure @medv/finder is present in the page (inject once, guarded). */
async function ensureFinder(sm: SessionManager, targetId: string): Promise<void> {
  const has = await sm.send(targetId, "Runtime.evaluate", {
    expression: "typeof globalThis.__skeinixFinder",
    returnByValue: true,
  });
  if (has?.result?.value !== "function") {
    await sm.send(targetId, "Runtime.evaluate", { expression: FINDER_INJECT });
  }
}

export type Overlay = {
  highlight(targetId: string, sel: string, label: string): Promise<void>;
  narrate(targetId: string, text: string): Promise<void>;
};

export type Handler = (
  sm: SessionManager,
  ov: Overlay,
  targetId: string,
  args: Record<string, unknown>,
) => Promise<Record<string, unknown>>;

const ok = (extra: Record<string, unknown> = {}) => ({ ok: true, ...extra });

// The host waits 30s for a correlated observation. Keep every bundle-local
// polling loop below that outer budget so the extension can always return a
// determinate success/failure (plus best-effort page state) before the gateway
// would have to label the result unknown.
export const MAX_COMMAND_WAIT_MS = 25_000;
export function boundedCommandWaitMs(value: unknown, fallback: number): number {
  const parsed = Number(value ?? fallback);
  const finite = Number.isFinite(parsed) ? parsed : fallback;
  return Math.min(Math.max(finite, 0), MAX_COMMAND_WAIT_MS);
}

async function browserWindowTabIds(
  args: Record<string, unknown>,
): Promise<number[] | undefined> {
  const raw = args.browser_window_id;
  const windowId = typeof raw === "number" ? raw : raw ? Number(raw) : NaN;
  if (!Number.isFinite(windowId)) return undefined;
  try {
    const tabs = await chrome.tabs.query({ windowId });
    return tabs
      .map((tab) => tab.id)
      .filter((tabId): tabId is number => typeof tabId === "number");
  } catch {
    return undefined;
  }
}

// Full key descriptors for named keys — `Input.dispatchKeyEvent` needs `code` +
// `windowsVirtualKeyCode` (and `text` for Enter/Space) or the page's key handlers
// (which often check keyCode===13 / e.key) won't fire. Printable single chars use
// the char itself as `text`.
const KEYMAP: Record<string, { code: string; keyCode: number; text?: string }> = {
  Enter: { code: "Enter", keyCode: 13, text: "\r" },
  Tab: { code: "Tab", keyCode: 9 },
  Escape: { code: "Escape", keyCode: 27 },
  Backspace: { code: "Backspace", keyCode: 8 },
  Delete: { code: "Delete", keyCode: 46 },
  ArrowUp: { code: "ArrowUp", keyCode: 38 },
  ArrowDown: { code: "ArrowDown", keyCode: 40 },
  ArrowLeft: { code: "ArrowLeft", keyCode: 37 },
  ArrowRight: { code: "ArrowRight", keyCode: 39 },
  Home: { code: "Home", keyCode: 36 },
  End: { code: "End", keyCode: 35 },
  PageUp: { code: "PageUp", keyCode: 33 },
  PageDown: { code: "PageDown", keyCode: 34 },
  Space: { code: "Space", keyCode: 32, text: " " },
};

/** A key only reaches the FOCUSED element — focus the handle first (e.g. press
 *  Enter on the input you just filled). No-op when no handle / no match. */
async function focusHandle(sm: SessionManager, targetId: string, handle: unknown): Promise<void> {
  if (!handle) return;
  await sm.send(targetId, "Runtime.evaluate", {
    expression:
      `(function(){var e=document.querySelector('[data-skeinix-h="'+${JSON.stringify(
        String(handle),
      )}+'"]');if(e&&e.focus)e.focus();})()`,
  });
}

// The page-side Markdown converter: ensure Turndown is present (inject its UMD
// once per page, guarded), then convert the resolved element — dropping inline
// `data:` image blobs (base64 icons) which are huge noise, keeping real URLs.
const TURNDOWN_CONVERT = `(function(root,max){
  var td=new window.TurndownService({headingStyle:"atx",bulletListMarker:"-",codeBlockStyle:"fenced"});
  td.remove(["script","style","noscript","svg"]);
  td.addRule("skeinixImg",{filter:"img",replacement:function(c,n){
    var s=n.getAttribute("src")||"",al=n.getAttribute("alt")||"";
    return s.indexOf("data:")===0?(al?"!["+al+"]":""):(s?"!["+al+"]("+s+")":"");
  }});
  var md=td.turndown(root.outerHTML||"");
  return {value:md.slice(0,max),format:"markdown",truncated:md.length>max,full_chars:md.length};
})`;

// Sync-with-wait for an act that may settle/navigate: after the action, poll for
// the caller's `expect` selector (a post-condition) up to `timeout`. Re-resolves
// the tab's CURRENT target each poll, because a click can navigate cross-process
// and swap the targetId. No `expect` → return immediately (the action is done).
// `expect_met:false` is NOT a hard error — the action ran; the condition just
// didn't appear in time (the agent can snapshot/retry).
async function settleExpect(
  sm: SessionManager,
  targetId: string,
  a: Record<string, unknown>,
): Promise<Record<string, unknown>> {
  const sel = a.expect;
  if (!sel) return ok();
  const tab = sm.tabIdFor(targetId);
  const deadline = Date.now() + boundedCommandWaitMs(a.timeout, 8000);
  const expr = `!!document.querySelector(${JSON.stringify(String(sel))})`;
  while (Date.now() < deadline) {
    await new Promise((r) => setTimeout(r, 120));
    const tid = (tab !== undefined && sm.targetForTab(tab)) || targetId;
    try {
      const r = await sm.send(tid, "Runtime.evaluate", {
        expression: expr,
        returnByValue: true,
      });
      if (r?.result?.value) return ok({ expect_met: true });
    } catch {
      /* target swapping mid-navigation — keep polling */
    }
  }
  // The expected content never appeared in time. The page may still be spinning
  // (a hung resource keeps it loading) — force-STOP the load (like the browser's
  // ✕) so the spinner halts and the DOM settles to whatever rendered, which the
  // agent can read/snapshot next instead of waiting out a perpetual spinner.
  const tid = (tab !== undefined && sm.targetForTab(tab)) || targetId;
  await sm.send(tid, "Page.enable").catch(() => {});
  await sm.send(tid, "Page.stopLoading").catch(() => {});
  return { ok: true, expect_met: false, stopped: true };
}

export const HANDLERS: Record<Cmd, Handler> = {
  [CMD.NAVIGATE]: async (sm, _o, t, a) => {
    await sm.send(t, "Page.enable");
    const url = String(a.url);
    // A tab still loading (or stuck in a perpetual "loading" spinner) can refuse
    // or hang a FRESH navigation, and a `beforeunload` handler would pop a dialog
    // that — with the debugger attached and Page enabled — blocks navigation with
    // nobody to answer it. Abort the in-flight load and clear onbeforeunload first
    // so re-navigating the SAME tab always takes.
    await sm.send(t, "Page.stopLoading").catch(() => {});
    await sm
      .send(t, "Runtime.evaluate", { expression: "try{window.onbeforeunload=null}catch(e){}" })
      .catch(() => {});
    const nav: any = await sm
      .send(t, "Page.navigate", { url })
      .catch((e) => ({ errorText: String(e?.message || e) }));
    if (nav?.errorText) return { ok: false, error: `navigate failed: ${nav.errorText}` };
    // Page.navigate returns before the page renders; honor `wait_until` by
    // polling document.readyState (no CDP event plumbing) until the requested
    // lifecycle stage is reached, then read the settled final_url/title. The wait
    // is CAPPED so a page that never stops loading can't hang the turn (and hold
    // the per-transport serial lock) — on timeout we return best-effort with
    // settled:false instead of failing, so the agent can still read what rendered.
    const waitUntil = String(a.wait_until ?? "load");
    const timeout = boundedCommandWaitMs(a.timeout, 20000);
    const deadline = Date.now() + timeout;
    let info: any = {};
    let ready = false;
    while (Date.now() < deadline) {
      await new Promise((r) => setTimeout(r, 150));
      try {
        const r = await sm.send(t, "Runtime.evaluate", {
          expression: "document.readyState",
          returnByValue: true,
        });
        const rs: string = r?.result?.value ?? "";
        if (waitUntil === "domcontentloaded") {
          ready = rs === "interactive" || rs === "complete";
        } else {
          // "load" (default) and "networkidle" both require full load.
          ready = rs === "complete";
        }
      } catch {
        // A cross-origin navigation briefly detaches the target; the SW
        // re-attaches. Swallow and keep polling until the timeout.
        ready = false;
      }
      if (ready) {
        if (waitUntil === "networkidle") {
          // extra settle delay for late XHR/render after load fires
          await new Promise((r) => setTimeout(r, 600));
        }
        break;
      }
    }
    if (!ready) {
      // Cap hit and the page never reached the target lifecycle — it may spin
      // forever (a hung resource keeps the tab loading). Force-STOP the load
      // (like the browser's ✕) so the spinner halts and the DOM settles; the
      // agent then reads the partial page instead of waiting out the spinner.
      await sm.send(t, "Page.stopLoading").catch(() => {});
    }
    // Best-effort: read the settled target info after the wait (or timeout).
    info = await sm.send(t, "Target.getTargetInfo").catch(() => ({}));
    return ok({
      final_url: info?.targetInfo?.url ?? url,
      title: info?.targetInfo?.title ?? "",
      settled: ready,
      stopped: !ready,
    });
  },
  [CMD.SNAPSHOT]: async (sm, _o, t, a) => {
    const prefix = `s${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 6)}_h`;
    const r = await sm.send(t, "Runtime.evaluate", {
      expression: `(${PRUNE_FN_SOURCE})(${JSON.stringify(prefix)},${JSON.stringify(String(a.scope || ""))},${JSON.stringify(a.prune !== false)})`,
      returnByValue: true,
    });
    const tree = r?.result?.value ?? { text: "", nodes: [] };
    if (tree.error) {
      return { ok: false, error: String(tree.error), error_code: String(tree.error), not_executed: true };
    }
    return ok({ dom: tree.text, handles: tree.nodes });
  },
  [CMD.READ_TEXT]: async (sm, _o, t, a) => {
    // Read visible text — by handle, by selector, or (default) the page. With
    // `query`, KEYWORD-SLICE instead of dumping the whole page: return only the
    // snippets around each match (± `context` chars) so big pages don't explode
    // the context. Always capped at `max_chars`.
    const expr =
      `(function(){` +
      `var h=${JSON.stringify(a.handle || "")},sel=${JSON.stringify(a.selector || "")},` +
      `q=${JSON.stringify(a.query || "")},ctx=${Number(a.context ?? 200)},max=${Number(a.max_chars ?? 8000)};` +
      `var root=h?document.querySelector('[data-skeinix-h="'+h+'"]'):(sel?document.querySelector(sel):document.body);` +
      `if(!root)return {text:"",error:"no element matched the handle/selector"};` +
      `var txt=root.innerText||"";` +
      `if(q){var low=txt.toLowerCase(),ndl=q.toLowerCase(),out=[],i=0,n=0;` +
      `while((i=low.indexOf(ndl,i))>=0&&n<30){var s=Math.max(0,i-ctx),e=Math.min(txt.length,i+ndl.length+ctx);` +
      `out.push((s>0?"…":"")+txt.slice(s,e).trim()+(e<txt.length?"…":""));i=e;n++;}` +
      `return {text:out.join("\\n---\\n").slice(0,max),matches:n,query:q};}` +
      `return {text:txt.slice(0,max),truncated:txt.length>max,full_chars:txt.length};})()`;
    const r = await sm.send(t, "Runtime.evaluate", { expression: expr, returnByValue: true });
    const v = r?.result?.value || {};
    if (v.error) return { ok: false, error: String(v.error) };
    return ok(v);
  },
  [CMD.READ_FIELDS]: async (sm, _o, t, a) => {
    // deterministic selector→value reads ONLY (§5.1): no semantic extraction
    const selectors = (a.selectors || {}) as Record<string, string>;
    const expr =
      `(function(){var s=${JSON.stringify(selectors)},o={};` +
      `for(var k in s){var e=document.querySelector(s[k]);o[k]=e?e.innerText:null;}return o;})()`;
    const r = await sm.send(t, "Runtime.evaluate", {
      expression: expr,
      returnByValue: true,
    });
    return ok({ fields: r?.result?.value ?? {} });
  },
  [CMD.QUERY]: async (sm, _o, t, a) => {
    // Find elements by CSS `selector`, and/or filter by visible `text`
    // (substring), exact `name` (text/aria-label), or `role`. Returns each match's
    // stamped `handle` (session-only, for live ops) AND a robust `css` (a durable,
    // unique selector for sedimenting into a workflow node). A bad CSS selector
    // surfaces as an error. NOTE: only STANDARD CSS is valid — Playwright
    // pseudo-selectors like `:has-text()` are NOT.
    await ensureFinder(sm, t);
    const prefix = `q${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 6)}_`;
    const expr =
      `(function(){` +
      `var sel=${JSON.stringify(a.selector || "")},role=${JSON.stringify(a.role || "")},` +
      `name=${JSON.stringify(a.name || "")},text=${JSON.stringify(a.text || "")};` +
      `var SKIP={SCRIPT:1,STYLE:1,NOSCRIPT:1,TEMPLATE:1,HEAD:1,META:1,LINK:1,TITLE:1,BODY:1,HTML:1};` +
      // Pick the search base: an explicit CSS selector; else, for a TEXT search,
      // ALL elements (so a <span>/<div> label is reachable — not just <a>/<button>);
      // else the interactive whitelist.
      `var base;try{base=sel?document.querySelectorAll(sel):` +
      `(text?document.body.querySelectorAll("*"):` +
      `document.querySelectorAll("a,button,input,select,textarea,[role],[onclick],[tabindex]"));}` +
      `catch(x){return {error:"invalid CSS selector "+JSON.stringify(sel)+": "+x.message+` +
      `" (use standard CSS or text/role/name — NOT Playwright :has-text())"};}` +
      `var arr=Array.prototype.slice.call(base).filter(function(e){return !SKIP[e.tagName];});` +
      `function tx(e){return (e.innerText||e.value||e.getAttribute("aria-label")||e.textContent||"").trim();}` +
      // Text: keep elements whose text contains it AND whose children do NOT (the
      // INNERMOST element bearing the text — e.g. the <span> label, not its wrapper).
      `if(text){var tl=text.toLowerCase();arr=arr.filter(function(e){` +
      `if(tx(e).toLowerCase().indexOf(tl)<0)return false;` +
      `for(var i=0;i<e.children.length;i++){if((e.children[i].textContent||"").toLowerCase().indexOf(tl)>=0)return false;}` +
      `return true;});}` +
      `if(name){var nl=name.toLowerCase();arr=arr.filter(function(e){return tx(e).toLowerCase()===nl||(e.getAttribute("aria-label")||"").toLowerCase()===nl;});}` +
      `if(role){arr=arr.filter(function(e){return (e.getAttribute("role")||e.tagName.toLowerCase())===role;});}` +
      `function css(e){try{return globalThis.__skeinixFinder(e);}catch(x){return "";}}` +
      `var prefix=${JSON.stringify(prefix)};` +
      `return {handles:arr.slice(0,50).map(function(e,i){var b=e.getBoundingClientRect();var handle=prefix+i;` +
      `e.setAttribute("data-skeinix-h",handle);return {handle:handle,css:css(e),bbox:[b.x,b.y,b.width,b.height],` +
      `role:e.getAttribute("role")||e.tagName.toLowerCase(),tag:e.tagName.toLowerCase(),name:tx(e).slice(0,80)};})};})()`;
    const r = await sm.send(t, "Runtime.evaluate", {
      expression: expr,
      returnByValue: true,
    });
    const v = r?.result?.value || {};
    if (v.error) return { ok: false, error: String(v.error) };
    return ok({ handles: v.handles || [], count: (v.handles || []).length });
  },
  [CMD.GET_ATTRIBUTE]: async (sm, _o, t, a) => {
    const r = await sm.send(t, "Runtime.evaluate", {
      expression:
        `(function(){var h=${JSON.stringify(String(a.handle || ""))};` +
        `var sel=${JSON.stringify(String(a.selector || ""))};` +
        `var name=${JSON.stringify(String(a.name || ""))};` +
        `var e=h?document.querySelector('[data-skeinix-h="'+h+'"]'):null;` +
        `if(!e&&sel)e=document.querySelector(sel);` +
        `if(!e)return {found:false,value:null,attributes:null};` +
        `if(name)return {found:true,name:name,value:e.getAttribute(name)};` +
        `var attrs={};for(var i=0;i<e.attributes.length;i++){var at=e.attributes[i];attrs[at.name]=at.value;}` +
        `return {found:true,value:attrs,attributes:attrs};})()`,
      returnByValue: true,
    });
    const value = r?.result?.value;
    if (value && typeof value === "object" && "found" in value) {
      return ok(value as Record<string, unknown>);
    }
    return ok({ found: value != null, value: value ?? null });
  },
  [CMD.GET_HTML]: async (sm, _o, t, a) => {
    // Read an element's content (default = the page <body> when no handle/selector)
    // in `format`: "html" (outerHTML), "markdown" (Turndown — compact, ~5-10x
    // smaller), or "text" (innerText). ALWAYS capped at `max_chars`;
    // `truncated`/`full_chars` flag a clip.
    const fmt = String(a.format || "html");
    const max = Number(a.max_chars ?? 12000);
    const resolve =
      `var h=${JSON.stringify(a.handle || "")},sel=${JSON.stringify(a.selector || "")};` +
      `var e=h?document.querySelector('[data-skeinix-h="'+h+'"]'):(sel?document.querySelector(sel):document.body);`;
    let expr: string;
    if (fmt === "markdown") {
      // Inject Turndown's UMD into the page ONCE (guarded), then convert there.
      const has = await sm.send(t, "Runtime.evaluate", {
        expression: "typeof window.TurndownService",
        returnByValue: true,
      });
      if (has?.result?.value !== "function") {
        await sm.send(t, "Runtime.evaluate", { expression: TURNDOWN_UMD });
      }
      expr =
        `(function(){${resolve}if(!e)return {error:"no element matched the handle/selector"};` +
        `return (${TURNDOWN_CONVERT})(e,${max});})()`;
    } else {
      expr =
        `(function(){${resolve}if(!e)return {error:"no element matched the handle/selector"};` +
        `var v=${fmt === "text" ? "e.innerText||\"\"" : "e.outerHTML||\"\""};` +
        `return {value:v.slice(0,${max}),format:${JSON.stringify(fmt)},truncated:v.length>${max},full_chars:v.length};})()`;
    }
    const r = await sm.send(t, "Runtime.evaluate", { expression: expr, returnByValue: true });
    const v = r?.result?.value || {};
    if (v.error) return { ok: false, error: String(v.error) };
    return ok(v);
  },
  [CMD.SCREENSHOT]: async (sm, _o, t, a) => {
    // `full_page` captures the WHOLE scrollable document (not just the viewport)
    // by clipping to its full size + captureBeyondViewport. (Previously ignored.)
    const params: Record<string, unknown> = { format: "png" };
    if (a.full_page) {
      const r = await sm.send(t, "Runtime.evaluate", {
        expression:
          `(function(){var d=document.documentElement,b=document.body;` +
          `return {w:Math.max(d.scrollWidth,b?b.scrollWidth:0,d.clientWidth),` +
          `h:Math.max(d.scrollHeight,b?b.scrollHeight:0,d.clientHeight)};})()`,
        returnByValue: true,
      });
      const v = (r?.result?.value || {}) as { w?: number; h?: number };
      if (v.w && v.h) {
        params.clip = { x: 0, y: 0, width: v.w, height: v.h, scale: 1 };
        params.captureBeyondViewport = true;
      }
    }
    const r = await sm.send(t, "Page.captureScreenshot", params);
    return ok({
      media: [{ slot: "screenshot", b64: r.data, ext: "png", mime: "image/png" }],
    });
  },
  [CMD.GET_IMAGE]: async (sm, _o, t, a) => {
    // Capture JUST the element named by `handle` (clip to its box), not the whole
    // viewport. (Previously the handle was ignored → it screenshotted the page.)
    const h = String(a.handle || "");
    const params: Record<string, unknown> = { format: "png" };
    if (h) {
      const r = await sm.send(t, "Runtime.evaluate", {
        expression:
          `(function(){var h=${JSON.stringify(h)};` +
          `var e=document.querySelector('[data-skeinix-h="'+h+'"]');if(!e)return null;` +
          `e.scrollIntoView({block:"center",inline:"center"});var b=e.getBoundingClientRect();` +
          `return {x:b.left+window.scrollX,y:b.top+window.scrollY,width:b.width,height:b.height};})()`,
        returnByValue: true,
      });
      const v = r?.result?.value as
        | { x: number; y: number; width: number; height: number }
        | null;
      if (!v) {
        return {
          ok: false,
          error:
            "no element matched the handle — re-snapshot or query for a fresh one",
        };
      }
      if (v.width < 1 || v.height < 1) {
        return { ok: false, error: "element has zero size — nothing to capture" };
      }
      params.clip = { x: v.x, y: v.y, width: v.width, height: v.height, scale: 1 };
      params.captureBeyondViewport = true;
    }
    const r = await sm.send(t, "Page.captureScreenshot", params);
    return ok({
      media: [{ slot: "image", b64: r.data, ext: "png", mime: "image/png" }],
    });
  },
  [CMD.FETCH_RESOURCE]: async (sm, _o, t, a) => {
    // Fetch a resource from the live browser and return bytes via the media pipeline.
    // All fetching runs in the PAGE context via Runtime.evaluate so the user's session
    // cookies/session credentials are included automatically — no SW-side fetch
    // and no backend credential handling.
    //
    // First discover bytes or a URL from the element, then fetch with a
    // streaming byte cap.
    // blob: URLs are page-local and must also be fetched from page context.
    const maxBytes = Number(a.max_bytes ?? 52_428_800); // 50 MB default
    const typeHint = String(a.type || "auto");
    const handle = String(a.handle || "");
    const selector = String(a.selector || "");
    const directUrl = String(a.url || "");

    if (!handle && !selector && !directUrl) {
      return { ok: false, error: "provide at least one of: handle, selector, or url" };
    }

    // ── Element inspection ───────────────────────────────────────────────────
    let resolvedUrl = directUrl;
    let hintMime = "";
    let elementTag: string | null = null;

    if (handle || selector) {
      const inspectExpr =
        `(function(){` +
        `var h=${JSON.stringify(handle)},sel=${JSON.stringify(selector)};` +
        `var e=h?document.querySelector('[data-skeinix-h="'+h+'"]')` +
        `:(sel?document.querySelector(sel):null);` +
        `if(!e)return{error:"element not found"};` +
        `var tag=e.tagName.toLowerCase(),hint=${JSON.stringify(typeHint)};` +
        // <canvas> → capture pixels (may fail for CORS-tainted canvas)
        `if(tag==="canvas"){try{var d=e.toDataURL("image/png");` +
        `return{kind:"direct",b64:d.split(",")[1],mime:"image/png",ext:"png",` +
        `resource_type:"image",element_tag:"canvas"};}` +
        `catch(err){return{error:"canvas is tainted by cross-origin content: "+err.message};}}` +
        // <svg> → serialise markup
        `if(tag==="svg"){var s=new XMLSerializer().serializeToString(e);` +
        `return{kind:"direct_text",text:s,mime:"image/svg+xml",ext:"svg",` +
        `resource_type:"image",element_tag:"svg"};}` +
        // <img> → src URL or inline data URI
        `if(tag==="img"){var src=e.currentSrc||e.getAttribute("src")||"";` +
        `if(src.indexOf("data:")===0){var p=src.split(",");` +
        `var m=p[0].replace("data:","").replace(";base64","");` +
        `return{kind:"direct",b64:p[1],mime:m,ext:m.split("/")[1]||"bin",` +
        `resource_type:"image",element_tag:"img"};}` +
        `return{kind:"url",url:new URL(src,document.baseURI).href,` +
        `hint_mime:"image/*",element_tag:"img"};}` +
        // <video> / <audio>
        `if(tag==="video"||tag==="audio"){` +
        `var src=e.currentSrc||e.getAttribute("src")||` +
        `(e.querySelector("source")?e.querySelector("source").getAttribute("src"):"")||"";` +
        `var resolved=src?new URL(src,document.baseURI).href:"";` +
        `if(!resolved)return{error:"no src found on <"+tag+">"};` +
        // HLS/DASH → stream manifest (fetch text from page context below)
        `if(/\.(m3u8|mpd)(\?|$)/i.test(resolved))` +
        `return{kind:"stream_url",url:resolved,element_tag:tag};` +
        `return{kind:"url",url:resolved,hint_mime:tag==="video"?"video/*":"audio/*",element_tag:tag};}` +
        // <a> / <link> / <script> / <iframe>
        `if(["a","link","script","iframe"].indexOf(tag)>=0){` +
        `var href=e.getAttribute("href")||e.getAttribute("src")||"";` +
        `return{kind:"url",url:href?new URL(href,document.baseURI).href:"",` +
        `hint_mime:"application/octet-stream",element_tag:tag};}` +
        // Generic element: text or CSS background-image
        `if(hint==="text"||hint==="auto"){var txt=(e.innerText||e.textContent||"").trim();` +
        `if(txt)return{kind:"direct_text",text:txt,mime:"text/plain",ext:"txt",` +
        `resource_type:"text",element_tag:tag};}` +
        `var bg=getComputedStyle(e).backgroundImage;` +
        `var m=bg.match(/url\(['"]?(.*?)['"]?\)/);` +
        `if(m&&m[1])return{kind:"url",url:new URL(m[1],document.baseURI).href,` +
        `hint_mime:"image/*",element_tag:tag};` +
        `return{error:"no resource found on <"+tag+">; try a child <img> or <video> element"};` +
        `})()`;

      const ir = await sm.send(t, "Runtime.evaluate", {
        expression: inspectExpr,
        returnByValue: true,
      });
      const iv = (ir?.result?.value || {}) as Record<string, unknown>;

      if (iv.error) return { ok: false, error: String(iv.error) };
      elementTag = iv.element_tag ? String(iv.element_tag) : null;

      if (iv.kind === "direct") {
        // Inline bytes already encoded (canvas toDataURL, img data URI)
        const mime = String(iv.mime || "application/octet-stream");
        const ext = String(iv.ext || "bin");
        return {
          ok: true,
          resource_type: String(iv.resource_type || "binary"),
          source_url: null, element_tag: elementTag, mime,
          media: [{ slot: "resource", b64: iv.b64, ext, mime }],
        };
      }

      if (iv.kind === "direct_text") {
        // SVG markup or innerText — encode to base64 in page context
        const encodeExpr =
          `(function(){var t=${JSON.stringify(iv.text)};` +
          // encodeURIComponent handles UTF-8; unescape converts to Latin1 for btoa
          `return btoa(unescape(encodeURIComponent(t)));})()`;
        const er = await sm.send(t, "Runtime.evaluate", {
          expression: encodeExpr,
          returnByValue: true,
        });
        const b64 = String(er?.result?.value || "");
        const mime = String(iv.mime || "text/plain");
        const ext = String(iv.ext || "txt");
        return {
          ok: true,
          resource_type: String(iv.resource_type || "text"),
          source_url: null, element_tag: elementTag, mime,
          media: [{ slot: "resource", b64, ext, mime }],
        };
      }

      if (iv.kind === "stream_url") {
        // Streaming manifest (HLS/DASH) — fetch text from page context, return as text
        const mUrl = String(iv.url || "");
        const isM3u8 = /\.m3u8(\?|$)/i.test(mUrl);
        const mime = isM3u8 ? "application/x-mpegurl" : "application/dash+xml";
        const ext = isM3u8 ? "m3u8" : "mpd";
        const fetchExpr =
          `(async function(){try{` +
          `var r=await fetch(${JSON.stringify(mUrl)},{credentials:"include"});` +
          `var text=await r.text();` +
          `return{ok:true,b64:btoa(unescape(encodeURIComponent(text)))};` +
          `}catch(e){return{ok:false,error:e.message};}})()`;
        const mr = await sm.send(t, "Runtime.evaluate", {
          expression: fetchExpr,
          returnByValue: true,
          awaitPromise: true,
        });
        const mv = (mr?.result?.value || {}) as Record<string, unknown>;
        if (!mv.ok) return { ok: false, error: `manifest fetch failed: ${mv.error}` };
        return {
          ok: true,
          resource_type: "stream",
          source_url: mUrl, element_tag: elementTag, mime,
          media: [{ slot: "resource", b64: mv.b64, ext, mime }],
        };
      }

      if (iv.kind === "url") {
        resolvedUrl = String(iv.url || "");
        hintMime = String(iv.hint_mime || "");
        if (!resolvedUrl) return { ok: false, error: "could not resolve a URL from the element" };
      }
    }

    // ── Page-context URL fetch (preserves user credentials) ──────────────────
    // Uses fetch() inside the page so the browser's cookie jar is included.
    // Cross-origin requests succeed when the server allows it (most CDNs do for
    // images; CORS-restricted private APIs may fail — the error is returned as-is).
    // blob: URLs are page-local and must be fetched here too (not from the SW).
    const fetchExpr =
      `(async function(){` +
      `var url=${JSON.stringify(resolvedUrl)},maxB=${maxBytes};` +
      `try{` +
      `var resp=await fetch(url,{credentials:"include"});` +
      `if(!resp.ok)return{ok:false,error:"HTTP "+resp.status+" "+resp.statusText};` +
      `var mime=(resp.headers.get("content-type")||"application/octet-stream").split(";")[0].trim();` +
      `var cl=resp.headers.get("content-length");` +
      `var fullLen=cl?parseInt(cl,10):null;` +
      `var reader=resp.body.getReader();` +
      `var chunks=[],total=0,truncated=false;` +
      `for(;;){var chunk=await reader.read();if(chunk.done)break;` +
      `if(total+chunk.value.length>maxB){chunks.push(chunk.value.slice(0,maxB-total));` +
      `total=maxB;truncated=true;reader.cancel();break;}` +
      `chunks.push(chunk.value);total+=chunk.value.length;}` +
      `var out=new Uint8Array(total),off=0;` +
      `for(var i=0;i<chunks.length;i++){out.set(chunks[i],off);off+=chunks[i].length;}` +
      // Build latin1 string for btoa without quadratic string concat
      `var chars=Array.from(out,function(b){return String.fromCharCode(b);});` +
      `return{ok:true,b64:btoa(chars.join("")),mime:mime,` +
      `truncated:truncated,full_content_length:fullLen};` +
      `}catch(e){return{ok:false,error:e.message};}` +
      `})()`;

    const fr = await sm.send(t, "Runtime.evaluate", {
      expression: fetchExpr,
      returnByValue: true,
      awaitPromise: true,
    });
    const fv = (fr?.result?.value || {}) as Record<string, unknown>;
    if (!fv.ok) return { ok: false, error: `fetch failed: ${fv.error}` };

    const mime = String(fv.mime || hintMime || "application/octet-stream");
    // Infer resource_type from MIME
    const resourceType =
      mime.startsWith("text/") || ["application/json","application/xml","application/javascript"].some(m => mime.startsWith(m))
        ? "text"
        : mime.startsWith("image/") ? "image"
        : mime.startsWith("video/") ? "video"
        : mime.startsWith("audio/") ? "audio"
        : "binary";
    // Derive file extension from MIME type
    const EXT_MAP: Record<string, string> = {
      "image/jpeg": "jpg", "image/png": "png", "image/gif": "gif",
      "image/webp": "webp", "image/svg+xml": "svg", "image/avif": "avif",
      "video/mp4": "mp4", "video/webm": "webm", "audio/mpeg": "mp3",
      "audio/ogg": "ogg", "text/html": "html", "text/plain": "txt",
      "text/css": "css", "application/json": "json", "application/pdf": "pdf",
      "application/zip": "zip",
    };
    const ext = EXT_MAP[mime] || mime.split("/")[1]?.replace(/[^a-z0-9]/g, "") || "bin";

    return {
      ok: true,
      resource_type: resourceType,
      source_url: resolvedUrl, element_tag: elementTag, mime,
      truncated: !!fv.truncated,
      full_content_length: fv.full_content_length ?? null,
      media: [{ slot: "resource", b64: fv.b64, ext, mime }],
    };
  },
  [CMD.ACQUIRE_VIDEO]: async (sm, _o, t, a) => {
    // Sample video frames through element screenshots. Native-byte extraction
    // and DRM-protected media remain outside this command's capability boundary.
    const maxFrames = Number(a.max_frames ?? 8);
    const media: Record<string, unknown>[] = [];
    for (let i = 0; i < maxFrames; i++) {
      const r = await sm.send(t, "Page.captureScreenshot", {
        format: "jpeg",
        quality: 60,
      });
      media.push({ slot: "frames", b64: r.data, ext: "jpg", mime: "image/jpeg" });
    }
    return ok({ tier: "frames", meta: { n_frames: media.length }, media });
  },
  [CMD.SCROLL]: async (sm, _o, t, a) => {
    // With a `handle`, scroll THAT element into view (centered); otherwise page
    // the viewport down ~90%. (Earlier this only ever did a fixed scrollBy and
    // ignored the handle the tool sends — scroll-to-element never worked.)
    const h = String(a.handle || "");
    const r = await sm.send(t, "Runtime.evaluate", {
      expression: h
        ? `(function(){var h=${JSON.stringify(h)};` +
          `var e=document.querySelector('[data-skeinix-h="'+h+'"]');` +
          `if(!e)return false;e.scrollIntoView({block:"center",inline:"center"});return true;})()`
        : `(window.scrollBy(0,Math.round(window.innerHeight*0.9)),true)`,
      returnByValue: true,
    });
    if (h && r?.result?.value !== true) {
      return {
        ok: false,
        error:
          "no element matched the handle — re-snapshot or query for a fresh one",
      };
    }
    return ok();
  },
  [CMD.WAIT_FOR]: async (sm, _o, t, a) => {
    const timeout = boundedCommandWaitMs(a.timeout, 8000);
    const deadline = Date.now() + timeout;
    // Two modes: a CSS `selector` (default), or visible `text` (a substring of the
    // page's rendered text — robust when you don't know the exact tag/attributes,
    // e.g. a search box that's a <textarea> not an <input>). `condition` is the
    // legacy single field, treated as a selector.
    const text = a.text ? String(a.text) : "";
    const selRaw = a.selector || a.condition || "";
    const sel = String(selRaw);
    let expr: string;
    if (text && !selRaw) {
      const ndl = JSON.stringify(text.toLowerCase());
      expr =
        `(function(){return (((document.body&&document.body.innerText)||"")` +
        `.toLowerCase().indexOf(${ndl})>=0);})()`;
    } else {
      // A syntactically INVALID selector (e.g. Playwright :has-text()) returns a
      // marker so we fail FAST with a clear message instead of polling uselessly
      // until the timeout.
      expr =
        `(function(){try{return !!document.querySelector(${JSON.stringify(sel || "body")});}` +
        `catch(x){return {__invalid:String((x&&x.message)||x)};}})()`;
    }
    let invalid: string | null = null;
    while (Date.now() < deadline) {
      const r = await sm.send(t, "Runtime.evaluate", {
        expression: expr,
        returnByValue: true,
      });
      const v = r?.result?.value;
      if (v === true) return ok();
      if (v && typeof v === "object" && (v as any).__invalid) {
        invalid = String((v as any).__invalid);
        break;
      }
      await new Promise((res) => setTimeout(res, 150));
    }
    if (invalid) {
      return {
        ok: false,
        error: `wait_for: invalid CSS selector ${JSON.stringify(sel)} (${invalid}) — pass text= to wait by visible text, or use standard CSS`,
      };
    }
    const what = text ? `text ${JSON.stringify(text)}` : `selector ${JSON.stringify(sel)}`;
    return { ok: false, error: `wait_for timeout: ${what} did not appear within ${timeout}ms` };
  },
  [CMD.LIST_TABS]: async (sm, _o, t, a) => {
    // "ls" for the controlled SESSION (§9 session-of-tabs): the root tab plus any
    // excursion tabs the session auto-attached — NOT the user's unrelated tabs
    // (which the agent can't control and shouldn't see, §3/§15). Each tab is keyed
    // by its STABLE `tab` (tabId) and live-queried for its current url+title (the
    // cached url goes stale after navigation). `active` marks the current tab.
    const currentTab = sm.tabIdFor(t);
    const seen = new Set<number>();
    const tabs: Record<string, unknown>[] = [];
    for (const id of sm.knownTargets()) {
      const tab = sm.tabIdFor(id);
      if (tab === undefined || seen.has(tab)) continue;
      seen.add(tab);
      let url = "";
      let title = "";
      try {
        const r = await sm.send(id, "Runtime.evaluate", {
          expression: "JSON.stringify({u:location.href,t:document.title})",
          returnByValue: true,
        });
        const info = JSON.parse(r?.result?.value || "{}");
        url = String(info.u || "");
        title = String(info.t || "");
      } catch {
        // target vanished between knownTargets() and the query — list it bare
      }
      tabs.push({ tab, url, title, active: tab === currentTab });
    }
    const health = await sm.health(await browserWindowTabIds(a));
    return ok({
      tabs,
      count: tabs.length,
      controlled: tabs.length > 0,
      health,
    });
  },
  [CMD.LIST_OPEN_TABS]: async (sm, _o, _t, a) => {
    // The user's OWN open tabs (NOT the controlled session) — so the agent can
    // adopt one the user already has open. Skips internal pages it can't drive
    // (chrome://, the extension's own pages). `tab` is the real chrome tabId to
    // pass to the "use" action.
    const windowId =
      typeof a.browser_window_id === "number"
        ? a.browser_window_id
        : typeof a.browser_window_id === "string" && a.browser_window_id
          ? Number(a.browser_window_id)
          : undefined;
    const all = await chrome.tabs.query(
      Number.isFinite(windowId) ? { windowId } : {},
    );
    const tabs = all
      .filter(
        (x) =>
          typeof x.id === "number" &&
          !!x.url &&
          /^https?:|^file:/.test(x.url),
      )
      .map((x) => ({
        tab: x.id as number,
        url: x.url ?? "",
        title: x.title ?? "",
        active: !!x.active,
      }));
    const health = await sm.health(tabs.map((tab) => tab.tab));
    return ok({ tabs, count: tabs.length, health });
  },
  [CMD.USE_TAB]: async (sm, _o, _t, a) => {
    // Adopt the user's EXISTING tab as the controlled root: attach the debugger
    // to it (the "Skeinix is debugging this tab" banner appears → control is
    // visible) and remember it so a SW restart re-attaches to the same tab. The
    // service-worker refreshes the island after the command (knownTargets>0).
    const tab = a.tab != null && a.tab !== "" ? Number(a.tab) : undefined;
    if (tab === undefined) {
      return { ok: false, error: "use requires a tab id (from list_open)" };
    }
    const expectedWindowId =
      typeof a.browser_window_id === "number"
        ? a.browser_window_id
        : typeof a.browser_window_id === "string" && a.browser_window_id
          ? Number(a.browser_window_id)
          : undefined;
    if (Number.isFinite(expectedWindowId)) {
      try {
        const tabInfo = await chrome.tabs.get(tab);
        if (tabInfo.windowId !== expectedWindowId) {
          return {
            ok: false,
            error_code: "tab_out_of_scope",
            error: "the requested tab is not in this side panel's browser window",
            not_executed: true,
          };
        }
      } catch {
        return {
          ok: false,
          error_code: "tab_not_found",
          error: `tab ${tab} was not found`,
          not_executed: true,
        };
      }
    }
    try {
      const targetId = await sm.attachRoot(tab);
      try {
        await chrome.storage.session.set({ controlledTabId: tab });
      } catch {
        /* storage best-effort — attach already succeeded */
      }
      return ok({ tab, target_id: targetId, controlled: true });
    } catch (e) {
      const message = String((e as Error)?.message || e);
      const debuggerConflict = /another debugger|already attached/i.test(message);
      const health = await sm.health([tab]).catch(() => undefined);
      return {
        ok: false,
        error_code: debuggerConflict
          ? "browser_debugger_conflict"
          : "browser_command_not_executed",
        error: `could not take control of tab ${tab}: ${message}`,
        not_executed: true,
        ...(health ? { health } : {}),
      };
    }
  },
  [CMD.SWITCH_TAB]: async (_sm, _o, _t, a) => {
    // Bring a tab to the FOREGROUND (real OS focus) for visibility-sensitive work.
    // Per-command `tab` targeting means most ops never need this; it is here for
    // pages that pause/behave differently when backgrounded.
    const tab = a.tab != null && a.tab !== "" ? Number(a.tab) : undefined;
    if (tab !== undefined) {
      try {
        await chrome.tabs.update(tab, { active: true });
      } catch {
        /* tab gone — non-fatal */
      }
    }
    return ok({ tab });
  },
  [CMD.CLOSE_TAB]: async (sm, _o, _t, a) => {
    // Close a tab by its stable tabId (never the controlled root — closeExcursion
    // guards that). Empty → no-op.
    const tab = a.tab != null && a.tab !== "" ? Number(a.tab) : undefined;
    const tid = tab !== undefined ? sm.targetForTab(tab) : undefined;
    if (tid) await sm.closeExcursion(tid);
    return ok({ tab });
  },
  [CMD.WAIT_FOR_NEW_TAB]: async (sm, _o, _t, a) => {
    // WAIT for a new (excursion) tab to open AND get its real chrome tabId
    // resolved — opening a sub-tab + auto-attach + id resolution all take time.
    // Returns the newest non-root tab's stable id so the agent can operate on it.
    // (Use after a click that opens a new tab; then pass the returned `tab`.)
    const root = sm.rootTab();
    const deadline = Date.now() + boundedCommandWaitMs(a.timeout, 8000);
    for (;;) {
      const excursions = sm.knownTabs().filter((x) => x.tab !== root);
      if (excursions.length) {
        const newest = excursions[excursions.length - 1];
        return ok({ tab: newest.tab, url: newest.url });
      }
      if (Date.now() >= deadline) break;
      await new Promise((r) => setTimeout(r, 150));
    }
    return { ok: false, error: "no new tab opened within the timeout" };
  },
  [CMD.CLICK]: async (sm, _o, t, a) => {
    // Resolve the element (handle from snapshot/query, else STANDARD CSS), scroll
    // it into view, and return its CENTER viewport coordinates. Reports
    // not-found / bad-selector instead of a silent no-op.
    const loc = await sm.send(t, "Runtime.evaluate", {
      expression:
        `(function(){var h=${JSON.stringify(a.handle || "")},sel=${JSON.stringify(a.selector || "")},e=null;` +
        `if(h)e=document.querySelector('[data-skeinix-h="'+h+'"]');` +
        `if(!e&&sel){try{e=document.querySelector(sel);}catch(x){return {error:"invalid CSS selector "+JSON.stringify(sel)+": "+x.message};}}` +
        `if(!e)return {found:false};` +
        `e.scrollIntoView({block:"center",inline:"center"});` +
        `var b=e.getBoundingClientRect();` +
        `return {found:true,x:b.left+b.width/2,y:b.top+b.height/2,w:b.width,h:b.height};})()`,
      returnByValue: true,
    });
    const v = loc?.result?.value || {};
    if (v.error) return { ok: false, error: String(v.error) };
    if (!v.found) {
      return {
        ok: false,
        error:
          "no element matched the handle/selector — re-snapshot or query " +
          "(by text/role) to get a fresh handle",
      };
    }
    if (!v.w || !v.h) {
      // Zero-size / not laid out (hidden control) — real mouse events can't hit a
      // pixel, so fall back to a synthetic DOM click on the resolved element.
      await sm.send(t, "Runtime.evaluate", {
        expression:
          `(function(){var h=${JSON.stringify(a.handle || "")},sel=${JSON.stringify(a.selector || "")};` +
          `var e=h?document.querySelector('[data-skeinix-h="'+h+'"]'):document.querySelector(sel);` +
          `if(e)e.click();})()`,
      });
    } else {
      // REAL mouse click at the element's center: hover → press → release. Unlike
      // element.click(), this fires the full pointer/mouse sequence at real
      // coordinates, so delegated handlers and span/div "buttons" (pointerdown-
      // based widgets) actually react, and the topmost element at that point gets
      // the event (respects overlays).
      const x = v.x as number;
      const y = v.y as number;
      await sm.send(t, "Input.dispatchMouseEvent", { type: "mouseMoved", x, y, button: "none" });
      await sm.send(t, "Input.dispatchMouseEvent", { type: "mousePressed", x, y, button: "left", clickCount: 1 });
      await sm.send(t, "Input.dispatchMouseEvent", { type: "mouseReleased", x, y, button: "left", clickCount: 1 });
    }
    // A click may trigger navigation — wait for the optional post-condition.
    return settleExpect(sm, t, a);
  },
  [CMD.TYPE]: async (sm, _o, t, a) => {
    // Focus the field first so insertText lands in IT (insertText goes to whatever
    // is focused otherwise).
    await focusHandle(sm, t, a.handle);
    if (a.replace) {
      // OVERWRITE: select the field's existing value so the insertText below
      // replaces it (works for <input>/<textarea>; falls back to clearing value).
      await sm
        .send(t, "Runtime.evaluate", {
          expression:
            `(function(){var e=document.querySelector('[data-skeinix-h=${JSON.stringify(a.handle)}]');` +
            `if(e){if(e.select){e.select();}else{e.value="";}}})()`,
        })
        .catch(() => {});
    }
    await sm.send(t, "Input.insertText", { text: String(a.text ?? "") });
    // Typing can trigger live filtering/navigation — honor the optional `expect`
    // (settleExpect returns immediately when no expect is set).
    return settleExpect(sm, t, a);
  },
  [CMD.FILL]: async (sm, _o, t, a) => {
    await sm.send(t, "Runtime.evaluate", {
      expression:
        `(function(){var e=document.querySelector('[data-skeinix-h=${JSON.stringify(a.handle)}]');` +
        `if(e){e.value=${JSON.stringify(a.text ?? "")};e.dispatchEvent(new Event("input",{bubbles:true}));}})()`,
    });
    return settleExpect(sm, t, a);
  },
  [CMD.SELECT]: async (sm, _o, t, a) => {
    await sm.send(t, "Runtime.evaluate", {
      expression:
        `(function(){var e=document.querySelector('[data-skeinix-h=${JSON.stringify(a.handle)}]');` +
        `if(e){e.value=${JSON.stringify(a.option ?? "")};e.dispatchEvent(new Event("change",{bubbles:true}));}})()`,
    });
    return settleExpect(sm, t, a);
  },
  [CMD.PRESS]: async (sm, _o, t, a) => {
    // Optionally focus a field first (press Enter on the input you just filled),
    // then dispatch a REAL key event (code + windowsVirtualKeyCode + text) so the
    // page actually reacts — e.g. Enter submits, Tab moves focus.
    await focusHandle(sm, t, a.handle);
    const key = String(a.key);
    const d = KEYMAP[key];
    const text = d?.text ?? (key.length === 1 ? key : undefined);
    const base: Record<string, unknown> = { key };
    if (d) {
      base.code = d.code;
      base.windowsVirtualKeyCode = d.keyCode;
      base.nativeVirtualKeyCode = d.keyCode;
    }
    await sm.send(t, "Input.dispatchKeyEvent", {
      type: "keyDown",
      ...base,
      ...(text ? { text } : {}),
    });
    await sm.send(t, "Input.dispatchKeyEvent", { type: "keyUp", ...base });
    // A key (Enter especially) may submit/navigate — honor the optional expect.
    return settleExpect(sm, t, a);
  },
  [CMD.SUBMIT]: async (sm, _o, t, a) => {
    await sm.send(t, "Runtime.evaluate", {
      expression:
        `(function(){var e=document.querySelector('[data-skeinix-h=${JSON.stringify(a.handle)}]')` +
        `||document.querySelector("form");if(e){var f=e.form||e;if(f.submit){f.submit();}else{e.click();}}})()`,
    });
    // A submit usually navigates — wait for the optional post-condition.
    return settleExpect(sm, t, a);
  },
  [CMD.ASSERT]: async (sm, _o, t, a) => {
    const r = await sm.send(t, "Runtime.evaluate", {
      expression: `!!document.querySelector(${JSON.stringify(a.condition || "body")})`,
      returnByValue: true,
    });
    return {
      ok: true,
      satisfied: !!r?.result?.value,
      observed: r?.result?.value ?? null,
    };
  },
  [CMD.HIGHLIGHT]: async (_s, ov, t, a) => {
    await ov.highlight(t, String(a.selector || ""), String(a.label || ""));
    return ok();
  },
  [CMD.NARRATE]: async (_s, ov, t, a) => {
    await ov.narrate(t, String(a.text || ""));
    return ok();
  },
  [CMD.CHECK_LOGIN]: async (sm, _o, t, a) => {
    // deterministic heuristic; NEVER reads credentials (§15)
    const sel = JSON.stringify(
      a.logged_in_selector || "[data-logged-in], .user-avatar",
    );
    const r = await sm.send(t, "Runtime.evaluate", {
      expression: `!!document.querySelector(${sel})`,
      returnByValue: true,
    });
    return ok({ logged_in: !!r?.result?.value });
  },
  // ensureAttached (SW) runs BEFORE routeCommand and already opens+attaches a
  // fresh controlled tab when none is controlled, so START_SESSION just needs to
  // confirm control is live; the side effect (open+attach) happens upstream.
  [CMD.START_SESSION]: async (_sm, _o, _t, a) =>
    ok({
      started: true,
      browser_session_id: typeof a.browser_session_id === "string" ? a.browser_session_id : "",
      session_generation:
        typeof a.session_generation === "number"
          ? a.session_generation
          : typeof a.session_generation === "string"
            ? Number(a.session_generation)
            : 0,
    }),
  [CMD.END_SESSION]: async (sm) => {
    const tabs = new Set<number>();
    for (const targetId of sm.knownTargets()) {
      const tabId = sm.tabIdFor(targetId);
      if (typeof tabId === "number") tabs.add(tabId);
    }
    for (const tabId of tabs) {
      try {
        await chrome.debugger.detach({ tabId });
      } catch {
        // Already detached or closed; release is best-effort for every target.
      }
    }
    await chrome.storage.session.remove(["controlledTabId", "controlledTabIds"]);
    return ok({ released: true, released_tabs: [...tabs] });
  },
};

export async function dispatch(
  cmd: Cmd,
  sm: SessionManager,
  ov: Overlay,
  targetId: string,
  args: Record<string, unknown>,
): Promise<Record<string, unknown>> {
  const h = HANDLERS[cmd];
  if (!h) throw new Error(`no bundled handler for ${cmd}`); // §6: never run non-bundled code
  return h(sm, ov, targetId, args);
}
