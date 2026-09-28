"""The Obscura headless browser, as this tool uses it: render a page's DOM
and read computed styles. Full-page screenshots go through its CDP server
(cdp.py). Installing Obscura is the kits' setup job; this only finds and
runs it."""
import json
import os
import shutil
import subprocess

OBSCURA_CANDIDATES = ("/usr/local/bin/obscura", os.path.expanduser("~/.local/bin/obscura"))
RENDER_TIMEOUT_S = 60

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


def find_obscura(env=os.environ, which=shutil.which, exists=os.path.isfile):
    """The Obscura binary if installed: $OBSCURA_BIN (or $OBSCURA), then
    PATH, then the two places the kits install to. None means no browser.
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


def render_html(url, binary, runner=subprocess.run, timeout=RENDER_TIMEOUT_S):
    """Fetch `url` through Obscura and return the rendered HTML, or None when
    the render fails, times out, or comes back empty. Obscura refuses private
    and internal addresses itself, so the SSRF rail holds on this path too."""
    cmd = [binary, "fetch", url, "--dump", "html", "--quiet", "--timeout", "30"]
    try:
        proc = runner(cmd, capture_output=True, timeout=timeout)
    except (subprocess.TimeoutExpired, OSError):
        return None
    if proc.returncode != 0:
        return None
    out = proc.stdout.decode("utf-8", "replace") if isinstance(proc.stdout, bytes) else (proc.stdout or "")
    return out if out.strip() else None


def read_styles(url, binary, runner=subprocess.run, timeout=RENDER_TIMEOUT_S):
    """Evaluate EVAL_JS on the rendered page and return its dict, or None."""
    cmd = [binary, "fetch", url, "--quiet", "--timeout", "30", "--eval", EVAL_JS]
    try:
        proc = runner(cmd, capture_output=True, timeout=timeout)
    except (subprocess.TimeoutExpired, OSError):
        return None
    if proc.returncode != 0:
        return None
    out = proc.stdout.decode("utf-8", "replace") if isinstance(proc.stdout, bytes) else (proc.stdout or "")
    return parse_eval(out)


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
