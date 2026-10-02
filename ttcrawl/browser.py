"""What a page is asked once a browser has built it (the HTML as rendered,
annotated, and the computed styles), and finding Obscura. cdp.py drives
the browser; `tt-crawl setup` installs both."""
import json
import os
import shutil

OBSCURA_CANDIDATES = ("/usr/local/bin/obscura", os.path.expanduser("~/.local/bin/obscura"))

# What the browser is asked to read off a rendered page for the brand: the
# fonts and colours by role, the buttons, the logo candidates. One JSON string.
EVAL_JS = r"""
(() => {
  const cs = (el) => el ? getComputedStyle(el) : null;
  const first = (sel) => document.querySelector(sel);
  const font = (el) => { const s = cs(el); return s ? s.fontFamily : ""; };
  const role = (sel) => { const el = first(sel); const s = cs(el); return s ? {
    font: s.fontFamily, size: s.fontSize, weight: s.fontWeight, lineHeight: s.lineHeight,
    letterSpacing: s.letterSpacing, color: s.color, background: s.backgroundColor } : null; };
  const counts = {};
  const bump = (k) => { if (k && k !== "rgba(0, 0, 0, 0)" && k !== "transparent") counts[k] = (counts[k] || 0) + 1; };
  const els = Array.from(document.querySelectorAll("body *")).slice(0, 4000);
  for (const el of els) { const s = cs(el); bump(s.backgroundColor); bump(s.color); }
  const buttons = Array.from(document.querySelectorAll("a, button")).filter(el => {
    const s = cs(el); return s && s.backgroundColor !== "rgba(0, 0, 0, 0)" && el.innerText && el.innerText.trim().length < 40;
  }).slice(0, 8).map(el => { const s = cs(el); return { text: el.innerText.trim(), background: s.backgroundColor,
    color: s.color, radius: s.borderRadius, font: s.fontFamily, weight: s.fontWeight, padding: s.padding }; });
  const logos = Array.from(document.querySelectorAll("img, svg")).filter(el => {
    const t = ((el.getAttribute("src") || "") + " " + (el.getAttribute("alt") || "") + " " + (el.getAttribute("class") || "") + " " + (el.closest("a") ? el.closest("a").getAttribute("href") || "" : "")).toLowerCase();
    return t.includes("logo") || (el.closest("header") && el.closest("a[href='/'], a[href='./'], a[href$='" + location.host + "/']"));
  }).slice(0, 6).map(el => ({ tag: el.tagName.toLowerCase(), src: el.getAttribute("src") || "", alt: el.getAttribute("alt") || "",
    width: el.getBoundingClientRect().width, height: el.getBoundingClientRect().height }));
  return JSON.stringify({
    title: document.title,
    body: role("body"), h1: role("h1"), h2: role("h2"), h3: role("h3"), p: role("main p, p"), a: role("main a, a"),
    fonts: Array.from(new Set(els.slice(0, 1500).map(font).filter(Boolean))).slice(0, 12),
    colors: Object.entries(counts).sort((a, b) => b[1] - a[1]).slice(0, 24),
    buttons, logos,
    viewport: { width: innerWidth, height: innerHeight, documentHeight: document.documentElement.scrollHeight },
  });
})()
"""


# One render per page: mark on the page what only a browser knows, as
# data-tt-* attributes the parser reads (an image's real width and the source
# it chose among its sizes; a large element's CSS background), then hand back
# the rendered HTML, and the computed styles when asked (STYLES).
EXTRACT_JS = r"""
(() => {
  for (const img of document.images) {
    if (img.currentSrc) img.setAttribute("data-tt-src", img.currentSrc);
    if (img.naturalWidth) { img.setAttribute("data-tt-w", img.naturalWidth); img.setAttribute("data-tt-h", img.naturalHeight); }
  }
  const all = document.body ? document.body.querySelectorAll("*") : [];
  for (let i = 0; i < all.length && i < 6000; i++) {
    const el = all[i], bg = getComputedStyle(el).backgroundImage;
    if (!bg || bg === "none") continue;
    const m = bg.match(/url\(["']?([^"')]+)["']?\)/); if (!m) continue;
    const r = el.getBoundingClientRect(); if (r.width < 300 || r.height < 200) continue;
    try { el.setAttribute("data-tt-bg", new URL(m[1], location.href).href); } catch (e) {}
  }
  const styles = STYLES ? JSON.parse(STYLES_JS) : null;
  const doctype = document.doctype ? "<!doctype html>" : "";
  return JSON.stringify({ html: doctype + document.documentElement.outerHTML, styles });
})()
"""


def extract_js(styles=False):
    """EXTRACT_JS, asking for the computed styles or not."""
    return EXTRACT_JS.replace("STYLES_JS", EVAL_JS.strip()).replace("STYLES ?", "true ?" if styles else "false ?")


def find_obscura(env=os.environ, which=shutil.which, exists=os.path.isfile):
    """The Obscura binary if installed: $OBSCURA_BIN (or $OBSCURA), then
    PATH, then the two places `tt-crawl setup` puts it.
    None means it is not installed.
    An explicit path that does not exist is a misconfiguration, not a
    reason to pick another binary."""
    explicit = env.get("OBSCURA_BIN") or env.get("OBSCURA")
    if explicit:
        return explicit if exists(explicit) else None
    found = which("obscura")
    if found:
        return found
    for cand in OBSCURA_CANDIDATES:
        if exists(cand):
            return cand
    return None


def parse_eval(out):
    """Obscura may print the string quoted as JSON, or raw."""
    if not out:
        return None
    s = out.strip()
    try:
        v = json.loads(s)
        if isinstance(v, str):
            v = json.loads(v)
        return v if isinstance(v, dict) else None
    except Exception:
        return None
