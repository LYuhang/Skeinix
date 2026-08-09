// @vitest-environment jsdom
import { describe, it, expect } from "vitest";
import { pruneDom, OVERLAY_HOST_ID, PRUNE_FN_SOURCE } from "./prune";

describe("bundled DOM-prune", () => {
  it("excludes the extension's own overlay host (C2)", () => {
    document.body.innerHTML = `
      <main><h1>Title</h1><button id="ok">OK</button></main>
      <div id="${OVERLAY_HOST_ID}"><span>SECRET OVERLAY TEXT</span></div>
      <script>var x=1</script><style>.a{}</style>`;
    const t = pruneDom(document.body.outerHTML);
    expect(t.text).toContain("Title");
    expect(t.text).toContain("OK");
    expect(t.text).not.toContain("SECRET OVERLAY TEXT"); // overlay never leaks
  });
  it("drops script/style/noscript nodes", () => {
    document.body.innerHTML = `<p>keep</p><script>var x=1</script>`;
    const t = pruneDom(document.body.outerHTML);
    expect(t.text).toContain("keep");
    expect(t.text).not.toContain("var x=1");
  });
  it("PRUNE_FN_SOURCE is a constant string with no template interpolation (no-remote-code)", () => {
    // the injected source must be a fixed literal — never built from backend data
    expect(typeof PRUNE_FN_SOURCE).toBe("string");
    expect(PRUNE_FN_SOURCE).toContain(OVERLAY_HOST_ID);
    expect(PRUNE_FN_SOURCE).not.toContain("${"); // no interpolation
  });
});
