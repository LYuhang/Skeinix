// Fixed, bundle-local DOM-prune (§5.2, §6). Runs CLIENT-SIDE before egress so
// only the trimmed tree leaves the machine (minimal egress, §7) and the
// extension's own Dynamic Island (closed Shadow DOM host) is NEVER fed back to the
// agent (C2). The injected source is a CONSTANT — no backend data ever enters it.
export const OVERLAY_HOST_ID = "skeinix-island-host";

export type PrunedTree = {
  text: string;
  nodes: { handle: string; role: string; name: string; selector: string }[];
};

// Pure walker, shared by the test path (jsdom) and the injected source.
function _prune(root: Element, includeOverlay: boolean, prefix = "h"): PrunedTree {
  const SKIP = new Set(["SCRIPT", "STYLE", "NOSCRIPT", "TEMPLATE"]);
  const out: PrunedTree = { text: "", nodes: [] };
  let h = 0;
  const walk = (el: Element) => {
    if (SKIP.has(el.tagName)) return;
    if (!includeOverlay && el.id === OVERLAY_HOST_ID) return; // C2: exclude overlay
    const tag = el.tagName.toLowerCase();
    if (
      ["a", "button", "input", "select", "textarea", "h1", "h2", "h3"].includes(
        tag,
      )
    ) {
      const name = (el.getAttribute("aria-label") || el.textContent || "")
        .trim()
        .slice(0, 80);
      // Stamp a per-read handle so snapshot → handle → click works directly
      // without reusing h0/q0 across independent reads.
      const handle = `${prefix}${h++}`;
      el.setAttribute("data-skeinix-h", handle);
      out.nodes.push({
        handle,
        role: el.getAttribute("role") || tag,
        name,
        selector: el.id ? `#${el.id}` : tag,
      });
    }
    for (const c of Array.from(el.children)) walk(c);
    const own = Array.from(el.childNodes)
      .filter((n) => n.nodeType === 3)
      .map((n) => (n.textContent || "").trim())
      .filter(Boolean)
      .join(" ");
    if (own) out.text += own + "\n";
  };
  walk(root);
  out.text = out.text.trim();
  return out;
}

export function pruneDom(
  _html: string,
  opts?: { includeOverlay?: boolean },
): PrunedTree {
  // test path: parse provided html into a jsdom document body
  const doc = new DOMParser().parseFromString(_html, "text/html");
  return _prune(doc.body, opts?.includeOverlay ?? false);
}

// The CONSTANT source injected via CDP Runtime.evaluate (`(${SRC})()` — but the
// SRC itself is a fixed literal; backend data never enters it). OVERLAY_HOST_ID
// is inlined as a literal so the shipped source contains no `${` interpolation.
export const PRUNE_FN_SOURCE = `function(prefix,scope,doPrune){
  prefix=prefix||"h";
  scope=scope||"";
  doPrune=doPrune!==false;
  var OVERLAY_HOST_ID="skeinix-island-host";
  var SKIP={SCRIPT:1,STYLE:1,NOSCRIPT:1,TEMPLATE:1};
  var out={text:"",nodes:[]};
  var h=0;
  var root=scope?document.querySelector(scope):document.body;
  if(!root)return {text:"",nodes:[],error:"scope_not_found"};
  function walk(el){
    if(SKIP[el.tagName])return;
    if(el.id===OVERLAY_HOST_ID)return;
    if(doPrune){
      var style=globalThis.getComputedStyle?globalThis.getComputedStyle(el):null;
      if(style&&(style.display==="none"||style.visibility==="hidden"))return;
      var rect=el.getBoundingClientRect?el.getBoundingClientRect():null;
      if(rect&&rect.width===0&&rect.height===0&&el.children.length===0)return;
    }
    var tag=el.tagName.toLowerCase();
    if(["a","button","input","select","textarea","h1","h2","h3"].indexOf(tag)>=0){
      var name=((el.getAttribute("aria-label")||el.getAttribute("placeholder")||el.getAttribute("value")||el.textContent||"")).trim().slice(0,80);
      var handle=prefix+(h++);
      el.setAttribute("data-skeinix-h",handle);
      out.nodes.push({handle:handle,role:el.getAttribute("role")||tag,name:name,selector:el.id?("#"+el.id):tag});
    }
    for(var i=0;i<el.children.length;i++)walk(el.children[i]);
    var own=Array.prototype.filter.call(el.childNodes,function(n){return n.nodeType===3;})
      .map(function(n){return (n.textContent||"").trim();}).filter(Boolean).join(" ");
    if(own)out.text+=own+"\\n";
  }
  walk(root);
  out.text=out.text.trim();
  return out;
}`;
