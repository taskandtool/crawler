"""`tt-crawl shoot`: pages as screenshots at the widths that matter, for an
agent to look at its own work. The whole page as `overview.webp` (all of it in
one image a model reads, to judge its shape), strips at the full resolution a
model reads (to judge the detail), and `page.jpg` (the whole page for people,
small enough to share as a deliverable); or only the first screen. A path
(`/about`) is this machine's dev server; `--sitemap` shoots every page a
sitemap lists. What a browser can measure (left edges, text size, sideways
scrolling) is `measure`. It may open this machine's own dev server
(localhost); every other private address stays refused.
"""
import json
import os
import re
import urllib.error
from urllib.parse import urljoin, urlsplit

from . import cdp, chrome, net
from .say import command, done, fail

# A phone is 390 wide (the common iPhone) and its first screen 844 tall; a
# desktop first screen is 1000 at any width.
PHONE_WIDTH, PHONE_HEIGHT, DESKTOP_HEIGHT = 390, 844, 1000
LOCAL_HOSTS = ("localhost", "127.0.0.1", "::1")
# Dev, the app's web service on this machine (Task & Tool serves it on 3000).
DEV = "http://localhost:3000"
SITEMAP_MAX = 50           # pages one --sitemap shoots; the rest are named by path


def page_url(page):
    """A full address as it is; a path (/about) on this machine's dev server;
    None for anything else."""
    if re.match(r"^https?://", page):
        return page
    return DEV + page if page.startswith("/") else None


def sitemap_pages(site, fetch=None):
    """([url], None): the pages `site`'s /sitemap.xml lists, each at `site`'s
    own address (dev's sitemap names production's); or (None, why)."""
    url = urljoin(site, "/sitemap.xml")
    try:
        r = (fetch or net.fetch_once)(url, timeout=15)
    except Exception:
        return None, "nothing answered at %s" % url
    if r["status"] >= 400:
        return None, "%s answered %d" % (url, r["status"])
    origin = "%s://%s" % urlsplit(site)[:2]
    found = re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", r["body"].decode("utf-8", "replace"))
    pages = list(dict.fromkeys(origin + (urlsplit(u).path or "/") for u in found))
    return (pages, None) if pages else (None, "%s lists no pages" % url)


def name_for(url):
    """`home` for the root path, else the path with dashes: /services/kitchens → services-kitchens."""
    path = urlsplit(url).path.strip("/")
    return re.sub(r"[^a-z0-9]+", "-", path.lower()).strip("-") or "home"


def page_status(url, timeout=15):
    """The page's HTTP status, or None when nothing answers: net's fetch,
    with its sign-in and the crawler's own name (a firewall that refuses
    Python's default one would make a page Chrome loads look like an error).
    A page that sends the visitor to another site, or to an address this
    machine may not open, answers with why instead: a sign-in page there
    would look like the page."""
    try:
        r = net.fetch_once(url, cap=0, timeout=timeout)
    except urllib.error.URLError as e:
        blocked = str(e.reason).partition("redirect to non-public host blocked: ")[2]
        return ("%s sends visitors to %s, which this machine may not open (often a sign-in)"
                % (url, _bare(blocked)) if blocked else None)
    except Exception:
        return None
    if _site(r["final_url"]) != _site(url):
        return "%s sends visitors to another site, %s (often a sign-in)" % (url, _bare(r["final_url"]))
    return r["status"]


def why_not(url, code):
    """Why a page is not shot or measured, from `page_status`; None when it can be:
    a screenshot of an error page, or of another site's, would look like success."""
    if code is None:
        return "nothing answered at %s" % url
    if isinstance(code, str):
        return code
    return "%s answered %d" % (url, code) if code >= 400 else None


def _site(url):
    return (urlsplit(url).hostname or "").lower().removeprefix("www.")


def _bare(url):
    """An address without its query: a sign-in page's carries the way back."""
    parts = urlsplit(url)
    return "%s://%s%s" % (parts.scheme, parts.netloc, parts.path)


def shoot(url, widths, out, first_screen=False, driver=None, status=page_status):
    """[(width, meta)] for each width; meta carries `dir` and `strips`, or
    `error` (`why_not`)."""
    why = why_not(url, status(url))
    if why:
        return [(w, {"error": why}) for w in widths]
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


def width_lines(results):
    """A line per width: where its images are and which to read first."""
    lines = []
    for width, meta in results:
        if meta.get("error"):
            lines.append("%dpx: failed: %s" % (width, meta["error"]))
            continue
        strips = meta["strips"]
        cut = ", cut off at %d strips" % len(strips) if meta.get("truncated") else ""
        if len(strips) == 1:
            lines.append("%dpx: %s/%s  (page %dpx tall, one image%s; page.jpg to share)" % (
                width, meta["dir"], strips[0], meta["height"], cut))
        elif not meta.get("overview"):
            lines.append("%dpx: %s/01.webp … %s  (page %dpx tall, too long for one overview; read in order%s; "
                         "page.jpg is the whole page to share)" % (width, meta["dir"], strips[-1], meta["height"], cut))
        else:
            lines.append("%dpx: %s/overview.webp, then 01.webp … %s  (page %dpx tall%s; page.jpg is the whole page to share)" % (
                width, meta["dir"], strips[-1], meta["height"], cut))
    return lines


def report(shots, first_screen):
    """(what was shot, the lines, what to do next) for [(url, results)];
    next is the check to run when a page failed."""
    how = "first screen" if first_screen else "whole page"
    if len(shots) == 1:
        url, results = shots[0]
        what, lines = "%s (%s)" % (url, how), width_lines(results)
    else:
        what = "%d pages (%s)" % (len(shots), how)
        lines = [l for url, results in shots for l in [url] + ["  " + w for w in width_lines(results)]]
    failed = [(url, m["error"]) for url, results in shots for _, m in results if m.get("error")]
    if failed:
        url, why = failed[0]
        if "sends visitors to" in why:
            nxt = "curl -sI " + url + ", to see where it sends visitors"
        elif why.startswith("nothing answered") and urlsplit(url).hostname in LOCAL_HOSTS:
            nxt = "python3 ~/tools/taskandtool.py logs (dev's service; restart brings it back), then this again"
        else:
            nxt = "curl -s -o /dev/null -w '%{http_code}' " + url + ", to see whether the page is served at that path"
    elif any(m.get("overview") for _, results in shots for _, m in results):
        nxt = "look at each overview for the page's shape, then its strips in order for the detail"
    else:
        nxt = "look at each image, in order"
    return what, lines, nxt


def run(args):
    urls = []
    for page in args.pages:
        url = page_url(page)
        if not url:
            return fail(args, 2, "%s is not a page: give a full address, or a path on this machine's dev server" % page,
                        "tt-crawl shoot /about")
        urls.append(url)
    note = None
    if args.sitemap:
        if len(urls) > 1:
            return fail(args, 2, "--sitemap takes one site (default dev), not %d pages" % len(urls),
                        "tt-crawl shoot --sitemap, or tt-crawl shoot https://theirsite.com --sitemap")
        site = urls[0] if urls else DEV + "/"
        listed, why = sitemap_pages(site)
        if why:
            return fail(args, 1, "--sitemap: %s" % why, "curl -s %s" % urljoin(site, "/sitemap.xml"))
        urls = listed[:SITEMAP_MAX]
        if len(listed) > SITEMAP_MAX:
            note = "the first %d of the %d pages in the sitemap; name the rest by path" % (SITEMAP_MAX, len(listed))
    urls = urls or [DEV + "/"]
    widths = args.width or [1280, PHONE_WIDTH]
    shots = [(url, shoot(url, widths, args.out, first_screen=args.first_screen)) for url in urls]
    failed = [url for url, results in shots if any(m.get("error") for _, m in results)]
    if args.json:
        # every width's result, failed ones too, so a caller sees which
        print(json.dumps([dict(m, width=w, url=url) for url, results in shots for w, m in results], indent=2))
        return 1 if failed else 0
    what, lines, nxt = report(shots, args.first_screen)
    lines += [note] if note else []
    if failed:
        if len(shots) > 1:
            said = "%d of %d pages shot (%s)" % (len(shots) - len(failed), len(shots), "first screen" if args.first_screen else "whole page")
        else:
            shot = sum(1 for _, m in shots[0][1] if not m.get("error"))
            said = "%s of %s" % ("%d of %d widths shot" % (shot, len(shots[0][1])) if shot else "no screenshots", what)
        return fail(args, 1, said, nxt, *lines)
    done(args, None, what, lines, nxt)
    return 0


def add_parser(sub):
    p = command(sub, "shoot", "screenshots of pages at desktop and phone width, whole or first screen; "
                              "a path is this machine's dev server",
                "Prints each page's folder per width and which image to read first (page.jpg is the whole page, "
                "small enough to share as a deliverable), then Next:. --json prints every width's result as JSON, "
                "failed ones too. Exit 1 when a page failed (a page answering 400 or more, or sending visitors to "
                "another site, is not shot).",
                with_json=False)
    p.add_argument("pages", nargs="*", metavar="PAGE",
                   help="a full address (https://example.com/about) or a path on this machine's dev server "
                        "(/about); several for several pages (default: dev's /)")
    p.add_argument("--sitemap", action="store_true",
                   help="every page the site's sitemap.xml lists (the site given, default dev), at most %d" % SITEMAP_MAX)
    p.add_argument("--width", type=int, action="append",
                   help="a width in pixels; repeat for more (default 1280 and 390)")
    p.add_argument("--first-screen", action="store_true", help="only what shows before scrolling, one image per width")
    p.add_argument("--out", default="uploads", help="where the images go (default uploads/), in <name>-<width>/")
    p.add_argument("--json", action="store_true", help="each width's result as JSON instead of the summary")
    p.set_defaults(func=run)
