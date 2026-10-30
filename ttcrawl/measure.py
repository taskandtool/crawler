"""`tt-crawl measure`: what is wrong with pages as a browser lays them out, at
the widths that matter, with no pictures: bands whose content does not start
on one left edge, text under the size a reader needs, words pressed against
the edge of a panel with a ground of its own, and a page that scrolls
sideways. An app's own check runs it on its built pages; `shoot` takes the
pictures to look at. It may open this machine's own dev server (localhost).
"""
import json
import re
from urllib.parse import urlsplit

from . import cdp, chrome
from .say import command, done, fail
from .shoot import LOCAL_HOSTS, PHONE_HEIGHT, DESKTOP_HEIGHT, page_status, why_not

WIDTHS = [390, 900, 1280, 1600]
MIN_TEXT, MIN_GAP = 14, 16


def measure(urls, widths, driver=None, status=page_status, min_text=MIN_TEXT, min_gap=MIN_GAP):
    """[{url, width, overflow, edges, small, tight}] for each page at each
    width, or {url, width, error} (`why_not`)."""
    out, todo = [], []
    for url in urls:
        why = why_not(url, status(url))
        if why:
            out += [{"url": url, "width": w, "error": why} for w in widths]
        else:
            todo.append(url)
    if not todo:
        return out
    if driver is None:
        driver, note = chrome.driver()
        if driver is None:
            return out + [{"url": u, "width": w, "error": note or "no browser on this machine (tt-crawl setup installs one)"}
                          for u in todo for w in widths]
    try:
        for url in todo:
            host = (urlsplit(url).hostname or "").lower()
            allow = (host,) if host in LOCAL_HOSTS else ()
            for width in widths:
                phone = width <= 600
                try:
                    got = driver._use(lambda b, w=width, p=phone: cdp.measure_page(
                        b, url, width=w, height=PHONE_HEIGHT if p else DESKTOP_HEIGHT, mobile=p,
                        allow_hosts=allow, min_text=min_text, min_gap=min_gap))
                    out.append(dict(got, url=url, width=width))
                except cdp.CDPError as e:
                    out.append({"url": url, "width": width, "error": str(e).split("\n")[0][:300]})
    finally:
        driver.stop()
    return out


RULES = {"error": "not-measured", "overflow": "sideways", "edges": "edges", "small": "small-text", "tight": "tight-panel"}


def problems(results, min_text=MIN_TEXT, min_gap=MIN_GAP, rules=False):
    """One line per problem in one page's results, the widths it shows at
    first (pure): the same text too small at four widths is one line. With
    `rules`, each is {"rule", "line"}, the rule data-lint-allow names."""
    found = {}  # key -> (widths, message); the first width's message stands

    def add(key, width, message):
        widths, _ = found.setdefault(key, ([], message))
        widths.append(width)

    for r in results:
        w = r["width"]
        if r.get("error"):
            add(("error", r["error"]), w, "not measured: %s" % r["error"])
            continue
        over = r.get("overflow")
        if over:
            add(("overflow", over.get("element")), w, "scrolls sideways by %dpx%s" % (over["px"], ", first past the edge: " + over["element"] if over.get("element") else ""))
        if r.get("edges"):
            groups = "; ".join("%dpx %s" % (g["x"], ", ".join(g["bands"][:3]) + (" +%d more" % (len(g["bands"]) - 3) if len(g["bands"]) > 3 else ""))
                               for g in r["edges"])
            # the same bands out of line at several widths are one line; a different split is its own
            add(("edges", tuple(tuple(g["bands"]) for g in r["edges"])), w, "left edges differ: %s; put every band's content on one left edge" % groups)
        # the same small text in many places (a footer's links) is one line
        small = {}
        for t in r.get("small") or []:
            small.setdefault(t["element"], []).append(t)
        for el, ts in small.items():
            more = " (+%d more)" % (len(ts) - 1) if len(ts) > 1 else ""
            px = min(t["px"] for t in ts)
            add(("small", el), w, 'text at %gpx in <%s> "%s"%s; %dpx is the smallest a reader needs' % (px, el, ts[0]["text"], more, min_text))
        for t in r.get("tight") or []:
            add(("tight", t["panel"], t["text"]), w, '"%s" is %dpx from the %s edge of <%s>, a panel with its own ground; give it at least %dpx of padding'
                % (t["text"], t["gap"], t["side"], t["panel"], min_gap))
    lines = [(RULES[key[0]], "%s: %s" % (", ".join("%dpx" % w for w in widths), message)) for key, (widths, message) in found.items()]
    return [{"rule": r, "line": ln} for r, ln in lines] if rules else [ln for _, ln in lines]


def run(args):
    for url in args.url:
        if not re.match(r"^https?://", url):
            return fail(args, 2, "needs full URLs, not %s" % url, "tt-crawl measure http://localhost:3000/ http://localhost:3000/about")
    widths = args.width or WIDTHS
    results = measure(args.url, widths, min_text=args.min_text, min_gap=args.min_gap)
    if args.json:
        # per page: its problems as lines, and what each width measured
        print(json.dumps([{"url": u, "problems": problems([r for r in results if r["url"] == u], args.min_text, args.min_gap, rules=True),
                           "widths": [r for r in results if r["url"] == u]} for u in args.url], indent=2))
        return 1 if any(r.get("error") for r in results) else 0
    lines, found = [], 0
    for url in args.url:
        mine = problems([r for r in results if r["url"] == url], args.min_text, args.min_gap)
        found += sum(1 for ln in mine if "not measured" not in ln)
        lines += [url] + ["  " + ln for ln in mine] if mine else []
    what = "%d page(s) at %s px" % (len(args.url), ", ".join(str(w) for w in widths))
    if any(r.get("error") for r in results):
        return fail(args, 1, "some pages were not measured, of " + what, "curl -s -o /dev/null -w '%{http_code}' <url>", *lines)
    done(args, None, "%s: %s" % (what, "%d problem(s)" % found if found else "nothing wrong"), lines,
         "fix each, then measure again" if found else None)
    return 0


def add_parser(sub):
    p = command(sub, "measure", "what is wrong with pages as a browser lays them out, at four widths, with no pictures",
                "Prints each problem by page and width: left edges that differ, text under --min-text px, words within "
                "--min-gap px of a coloured panel's edge, sideways scrolling. --json prints each page's problems and measurements. "
                "Exit 1 when a page could not be measured.",
                with_json=False)
    p.add_argument("url", nargs="+", help="the pages, e.g. http://localhost:3000/ http://localhost:3000/about")
    p.add_argument("--width", type=int, action="append", help="a width in pixels; repeat for more (default 390, 900, 1280 and 1600)")
    p.add_argument("--min-text", type=float, default=MIN_TEXT, help="the smallest text size in px (default 14)")
    p.add_argument("--min-gap", type=int, default=MIN_GAP, help="the least room in px between words and a panel's edge (default 16)")
    p.add_argument("--json", action="store_true", help="every page's measurements as JSON")
    p.set_defaults(func=run)
