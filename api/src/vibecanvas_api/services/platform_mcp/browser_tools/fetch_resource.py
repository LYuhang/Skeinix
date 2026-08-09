"""Platform MCP browser_fetch_resource — save a live browser resource to VFS.

BYTE-TRANSPORT PIPELINE (same as SCREENSHOT / GET_IMAGE, §5.3)
--------------------------------------------------------------
  extension → bytes + mime → media slot "resource" (base64 in observation)
  host      → write_observation_media → /data/browser-media/<hash>.<ext>
  tool      → obs.data["resource"] = VFS path  (never raw bytes in context)

EXTENSION CONTRACT (Cmd.FETCH_RESOURCE)
---------------------------------------
Input args: { handle?, selector?, url?, type?, max_bytes?, save_path?, tab? }

DOM discovery stage (content script):
  Inspect element to locate bytes or a URL to fetch:

  <img>               el.currentSrc / el.src
                      data: URI          → decode inline (no network)
  <video> / <audio>   el.currentSrc or <source src>
                      .m3u8 / .mpd       → fetch manifest text only;
                                           set resource_type = "stream"
                      blob: URL          → fetch() in content-script context
  <canvas>            el.toDataURL()     → PNG bytes inline (no network)
                      (if CORS-tainted, the browser observation is converted to
                       ToolError and @tool_output emits the standard error envelope)
  <svg> inline        XMLSerializer      → SVG bytes inline (no network)
  <a><link><script><iframe>  el.href / el.src → URL
  generic element     type="text"        → el.innerText inline (no network)
                      else               → getComputedStyle().backgroundImage → URL

Authenticated fetch stage (controlled page context):
  Runtime.evaluate(fetch(url, {credentials:"include"})) with streaming read up
  to max_bytes. The bytes are fetched by the user's browser, not by the backend,
  so browser cookies/session credentials are available. CORS still applies to
  JavaScript fetch; when a site only permits a resource as a rendered subresource
  but blocks JS fetch, the extension returns that browser-side error.

Observation returned by extension (before media pipeline):
  {
    ok: bool,
    data: {
      resource_type: "text"|"image"|"video"|"audio"|"stream"|"binary",
      source_url:    str | null,   # URL fetched; null for inline-only resources
      element_tag:   str | null,   # e.g. "img", "canvas"; null for direct-URL fetch
      mime:          str,
      truncated:     bool,         # true when max_bytes hit
      full_content_length: int | null,   # from Content-Length header
    },
    media: [{ slot:"resource", b64:str, mime:str, ext:str }]
  }

After host pipeline: data["resource"] = "/data/browser-media/<hash>.<ext>"
"""
from __future__ import annotations

from langchain.tools import ToolRuntime
from langchain_core.tools import tool

from vibecanvas_api.agents.tools.decorator import tool_output, ToolError
from vibecanvas_api.agents.tools.render import register_render, Rendered
from vibecanvas_api.services.platform_mcp.browser_tools._common import _run
from vibecanvas_api.browser.media import normalize_browser_media_save_path

_DEFAULT_MAX_MB = 50
_DEFAULT_MAX_BYTES = _DEFAULT_MAX_MB * 1024 * 1024

# MIME prefix → resource category. Order matters (more specific first).
_MIME_CATEGORY: list[tuple[str, str]] = [
    ("application/json",           "text"),
    ("application/xml",            "text"),
    ("application/javascript",     "text"),
    ("application/x-yaml",         "text"),
    ("application/x-mpegurl",      "stream"),   # HLS manifest
    ("application/dash+xml",       "stream"),   # MPEG-DASH manifest
    ("text/",                      "text"),
    ("image/",                     "image"),
    ("video/",                     "video"),
    ("audio/",                     "audio"),
]


def _category_from_mime(mime: str) -> str:
    for prefix, cat in _MIME_CATEGORY:
        if mime.startswith(prefix):
            return cat
    return "binary"


def _human_bytes(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    if n < 1024 ** 2:
        return f"{n / 1024:.1f} KB"
    if n < 1024 ** 3:
        return f"{n / 1024 ** 2:.1f} MB"
    return f"{n / 1024 ** 3:.1f} GB"


@register_render("browser_fetch_resource")
def _render_fetch_resource(raw: dict, ctx) -> Rendered:
    data = raw.get("data") or {}

    path          = data.get("resource") or ""
    resource_type = data.get("resource_type") or ""
    source_url    = data.get("source_url") or ""
    element_tag   = data.get("element_tag") or ""
    mime          = data.get("mime") or "application/octet-stream"
    truncated     = data.get("truncated", False)
    full_len      = data.get("full_content_length")

    # Prefer the extension-reported category; fall back to mime inference.
    category = resource_type or _category_from_mime(mime)

    # Pull authoritative size + mime from the media pipeline entry.
    size: int | None = None
    for m in (data.get("media") or []):
        if m.get("slot") == "resource":
            size = m.get("bytes_len")
            mime = m.get("mime") or mime
            break

    lines = [f"Saved {category} resource to {path}"]
    lines.append(f"  mime:    {mime}")
    if source_url:
        lines.append(f"  url:     {source_url}")
    if element_tag:
        lines.append(f"  element: <{element_tag}>")
    if size is not None:
        size_note = _human_bytes(size)
        if truncated:
            full_note = f" of {_human_bytes(full_len)}" if full_len else ""
            size_note += f"{full_note} — truncated at max_bytes limit"
        lines.append(f"  size:    {size_note}")

    # Streaming-manifest note: make it impossible to miss.
    if category == "stream":
        lines.append(
            "  NOTE: streaming format (HLS/DASH) — manifest text saved, not the full video."
        )

    content = "\n".join(lines)
    abstract = f"browser_fetch_resource → {path or '?'} ({category}, {_human_bytes(size) if size else mime})"
    return Rendered(content=content, content_type="text/plain", abstract=abstract)


@tool_output(content_type="text/plain", tool="browser_fetch_resource")
async def _fetch_resource(
    handle: str, selector: str, url: str,
    type: str, max_bytes: int, save_path: str, tab: int,
    runtime: ToolRuntime,
) -> dict:
    if not handle and not selector and not url:
        raise ToolError(
            "missing_input",
            "provide at least one of: handle, selector (CSS), or url",
        )
    try:
        normalized_save_path = normalize_browser_media_save_path(save_path)
    except ValueError as exc:
        raise ToolError("invalid_save_path", str(exc)) from exc
    return await _run(
        "fetch_resource", runtime.context,
        handle=handle or None,
        selector=selector or None,
        url=url or None,
        type=type or None,
        max_bytes=max_bytes if max_bytes > 0 else _DEFAULT_MAX_BYTES,
        save_path=normalized_save_path,
        tab=tab or None,
    )


@tool(response_format="content_and_artifact")
async def browser_fetch_resource(
    handle: str = "",
    selector: str = "",
    url: str = "",
    type: str = "auto",
    max_bytes: int = 0,
    save_path: str = "",
    tab: int = 0,
    require_user_auth: bool = True,
    approval_reason: str = "",
    *,
    runtime: ToolRuntime,
) -> str:
    """Fetch a resource from the browser and save it as a file.

    The fetch runs inside the user's browser page context with browser-managed
    credentials such as cookies/session state, so many auth-gated resources are
    accessible without backend credentials.
    e.g. save a product photo from a listing page; download a PDF report the user
    has open; capture a chart rendered as a canvas element; fetch the body of an
    article the user is already authenticated to view.

    LOCATING THE RESOURCE — provide one of:
    - `handle`: an element handle identifying the source element. The extension
      inspects the element to locate the resource automatically:
        <img>             → the image at its src URL
        <video> / <audio> → the media at its src URL; streaming manifests (HLS/DASH)
                            are saved as text with resource_type="stream"
        <canvas>          → pixels captured via toDataURL()
        <svg> inline      → the serialised SVG markup
        <a> / <link> / <script> / <iframe> → the resource at its href/src URL
        any other element with type="text"  → the element's visible text
        any other element (default)         → checks for a CSS background image URL
    - `selector`: CSS selector as an alternative to handle.
    - `url`: direct URL, bypassing element inspection.

    `save_path` optionally controls where the backend stores the file. It must be
    an exact path under the current chat workspace `/data/` folder, such as
    `/data/downloads/report.pdf` or `/data/images/product.png`. If omitted, the
    backend uses `/data/browser-media/<content-hash>.<ext>`. Do not use `/run`
    for browser resources; `/run` is reserved for workflow execution files.

    `max_bytes` caps the transfer size (default: 50 MB). If the resource exceeds
    this, the saved file contains what was buffered and the result notes truncation.
    Streaming video (HLS/DASH) saves only the manifest text.

    Args:
        handle: element handle identifying the source element (preferred).
        selector: CSS selector as an alternative to handle.
        url: direct URL to fetch, bypassing element inspection.
        type: resource type hint — "text" | "image" | "video" | "auto" (default).
        max_bytes: byte cap for transfer; 0 = default (50 MB).
        save_path: optional exact `/data/...` destination path in the current chat workspace.
        tab: the tab to act on (0 = controlled tab).
        require_user_auth: whether to ask the user before copying browser-accessible
            resource bytes into the chat workspace. Defaults true.
        approval_reason: concise user-facing reason shown if approval is requested.

    Returns:
        content = the saved resource's path, MIME type, source URL, originating
        element tag, and byte size.
    """
    return await _fetch_resource(handle, selector, url, type, max_bytes, save_path, tab, runtime)


FETCH_TOOLS = [browser_fetch_resource]
