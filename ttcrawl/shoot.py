"""`tt-crawl shoot`: one page as screenshots at the widths that matter, for an
agent to look at its own work. The whole page as `overview.png` (all of it in
one image a model reads, to judge its shape), strips at the full resolution a
model reads (to judge the detail), and `page.png` (the whole page at full
size, for people); or only the first screen. It may open this machine's own dev
server (localhost); every other private address stays refused.
"""
import json
import os
import re
import urllib.error
import urllib.request
from urllib.parse import urlsplit

from . import cdp, chrome, net
from .say import command, done, fail

# A phone is 390 wide (the common iPhone) and its first screen 844 tall; a
# desktop first screen is 1000 at any width.
PHONE_WIDTH, PHONE_HEIGHT, DESKTOP_HEIGHT = 390, 844, 1000
LOCAL_HOSTS = ("localhost", "127.0.0.1", "::1")


def name_for(url):
    """`home` for the root path, else the path with dashes: /services/kitchens → services-kitchens."""
    path = urlsplit(url).path.strip("/")
    return re.sub(r"[^a-z0-9]+", "-", path.lower()).strip("-") or "home"


def page_status(url, timeout=15):
    """The page's HTTP status, or None when nothing answers."""
    try:
        # the crawler's own name: a firewall that refuses Python's default one
        # would make a page Chrome loads look like an error page
        req = urllib.request.Request(url, method="GET", headers={"User-Agent": net.USER_AGENT})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code
    except Exception:
        return None


def shoot(url, widths, out, first_screen=False, driver=None, status=page_status):
    """[(width, meta)] for each width; meta carries `dir` and `strips`, or `error`.
    A page that answers 400 or above is not shot: a screenshot of an error
    page would look like success."""
    code = status(url)
    if code is None:
        return [(w, {"error": "nothing answered at %s" % url}) for w in widths]
    if code >= 400:
        return [(w, {"error": "%s answered %d" % (url, code)}) for w in widths]
    name = name_for(url)
    host = (urlsplit(url).hostname or "").lower()
    allow = (host,) if host in LOCAL_HOSTS else ()
    if driver is None:
        driver, note = chrome.driver()
        if driver is None:
            return [(w, {"error": note or "no browser on this machine (tt-crawl setup installs one)"}) for w in widths]
    results = []
    try:
        for width in widths:
            phone = width <= 600
            folder = os.path.join(out, "%s-%d" % (name, width))
            try:
                meta = driver._use(lambda b, w=width, f=folder, p=phone: cdp.screenshot_strips(
                    b, url, f, width=w, height=PHONE_HEIGHT if p else DESKTOP_HEIGHT, mobile=p,
                    first_screen=first_screen, allow_hosts=allow))
                meta["dir"] = folder
            except cdp.CDPError as e:
                meta = {"error": str(e).split("\n")[0][:300]}
            results.append((width, meta))
    finally:
        driver.stop()
    return results


def report(url, results, first_screen):
    """(what was shot, a line per width, what to do next); next is the
    check to run when a width failed."""
    what = "%s (%s)" % (url, "first screen" if first_screen else "whole page")
    lines = []
    for width, meta in results:
        if meta.get("error"):
            lines.append("%dpx: failed: %s" % (width, meta["error"]))
            continue
        strips = meta["strips"]
        cut = ", cut off at %d strips" % len(strips) if meta.get("truncated") else ""
        if len(strips) == 1:
            lines.append("%dpx: %s/%s  (page %dpx tall, one image%s)" % (width, meta["dir"], strips[0], meta["height"], cut))
        elif not meta.get("overview"):
            lines.append("%dpx: %s/01.png … %s  (page %dpx tall, too long for one overview; read in order%s; "
                         "page.png is the whole page for people)" % (width, meta["dir"], strips[-1], meta["height"], cut))
        else:
            lines.append("%dpx: %s/overview.png, then 01.png … %s  (page %dpx tall%s; page.png is the whole page for people)" % (
                width, meta["dir"], strips[-1], meta["height"], cut))
    if any(m.get("error") for _, m in results):
        nxt = "curl -s -o /dev/null -w '%{http_code}' " + url + ", to see whether the page is served at that path"
    elif any(m.get("overview") for _, m in results):
        nxt = "look at each overview for the page's shape, then its strips in order for the detail"
    else:
        nxt = "look at each image, in order"
    return what, lines, nxt


def run(args):
    if not re.match(r"^https?://", args.url):
        return fail(args, 2, "needs a full URL, not %s" % args.url, "tt-crawl shoot http://localhost:3000/services")
    widths = args.width or [1280, PHONE_WIDTH]
    results = shoot(args.url, widths, args.out, first_screen=args.first_screen)
    failed = any(m.get("error") for _, m in results)
    if args.json:
        # every width's result, failed ones too, so a caller sees which
        print(json.dumps([dict(m, width=w) for w, m in results], indent=2))
        return 1 if failed else 0
    what, lines, nxt = report(args.url, results, args.first_screen)
    if failed:
        shot = sum(1 for _, m in results if not m.get("error"))
        return fail(args, 1, "%s of %s" % ("no screenshots" if not shot else "%d of %d widths shot" % (shot, len(results)), what),
                    nxt, *lines)
    done(args, None, what, lines, nxt)
    return 0


def add_parser(sub):
    p = command(sub, "shoot", "screenshots of one page at desktop and phone width, whole or first screen; "
                              "may open this machine's own dev server",
                "Prints each width's folder and which image to read first, then Next:. --json prints every width's "
                "result as JSON, failed ones too. Exit 1 when a width failed (a page answering 400 or more is not shot).",
                with_json=False)
    p.add_argument("url", help="the page, e.g. http://localhost:3000/ or https://example.com/about")
    p.add_argument("--width", type=int, action="append",
                   help="a width in pixels; repeat for more (default 1280 and 390)")
    p.add_argument("--first-screen", action="store_true", help="only what shows before scrolling, one image per width")
    p.add_argument("--out", default="uploads", help="where the images go (default uploads/), in <name>-<width>/")
    p.add_argument("--json", action="store_true", help="each width's result as JSON instead of the summary")
    p.set_defaults(func=run)
