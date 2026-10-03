"""`tt-crawl site`, `survey`, `brand`, `pages`, `reference` and `add`: read a
site into its folder (paths.py has the layout). The five crawls are one crawl
with different defaults.

A crawl reads pages, then writes everything from what it read:

- **read**: the start page, then what the site's own header and nav link
  to, then what the home page links to, then the footer's links, then the
  sitemap, then everything else found along the way (templates.Frontier),
  capped per template and per section when sampling (templates.Sampler).
  Each page is asked for plainly first (status, redirects, throttling), then
  rendered once in the browser (`--browser`, chrome by default; none with
  `--static`), and parsed into its content blocks, links, forms, images,
  reviews and structured data. Each page read is saved to `_cache/pages/` as it is read,
  and the crawl's place every few pages, so a long crawl can `--resume`.
- **write**: the page files (frontmatter, then the page's own text,
  verbatim), the pictures (each once, at its largest, linked from every page
  that shows it), and the index files in `_index/`: inventory, templates,
  furniture, media, facts, reviews, styles, common lines, the run and the
  manifest. `add` reads more pages into a folder and writes it all again
  from the cache, without reading the rest.

No step involves a model: a crawl of thousands of pages costs the machine's
time and nothing else.
"""
import hashlib
import json
import math
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urljoin, urlsplit

from . import __version__, chrome, dom, inventory, net, paths, structured
from .blocks import fingerprint, to_markdown
from .facts import Facts, jsonld_reviews, reviews as page_reviews
from .furniture import page_furniture, site_furniture
from .html import parse_page
from .media import MODES, Media, dimensions, extension
from .media import file_name as image_file_name
from .styles import merge_styles
from .templates import Frontier, Sampler, Templates, is_collection
from .text import (MD_IMAGE_RE, MIN_MARKDOWN_CHARS, NearDuplicates, content_digest, minhash, norm_line,
                   page_name, rewrite_images, word_count)

DEFAULT_MAX_PAGES = 100
MAX_IMAGE_BYTES = 25 * 1024 * 1024
MAX_SITEMAPS = 50
STYLE_PAGES = 5
# A brand read learns the look from the first pages (the start and what its
# header links to); shooting every page cost most of the crawl's time.
BRAND_SHOT_PAGES = 5
MAX_DELAY_S = 8
STATE_EVERY = 10             # pages between two saves of the crawl's place
# Pages read at once, each in its own browser: most of a crawl is the browser
# rendering, and three in flight cut a 24-page read by more than half without
# a burst a small host would notice. A throttled answer drops to one.
PARALLEL = 3
IMAGE_PARALLEL = 6          # pictures downloaded at once (plain requests, no browser)
CACHE_VERSION = 2
# A line on this share of the pages read (and on at least three) is the
# site's furniture wherever the theme put it: a top bar, a skip link, a
# sitewide banner. A call to action on every service page is content.
FURNITURE_SHARE = 0.6
# What the parsed page keeps in the cache: enough to write the folder again.
PARSED_KEEP = ("url", "title", "lang", "meta", "meta_description", "canonical", "links", "internal_links",
               "images", "forms", "landmark_lines", "has_landmarks", "h1")


def load_robots(start_url):
    from urllib import robotparser
    rp = robotparser.RobotFileParser()
    parts = urlsplit(start_url)
    try:
        text = net.fetch_text(f"{parts.scheme}://{parts.netloc}/robots.txt", cap=200_000)
        rp.parse(text.splitlines())
        sitemaps = re.findall(r"(?im)^\s*sitemap:\s*(\S+)", text)
    except Exception:
        rp.parse(["User-agent: *", "Allow: /"])
        sitemaps = []
    return rp, sitemaps


def parse_sitemap(xml):
    """(loc, lastmod) pairs from a sitemap or sitemap index (pure)."""
    out = []
    for m in re.finditer(r"<url>(.*?)</url>|<sitemap>(.*?)</sitemap>", xml, re.S):
        block = m.group(1) or m.group(2) or ""
        loc = re.search(r"<loc>\s*([^<\s]+)\s*</loc>", block)
        if not loc:
            continue
        lastmod = re.search(r"<lastmod>\s*([^<\s]+)\s*</lastmod>", block)
        out.append((loc.group(1), lastmod.group(1) if lastmod else None))
    if not out:
        out = [(u, None) for u in re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", xml)]
    return out


def sitemap_urls(start_url, extra_sitemaps, root_host):
    """Every same-site page URL the site's sitemaps list: (url, lastmod, the
    sitemap it was listed in), the last being what tells a post from a page."""
    parts = urlsplit(start_url)
    todo = [f"{parts.scheme}://{parts.netloc}/sitemap.xml", f"{parts.scheme}://{parts.netloc}/sitemap_index.xml"] + list(extra_sitemaps)
    seen, found = set(), []
    while todo and len(seen) < MAX_SITEMAPS:
        sm = todo.pop(0)
        if sm in seen or not net.same_site(sm, root_host):
            continue
        seen.add(sm)
        try:
            r = net.fetch(sm)
            if r["status"] != 200:
                continue
            xml = r["body"].decode("utf-8", "replace")
        except Exception:
            continue
        for loc, lastmod in parse_sitemap(xml):
            if re.search(r"\.xml(\.gz)?$", loc.lower()) and "sitemap" in loc.lower():
                todo.append(loc)
            elif net.same_site(loc, root_host):
                found.append((loc, lastmod, sm))
    return found


def _load_json(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _dump(path, data, indent=2):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=indent)
    os.replace(tmp, path)


def _now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class Crawl:
    """One run into one folder: what was read, and how to write it."""

    def __init__(self, args, start, profile, settings=None):
        self.args, self.start, self.profile = args, start, profile
        # What `add` and `import` keep for this folder: the pictures and the
        # page limit the crawl that made it chose.
        self.settings = settings or {"images": args.images, "limit": args.max_pages}
        self.root_host = urlsplit(start).hostname or ""
        self.out = args.out
        # --static is no browser at all: no render, no screenshots, no styles
        self.driver, self.browser_note = (None, None) if args.static else chrome.driver(args.browser)
        if args.static and (args.screenshots or args.styles):
            self.browser_note = "--static: no browser, so no screenshots or styles"
            args.screenshots = args.styles = False
        self.structured_dir = os.path.join(self.out, paths.STRUCTURED)
        manifest = _load_json(paths.index(self.out, "manifest.json")) or {}
        self.earlier = {p["url"]: p["file"] for p in (manifest.get("earlier") or []) + (manifest.get("pages") or [])
                        if p.get("url") and p.get("file")}
        self.previous_hashes = {p["url"]: p.get("hash") for p in manifest.get("pages") or [] if p.get("url")}
        # A site that throttles us once is asked more gently for the rest of the run.
        self.pace = {"delay": args.delay, "throttled": 0}
        self.records = {start: inventory.new_record(start)}
        self.order = []              # names of the pages kept, in reading order (their data is in _cache)
        self.skipped = []
        self.templates = Templates()
        self.frontier = Frontier()
        self.sampler = Sampler(args.per_template, args.per_section)
        self.seen = set()
        self.kept_hashes, self.near = set(), NearDuplicates()
        self.style_readings = []
        self.extra_drivers = []            # the browsers of pages read in parallel (workers)
        self.limit_reached = False
        self.started = _now()
        # One name per URL for its page file, its structured JSON and its
        # screenshots: the name an earlier run gave it, else its own
        # page_name, hashed when another URL already holds that one.
        self.names = {}
        self.owners = {os.path.splitext(os.path.basename(f))[0]: u for u, f in self.earlier.items()}
        for d in (paths.PAGES, paths.INDEX, os.path.join(paths.CACHE, "pages")):
            os.makedirs(os.path.join(self.out, d), exist_ok=True)
        os.makedirs(self.structured_dir, exist_ok=True)

    def name_for(self, url):
        if url not in self.names:
            stem = os.path.splitext(os.path.basename(self.earlier[url]))[0] if url in self.earlier else page_name(url)
            if self.owners.get(stem, url) != url:
                stem = page_name(url, hashed=True)
            self.names[url], self.owners[stem] = stem, url
        return self.names[url]

    def record(self, url):
        return self.records.setdefault(url, inventory.new_record(url))

    def skip(self, url, reason):
        self.skipped.append({"url": url, "reason": reason})
        self.record(url)["reason"] = reason

    def cache_path(self, name):
        return os.path.join(self.out, paths.CACHE, "pages", name + ".json")

    def cached(self, name):
        return _load_json(self.cache_path(name))

    # ── discovery ──
    def seed(self):
        self.robots, robots_sitemaps = load_robots(self.start)
        self.frontier.add(self.start, Frontier.START)
        self.templates.add(self.start)
        for u, lastmod, sm in sitemap_urls(self.start, robots_sitemaps, self.root_host):
            n = net.normalize_url(u)
            if not n:
                continue
            rec = self.record(n)
            rec["in_sitemap"], rec["lastmod"] = True, lastmod or rec["lastmod"]
            self.templates.add(n, sm)
            self.frontier.add(n, Frontier.SITEMAP)

    def found(self, links, from_start):
        """Queue the same-site links a page carries, each through the door it
        came by: the site's header and nav first (the home page's, outside its
        main content: a post's own previous/next <nav> is not the site's), the
        home page's own links next."""
        nav = []
        for l in links:
            n = net.normalize_url(l["href"])
            if not n or n in self.seen or not net.same_site(n, self.root_host) or not net.crawlable(n):
                continue
            self.record(n)
            self.templates.add(n)
            if from_start and l["landmark"] in ("header", "nav") and not l.get("in_main"):
                nav.append(n)
                tier = Frontier.NAV
            elif from_start:
                tier = Frontier.FOOTER if l["landmark"] in ("footer", "aside") else Frontier.HOME_BODY
            else:
                tier = Frontier.FOUND
            self.frontier.add(n, tier)
        self.templates.mark_nav(nav)

    # ── reading ──
    def next_batch(self, size):
        """Up to `size` pages to read next, each admitted (robots, sampling)
        in frontier order; the ones turned away are recorded as skipped."""
        batch = []
        while len(self.frontier) and len(batch) < size:
            url = self.frontier.pop()
            if url in self.seen:
                continue
            self.seen.add(url)
            template = self.templates.of(url)
            self.record(url)["template"] = template
            if not self.robots.can_fetch(net.USER_AGENT, url):
                self.skip(url, "robots")
                continue
            if url != self.start and not self.sampler.admit(url, template, nav=url in self.templates.nav):
                self.skip(url, "sampled_out")
                continue
            batch.append((url, template))
        return batch

    def workers(self, n):
        """A browser per page in flight: the crawl's own first, more started
        as needed; a browser that will not start leaves fewer in flight."""
        if self.driver is None:
            return [None] * n
        while len(self.extra_drivers) < n - 1:
            d, _note = chrome.driver(self.args.browser, install=False)
            if d is None:
                break
            self.extra_drivers.append(d)
        return [self.driver] + self.extra_drivers[: n - 1]

    def loop(self):
        requested = 0
        width = max(1, getattr(self.args, "parallel", PARALLEL) or 1)
        while len(self.frontier):
            budget = self.args.max_pages - len(self.order)
            if budget <= 0:
                self.limit_reached = True
                break
            # The start page alone, so the header's links are known before the
            # rest; then up to `width` at once, never past the page limit.
            size = 1 if not self.order else min(width, budget)
            batch = self.next_batch(size)
            if not batch:
                continue
            # Between every two rounds of requests, at the pace the site allows.
            if requested:
                time.sleep(self.pace["delay"])
            styles_left = self.args.styles and self.args.style_pages - len(self.style_readings)
            jobs = [(url, i < (styles_left or 0)) for i, (url, _t) in enumerate(batch)]
            drivers = self.workers(len(jobs))
            if len(drivers) < len(jobs):
                for url, _t in batch[len(drivers):]:      # no browser for these: back to the frontier
                    self.seen.discard(url)
                    self.frontier.add(url, Frontier.FOUND)
                jobs, batch = jobs[: len(drivers)], batch[: len(drivers)]
            if len(jobs) == 1:
                fetched = [self.fetch(jobs[0][0], drivers[0], jobs[0][1])]
            else:
                with ThreadPoolExecutor(max_workers=len(jobs)) as pool:
                    fetched = list(pool.map(lambda j, d: self.fetch(j[0], d, j[1]), jobs, drivers))
            for (url, template), got in zip(batch, fetched):
                requested += 1
                self.read(url, from_start=(url == self.start), fetched=got)
                if self.record(url)["reason"] == "redirect":
                    self.sampler.refund(url, template)
                if requested % STATE_EVERY == 0:
                    self.save_state()
            if self.pace["throttled"]:      # the site pushed back: one page at a time from here
                width = 1
        for url in self.frontier:
            rec = self.record(url)
            rec["template"] = rec["template"] or self.templates.of(url)
            if rec["reason"] is None:
                rec["reason"] = "unread"
        self.save_state()

    def read(self, url, from_start=False, fetched=None):
        """Read one page: kept (saved to the cache), or skipped with its reason.
        `fetched` is what `fetch` already brought back, when it ran in parallel."""
        got = self.get(url, fetched)
        if got is None:
            return
        html, rendered, styles, fetcher = got
        self.absorb(url, html, rendered=rendered, styles=styles, fetcher=fetcher, from_start=from_start)

    def fetch(self, url, driver, want_styles):
        """The network half of reading a page, safe to run several at once:
        the plain request first, for the status, the final URL and the headers
        (and the page itself when there is no browser), so a redirect, an
        error or a throttled answer costs no render; then one render in
        `driver`. (resp or None, render or None, engine or None)."""
        try:
            resp = net.fetch(url, method="GET" if driver is None else "HEAD")
        except Exception:
            resp = None
        if driver and (resp is None or resp["status"] == 405):
            # some servers refuse or drop a HEAD; ask once more the plain way
            try:
                resp = net.fetch(url)
            except Exception:
                resp = None
        if resp is None or not driver:
            return resp, None, None
        final = net.normalize_url(resp["final_url"]) if resp["final_url"] else None
        if (final and final != url) or (resp["status"] and resp["status"] >= 400):
            return resp, None, None
        return resp, driver.render(url, styles=want_styles), driver.engine

    def get(self, url, fetched=None):
        """The page's HTML, or None (skipped, with the reason recorded), from
        what `fetch` brought back (fetched now when it has not run)."""
        rec = self.record(url)
        if fetched is None:
            want_styles = self.args.styles and len(self.style_readings) < self.args.style_pages
            fetched = self.fetch(url, self.driver, want_styles)
        resp, got, engine = fetched
        if resp is None:
            return self.skip(url, "fetch_failed")
        rec["status"], rec["final_url"] = resp["status"], resp["final_url"]
        lm = resp["headers"].get("last-modified")
        if lm and not rec["lastmod"]:
            try:
                rec["lastmod"] = parsedate_to_datetime(lm).date().isoformat()
            except Exception:
                pass
        final = net.normalize_url(resp["final_url"]) if resp["final_url"] else None
        if final and final != url:
            if net.same_site(final, self.root_host) and net.crawlable(final) and final not in self.seen:
                self.record(final)
                self.templates.add(final)
                self.frontier.add(final, Frontier.NAV if url in self.templates.nav else Frontier.FOUND)
            return self.skip(url, "redirect")
        if resp["status"] and resp["status"] >= 400:
            return self.skip(url, "http_%d" % resp["status"])

        # The render: the page's HTML as the browser built it, annotated with
        # what only the browser knows, and the computed styles when asked.
        html, rendered, styles = None, False, None
        if got:
            html, rendered, styles = got["html"], True, got.get("styles")
        if html is None:
            if not resp["body"]:
                try:
                    resp = net.fetch(url)
                except Exception:
                    return self.skip(url, "fetch_failed")
            html = resp["body"].decode("utf-8", "replace")
        if not html or not html.strip():
            return self.skip(url, "fetch_failed")
        return html, rendered, styles, engine if rendered else "static"

    def absorb(self, url, html, rendered=False, styles=None, fetcher="static", from_start=False, front=None):
        """Keep a page read by any route (a render, a static fetch, or an
        item a platform's own feed carried): its structured data, its
        inventory record, the links it offers, and, unless it is empty or a
        duplicate, its blocks in the cache. `front` adds to its frontmatter
        (an import's date, author, categories)."""
        rec = self.record(url)
        parsed = parse_page(html, url)
        page_structured = structured.page_structured(parsed, html)
        with open(os.path.join(self.structured_dir, self.name_for(url) + ".json"), "w") as f:
            json.dump(page_structured, f, indent=2)
        rec.update({
            "title": parsed["title"], "meta_description": parsed["meta_description"], "h1": parsed["h1"],
            "h1_count": parsed["h1_count"], "canonical": parsed["canonical"], "lang": parsed["lang"],
            "noindex": parsed["noindex"], "hreflang": parsed["hreflang"],
            "jsonld_types": page_structured["jsonld_types"], "og_image": parsed["meta"].get("og:image"),
            "forms": [{"action": f["action"], "method": f["method"], "fields": [x["name"] or x["type"] for x in f["fields"]]} for f in parsed["forms"]],
            "embeds": page_structured["embeds"], "tracking": page_structured["tracking"], "rendered": rendered,
            "documents": sorted({l["href"] for l in parsed["links"] if net.is_document(l["href"]) and net.same_site(l["href"], self.root_host)}),
            "fingerprint": fingerprint(parsed["blocks"]),
        })
        internal = []
        for l in parsed["internal_links"]:
            n = net.normalize_url(l["href"])
            if n and n != url and n not in internal:
                internal.append(n)
        rec["outbound_internal"] = internal
        self.found(parsed["internal_links"], from_start)
        for l in parsed["internal_links"]:
            n = net.normalize_url(l["href"])
            if n and n != url:
                inventory.add_inbound(self.records, url, n, sitewide=l["landmark"] in ("header", "nav", "footer", "aside"))

        # The page's own text, verbatim; a page with little of it (contact,
        # gallery, a short landing page) is still the site's page, kept and
        # marked thin, since it is often where the phone number, the photos or
        # the form are.
        own = to_markdown(parsed["blocks"])
        if not own.strip():
            return self.skip(url, "empty")
        digest = content_digest(own)
        if digest in self.kept_hashes:
            return self.skip(url, "exact_duplicate")
        sig = minhash(own)
        if self.near.check(sig):
            return self.skip(url, "near_duplicate")
        self.kept_hashes.add(digest)
        self.near.keep(sig)
        if styles:
            self.style_readings.append(styles)
        name = self.name_for(url)
        found_reviews = page_reviews(dom.build(html), url)
        _dump(self.cache_path(name), {
            "url": url, "name": name, "rendered": rendered, "fetched": _now(), "fetcher": fetcher, "front": front or {},
            "blocks": parsed["blocks"], "structured": page_structured, "reviews": found_reviews,
            "parsed": {k: parsed.get(k) for k in PARSED_KEEP}, "digest": digest, "minhash": sig}, indent=None)
        if name not in self.order:
            self.order.append(name)
        shot_cap = getattr(self.args, "screenshot_pages", None)
        shots = sum(1 for r in self.records.values() if r.get("screenshot"))
        if self.driver and self.args.screenshots and (not shot_cap or shots < shot_cap):  # only the pages kept: a duplicate costs no capture
            shot = self.driver.shoot(url, os.path.join(self.out, paths.SHOTS, name))
            if shot.get("strips"):
                rec["screenshot"] = paths.SHOTS + "/" + name
            else:
                rec["screenshot_error"] = shot.get("error")

    # ── the crawl's place, for --resume and add ──
    def save_state(self):
        state = {"version": CACHE_VERSION, "start": self.start, "profile": self.profile, "settings": self.settings,
                 "started": self.started,
                 "records": self.records, "order": self.order, "seen": sorted(self.seen), "skipped": self.skipped,
                 "frontier": [[u, self.frontier.where[u]] for u in self.frontier],
                 "sampler": {"by_template": self.sampler.by_template, "by_section": self.sampler.by_section},
                 "templates": {"children": {k: sorted(v) for k, v in self.templates.children.items()},
                               "labels": self.templates.labels, "nav": sorted(self.templates.nav),
                               "own": sorted(self.templates.own)},
                 "names": self.names, "style_readings": self.style_readings, "limit_reached": self.limit_reached,
                 "throttled": self.pace["throttled"]}
        _dump(os.path.join(self.out, paths.CACHE, "crawl.json"), state, indent=None)

    def load_state(self):
        """The crawl's place from the cache, or None when there is none."""
        state = saved_state(self.out)
        if not state:
            return None
        self.records = {u: dict(inventory.new_record(u), **r) for u, r in state["records"].items()}
        self.order = [n for n in state["order"] if os.path.isfile(self.cache_path(n))]
        self.seen, self.skipped = set(state["seen"]), state["skipped"]
        for u, tier in state["frontier"]:
            self.frontier.add(u, tier)
        self.sampler.by_template.update(state["sampler"]["by_template"])
        self.sampler.by_section.update(state["sampler"]["by_section"])
        t = state["templates"]
        self.templates.children = {k: set(v) for k, v in t["children"].items()}
        self.templates.labels, self.templates.nav, self.templates.own = t["labels"], set(t["nav"]), set(t["own"])
        self.names.update(state["names"])
        for u, n in state["names"].items():
            self.owners[n] = u
        self.style_readings = state["style_readings"]
        self.pace["throttled"] = state.get("throttled", 0)
        self.limit_reached = state.get("limit_reached", False)
        self.started = state.get("started") or self.started
        for n in self.order:
            page = self.cached(n)
            if page:
                self.kept_hashes.add(page["digest"])
                self.near.keep(page["minhash"])
        return state

    def forget(self, url):
        """Before a page is read again (add, import): its earlier copy is no
        duplicate of it."""
        old = self.cached(self.name_for(url))
        if not old:
            return
        self.kept_hashes.discard(old["digest"])
        self.near = NearDuplicates()
        for page in self.pages():
            if page["url"] != url:
                self.near.keep(page["minhash"])

    # ── writing ──
    def pages(self):
        """The pages kept, from the cache, one at a time."""
        for n in self.order:
            page = self.cached(n)
            if page:
                yield page

    def _blocks(self, page, repeated):
        return [dict(b, chrome=True) if b["tag"] != "img" and norm_line(b["text"]) in repeated else b
                for b in page["blocks"]]

    def write(self):
        args, out = self.args, self.out
        # what actually rendered the pages kept, not what was asked for
        engines = sorted({p["fetcher"] for p in self.pages() if p["rendered"] and p["fetcher"] in ("chrome", "obscura")})
        self.renderer = "+".join(engines) or None
        if self.driver and self.driver.note:
            self.browser_note = self.driver.note
        repeated = repeated_lines(p["blocks"] for p in self.pages())
        common_lines, seen_lines = [], set()
        for page in self.pages():
            for t in _landmark_lines(page) + [b["text"] for b in page["blocks"] if b["tag"] != "img" and not b.get("chrome")
                                               and b["text"] and norm_line(b["text"]) in repeated]:
                if norm_line(t) not in seen_lines:
                    seen_lines.add(norm_line(t))
                    common_lines.append(t.strip())
        if common_lines:
            with open(paths.index(out, "common.md"), "w") as f:
                f.write("<!-- the site's header, nav, and footer, and the lines that repeat on most pages "
                        "wherever the theme put them; removed from every page and kept here once. "
                        "furniture.json has the header and footer as structure -->\n\n")
                f.write("\n".join(common_lines) + "\n")

        # The records every page feeds: furniture first (it names the logo),
        # then the pictures, then the facts and reviews.
        furniture_pages, media = [], Media()
        media.load(_load_json(paths.index(out, "media.json")))
        for i, page in enumerate(self.pages()):
            fp = page_furniture(page["parsed"], self.root_host)
            fp["_order"] = i
            furniture_pages.append(fp)
            media.add_page(page["url"], self._blocks(page, repeated), page["parsed"]["images"],
                           (page["parsed"]["meta"] or {}).get("og:image"))
        site_view = site_furniture(furniture_pages)
        for p in furniture_pages:
            p.pop("_order", None)
        _dump(paths.index(out, "furniture.json"), {"site": site_view, "pages": furniture_pages})
        media.classify((site_view.get("logo") or {}).get("src"), paths.host_of(self.start).split(".")[0])
        fetched_images = self.fetch_images(media)

        written, manifest_pages = [], []
        for page in self.pages():
            url, rec = page["url"], self.record(page["url"])
            md = to_markdown(self._blocks(page, repeated))
            if not md.strip():
                self.skip(url, "boilerplate_only")
                continue
            local = {}
            for ref in dict.fromkeys(MD_IMAGE_RE.findall(md)):
                it = media.items.get(media.key_of(urljoin(url, ref)))
                if it and it["file"]:
                    local[ref] = "../%s/%s" % (paths.IMAGES, it["file"])
            md = rewrite_images(md, local)
            fname = "%s/%s.md" % (paths.PAGES, page["name"])
            thin = len(md.strip()) < MIN_MARKDOWN_CHARS
            body_hash = hashlib.sha256(md.encode()).hexdigest()[:16]
            before = self.previous_hashes.get(url, "absent")
            change = "new" if before == "absent" else "same" if before == body_hash else "changed"
            front = {"url": url, "title": rec["title"] or None, "template": rec["template"],
                     **(page.get("front") or {}), "fetched": page["fetched"], "fetcher": page["fetcher"], "hash": body_hash}
            if thin:
                front["thin"] = True
            with open(os.path.join(out, fname), "w") as f:
                f.write(frontmatter(front) + "\n" + md)
            rec["file"], rec["word_count"], rec["thin"] = fname, word_count(md), thin
            written.append(url)
            manifest_pages.append({"url": url, "file": fname, "template": rec["template"], "chars": len(md),
                                   "rendered": page["rendered"], "thin": thin, "hash": body_hash, "change": change})

        # Pages an earlier run wrote and this one did not read (a smaller
        # limit, a page gone from the site): the files stay, listed so the
        # next run and the reader know them.
        read_now = set(written)
        earlier = [{"url": u, "file": f} for u, f in sorted(self.earlier.items())
                   if u not in read_now and os.path.isfile(os.path.join(out, f))]

        _dump(paths.index(out, "media.json"), media.to_json())
        business = structured.merge_business([(p["structured"]["url"], p["structured"]["jsonld"]) for p in self.pages()])
        _dump(os.path.join(self.structured_dir, "business.json"),
              business or {"note": "no LocalBusiness or Organization markup found on the crawled pages"})
        facts, all_reviews, ratings = Facts(), [], []
        facts.from_jsonld(business, self.start)
        for page in self.pages():
            jr, jratings = jsonld_reviews(page["structured"]["jsonld"], page["url"])
            ratings.extend(jratings)
            for r in page["reviews"] + jr:
                if not any(x["quote"] == r["quote"] for x in all_reviews):
                    all_reviews.append(r)
            facts.from_page(page["url"], self.record(page["url"])["template"], self._blocks(page, set()),
                            page["parsed"]["links"], {r["quote"] for r in page["reviews"] + jr})
        facts_json = facts.to_json()
        wordpress = any("wordpress" in ((p["parsed"]["meta"] or {}).get("generator") or "").lower()
                        or any("/wp-content/" in (i.get("src") or "") for i in p["parsed"]["images"])
                        for p in self.pages())
        facts_json["ratings"] = ratings
        _dump(paths.index(out, "facts.json"), facts_json)
        _dump(paths.index(out, "reviews.json"), all_reviews)
        with open(paths.index(out, "reviews.md"), "w") as f:
            f.write(reviews_markdown(all_reviews, ratings, self.start))
        styles = None
        if args.styles and self.style_readings:
            styles = merge_styles(self.style_readings)
            styles["renderer"] = self.renderer
            _dump(paths.index(out, "styles.json"), styles)

        templates = template_report(self.records)
        _dump(paths.index(out, "templates.json"), templates)
        with open(paths.index(out, "templates.md"), "w") as f:
            f.write(templates_markdown(templates, self.start))

        skipped_by = {}
        for s in self.skipped:
            skipped_by[s["reason"]] = skipped_by.get(s["reason"], 0) + 1
        unread = sum(1 for r in self.records.values() if r["reason"] in ("unread", "sampled_out"))
        images_on_disk = sum(1 for i in media.items.values() if i["file"])
        limit = self.settings["limit"]
        manifest = {"start": self.start, "profile": self.profile, "pages": manifest_pages, "earlier": earlier,
                    "skipped": self.skipped, "images": images_on_disk, "renderer": self.renderer, "limit": limit,
                    "limit_reached": self.limit_reached, "discovered": len(self.records), "unread": unread,
                    "common_lines": len(common_lines), "throttled": self.pace["throttled"]}
        _dump(paths.index(out, "manifest.json"), manifest)
        inventory.write(self.records, os.path.join(out, paths.INDEX), self.start,
                        {"limit": limit, "limit_reached": self.limit_reached, "renderer": self.renderer})
        self.save_state()

        not_fetched = {t["template"]: t["not_read"] for t in templates if t["not_read"]}
        run = {"tool": "tt-crawl", "version": __version__, "command": args.command, "profile": self.profile,
               "argv": sys.argv[1:], "start": self.start, "started": self.started, "finished": _now(),
               "renderer": self.renderer, "browser_note": self.browser_note,
               "read": len(manifest_pages), "skipped": skipped_by, "limit": limit,
               "limit_reached": self.limit_reached, "throttled": self.pace["throttled"],
               "per_template": self.sampler.per_template, "per_section": self.sampler.per_section,
               "images": {"mode": args.images, "on_disk": images_on_disk, "fetched_now": fetched_images,
                          "seen": len(media.items)},
               "import": getattr(self, "import_stats", None),
               "changes": {c: sum(1 for p in manifest_pages if p["change"] == c) for c in ("new", "changed", "same")},
               "not_fetched_by_template": not_fetched}
        _dump(paths.index(out, "run.json"), run)
        paths.register_site(out, {"host": paths.host_of(self.start), "start": self.start, "profile": self.profile,
                                  "updated": run["finished"], "pages": len(manifest_pages),
                                  "templates": len(templates), "images": images_on_disk,
                                  "styles": bool(styles) or os.path.isfile(paths.index(out, "styles.json")),
                                  "screenshots": any(r.get("screenshot") for r in self.records.values()),
                                  "reviews": len(all_reviews), "wordpress": wordpress})

        summary = {"pages": len(manifest_pages), "discovered": len(self.records), "unread": unread,
                   "limit": limit, "limit_reached": self.limit_reached, "skipped": len(self.skipped),
                   "thin": sum(1 for p in manifest_pages if p["thin"]), "earlier_kept": len(earlier),
                   "new": run["changes"]["new"], "changed": run["changes"]["changed"],
                   "templates": len(templates), "collections": sum(1 for t in templates if t["collection"]),
                   "throttled": self.pace["throttled"],
                   "screenshots": sum(1 for r in self.records.values() if r.get("screenshot")),
                   "images": images_on_disk, "images_seen": len(media.items),
                   "rendered": sum(1 for p in manifest_pages if p["rendered"]),
                   "common_lines": len(common_lines), "renderer": self.renderer,
                   "inventory": len(self.records), "furniture_landmarks": site_view.get("has_landmarks", False),
                   "nav_items": len(site_view.get("nav", [])), "media": len(media.items),
                   "reviews": len(all_reviews), "facts": {k: len(v) for k, v in facts_json.items() if k in Facts.KINDS},
                   "business_markup": bool(business), "documents": sum(len(r["documents"]) for r in self.records.values()),
                   "styles_pages": (styles or {}).get("pages_read", 0), "out": out}
        if self.browser_note:
            summary["browser_note"] = self.browser_note
        if args.styles and not self.style_readings:
            summary["styles_skipped"] = "no page rendered in the browser"
        print(json.dumps(summary))
        return 0

    def fetch_images(self, media):
        """Each chosen picture once, at its largest: the original first, then
        the widest size the page offered, then the one it showed. A picture a
        run already fetched is reused; the same bytes under two URLs are one
        file."""
        img_dir = os.path.join(self.out, paths.IMAGES)
        os.makedirs(img_dir, exist_ok=True)
        by_bytes = {}
        for k in media.known.values():
            path = os.path.join(img_dir, k.get("file") or "")
            if k.get("file") and os.path.isfile(path):
                with open(path, "rb") as f:
                    by_bytes.setdefault(hashlib.sha256(f.read()).hexdigest(), k["file"])
        fetched = 0
        todo = []
        for it in media.select(self.args.images):
            known = media.known.get(it["key"])
            if known and os.path.isfile(os.path.join(img_dir, known["file"])):
                it.update(known)
            else:
                todo.append(it)

        def download(it):
            """The first candidate that answers with bytes: (url, data, type) or None."""
            for url in media.candidates(it):
                host = urlsplit(url).hostname
                if not host or not net.is_public_host(host):
                    continue
                try:
                    data, ctype = net.fetch_bytes(url, MAX_IMAGE_BYTES)
                except Exception:
                    continue
                if data:
                    return url, data, ctype
            return None

        # Downloads run several at once; naming, de-duplication and writing
        # stay in order, so the same crawl names its files the same way.
        with ThreadPoolExecutor(max_workers=IMAGE_PARALLEL) as pool:
            downloads = list(pool.map(download, todo))
        for it, got in zip(todo, downloads):
            if got:
                url, data, ctype = got
                digest = hashlib.sha256(data).hexdigest()
                fname = by_bytes.get(digest)
                if not fname:
                    fname = image_file_name(it["key"], it["original"], extension(ctype, url))
                    with open(os.path.join(img_dir, fname), "wb") as f:
                        f.write(data)
                    by_bytes[digest] = fname
                    fetched += 1
                size = dimensions(data)
                it.update({"file": fname, "fetched_from": url, "bytes": len(data),
                           "width": size[0] if size else it["width"], "height": size[1] if size else it["height"]})
        # A picture an earlier run fetched stays linked whether or not this
        # run chose it (a content crawl, then a brand one, keeps every file).
        for it in media.items.values():
            known = media.known.get(it["key"])
            if not it["file"] and known and os.path.isfile(os.path.join(img_dir, known["file"])):
                it.update(known)
        return fetched


def frontmatter(fields):
    """YAML frontmatter; strings JSON-quoted, which YAML reads as they are (pure)."""
    lines = ["---"]
    for k, v in fields.items():
        if v is None:
            continue
        lines.append("%s: %s" % (k, "true" if v is True else "false" if v is False else json.dumps(v, ensure_ascii=False)))
    return "\n".join(lines + ["---", ""])


def repeated_lines(pages_blocks):
    """The normalized text lines that are furniture by repetition (pure): on
    FURNITURE_SHARE of the pages or more, and on at least three."""
    counts, n = {}, 0
    for blocks in pages_blocks:
        n += 1
        for key in {norm_line(b["text"]) for b in blocks if b["tag"] != "img" and not b.get("chrome") and b["text"]}:
            counts[key] = counts.get(key, 0) + 1
    if n < 3:
        return set()
    need = max(3, math.ceil(n * FURNITURE_SHARE))
    return {k for k, c in counts.items() if c >= need}


def template_report(records):
    """Every template the crawl knows, biggest first (pure): how many URLs,
    how many read and not, the shapes of the ones read, examples, and the
    average words of a read page (the size of an import, before one)."""
    by = {}
    for r in records.values():
        t = r.get("template")
        if not t:
            continue
        row = by.setdefault(t, {"template": t, "collection": is_collection(t), "count": 0, "read": 0, "not_read": 0,
                                "shapes": {}, "examples": [], "words": 0})
        row["count"] += 1
        if r.get("file"):
            row["read"] += 1
            row["words"] += r.get("word_count") or 0
            if r.get("fingerprint"):
                row["shapes"][r["fingerprint"]] = row["shapes"].get(r["fingerprint"], 0) + 1
        elif r.get("reason") in ("unread", "sampled_out"):
            row["not_read"] += 1
        if len(row["examples"]) < 3:
            row["examples"].append(r["url"])
    out = []
    for row in by.values():
        row["avg_words"] = round(row.pop("words") / row["read"]) if row["read"] else None
        out.append(row)
    out.sort(key=lambda r: (-r["count"], r["template"]))
    return out


def templates_markdown(rows, start):
    lines = ["# Templates: %s" % start, "",
             "A template is a kind of page the site has many of (posts, products, locations); a page the",
             "site has one of is its own. `read` pages are in pages/; the rest can be fetched with",
             "`tt-crawl add URL`. `shapes` counts the different layouts among the pages read: two shapes",
             "in one template can mean two kinds of page share one URL pattern.", "",
             "| template | pages | read | not read | shapes | avg words | example |",
             "|---|---|---|---|---|---|---|"]
    for r in rows:
        if not r["collection"] and r["count"] == 1:
            continue
        lines.append("| %s | %d | %d | %d | %s | %s | %s |" % (
            r["template"], r["count"], r["read"], r["not_read"], len(r["shapes"]) or "",
            r["avg_words"] if r["avg_words"] is not None else "", r["examples"][0] if r["examples"] else ""))
    singles = [r for r in rows if not r["collection"] and r["count"] == 1]
    lines += ["", "%d single pages (their own template each); see inventory.md." % len(singles)]
    return "\n".join(lines) + "\n"


def reviews_markdown(rows, ratings, start):
    lines = ["# Reviews on %s" % start, "",
             "Each as the site shows it: the words, the name, the date and platform when the card says them,",
             "and the page it is on. Quotes are verbatim; nothing here is a summary.", ""]
    for r in ratings:
        lines.append("Rating stated in the site's markup: %s%s (%s)" % (
            r.get("value"), " from %s reviews" % r["count"] if r.get("count") else "", r["url"]))
    if ratings:
        lines.append("")
    for r in rows:
        meta = ", ".join(str(x) for x in (r.get("date"), r.get("platform"),
                                          "%s stars" % r["stars"] if r.get("stars") else None) if x)
        lines += ["> %s" % r["quote"], "> — %s%s  " % (r.get("name") or "unnamed", " (%s)" % meta if meta else ""),
                  "> %s" % r["url"], ""]
    if not rows:
        lines.append("No reviews found on the pages read.")
    return "\n".join(lines) + "\n"


def _landmark_lines(page):
    out = []
    for lm in ("header", "nav", "footer", "aside"):
        out.extend(t.strip() for t in page["parsed"]["landmark_lines"].get(lm, []) if t.strip())
    return out


def saved_state(out):
    """The crawl's place a folder's cache holds, or None."""
    state = _load_json(os.path.join(out, paths.CACHE, "crawl.json"))
    return state if state and state.get("version") == CACHE_VERSION else None


def folder_settings(state):
    """What the crawl that made a folder chose, which `add` and `import`
    keep: its profile, its pictures and its page limit (None when it
    recorded none). A page added to a brand folder fetches brand pictures,
    not every picture on it."""
    s = state.get("settings")
    if not s or not s.get("images"):
        return None
    return state["profile"], {"images": s["images"], "limit": s.get("limit")}


def into_folder(args, command):
    """For `add` and `import`: the folder a crawl wrote (--out, else the one
    raw/site/<host>), its saved state, and the settings it keeps; or None
    after saying why."""
    args.out = args.out or paths.the_site()
    state = saved_state(args.out) if args.out else None
    if not state:
        sys.stderr.write("%s needs --out: a folder a crawl wrote (it reads %s/crawl.json)\n" % (command, paths.CACHE))
        return None
    args.start_url = state["start"]
    kept = folder_settings(state)
    if not kept:
        sys.stderr.write("%s has no recorded crawl settings; crawl it again\n" % args.out)
        return None
    profile, settings = kept
    args.images = args.images or settings["images"]
    args.max_pages = settings["limit"]
    return profile, settings


def _start_run(args, profile, body, settings=None):
    start = net.normalize_url(args.start_url)
    root_host = urlsplit(start or "").hostname or ""
    if not start or not root_host or not net.is_public_host(root_host):
        sys.stderr.write("refusing: start host is missing or not a public address\n")
        return 2
    args.out = args.out or paths.site_dir(start, external=args.external)
    os.makedirs(args.out, exist_ok=True)
    crawl = Crawl(args, start, profile, settings)

    def throttled(url, status, wait):
        crawl.pace["throttled"] += 1
        crawl.pace["delay"] = min(MAX_DELAY_S, max(crawl.pace["delay"], 0.5) * 2)
        sys.stderr.write("%s %s: throttled; waiting %ds, then %.1fs between pages\n" % (status, url, wait, crawl.pace["delay"]))

    net.on_throttle = throttled
    try:
        return body(crawl)
    finally:
        net.on_throttle = None
        for d in [crawl.driver] + crawl.extra_drivers:
            if d:
                d.stop()


def run(args):
    def body(crawl):
        # Only the same site's crawl is carried on (www. or not, any start
        # path): another site's place would seed this one with its pages.
        saved = saved_state(crawl.out) if args.resume else None
        same_site = lambda a, b: (urlsplit(a or "").hostname or "").removeprefix("www.") == (urlsplit(b or "").hostname or "").removeprefix("www.")
        if saved and same_site(saved.get("start"), crawl.start):
            crawl.load_state()
            crawl.limit_reached = False
            crawl.robots, _ = load_robots(crawl.start)
            sys.stderr.write("resuming: %d pages read, %d waiting\n" % (len(crawl.order), len(crawl.frontier)))
        else:
            if args.resume:
                sys.stderr.write("nothing to resume for %s in %s; starting fresh\n" % (crawl.start, crawl.out))
            crawl.seed()
        crawl.loop()
        return crawl.write()
    return _start_run(args, args.profile, body)


def run_add(args):
    found = into_folder(args, "add")
    if not found:
        return 2
    profile, settings = found

    def body(crawl):
        crawl.load_state()
        crawl.robots, _ = load_robots(crawl.start)
        for i, raw in enumerate(args.urls):
            url = net.normalize_url(urljoin(crawl.start, raw))
            if not url or not net.same_site(url, crawl.root_host):
                sys.stderr.write("skipping %s: not on %s\n" % (raw, crawl.root_host))
                continue
            crawl.seen.add(url)
            rec = crawl.record(url)
            crawl.templates.add(url)
            rec["template"] = crawl.templates.of(url)
            rec["reason"] = None
            if not crawl.robots.can_fetch(net.USER_AGENT, url):
                crawl.skip(url, "robots")
                continue
            if i:
                time.sleep(crawl.pace["delay"])
            crawl.skipped = [s for s in crawl.skipped if s["url"] != url]
            crawl.forget(url)
            crawl.read(url)
        return crawl.write()
    return _start_run(args, profile, body, settings)


IMAGES_HELP = ("which pictures to fetch: none; brand (the logo, the og:image and the 60 photographs the most pages "
               "show); content (every picture in the pages' own content); all (content, and the header's and "
               "footer's too)")


def _read_args(p):
    """How pages are read, for every command that reads them."""
    p.add_argument("--browser", choices=("chrome", "obscura"), default="chrome",
                   help="the browser that renders the pages and takes screenshots (default chrome, installed on "
                        "first use where it can be; obscura when chrome cannot be had)")
    p.add_argument("--static", action="store_true", help="no browser at all: plain fetches, no screenshots, no styles")


def _crawl_args(p, max_pages, images):
    p.add_argument("start_url")
    p.add_argument("--out", default=None, help="the site's folder (default raw/site/<host>, or raw/external/<host> with --external)")
    p.add_argument("--external", action="store_true", help="someone else's site: raw/external/<host> by default")
    p.add_argument("--max-pages", type=int, default=max_pages,
                   help=f"pages to read (default {max_pages}; the summary says how many were found)")
    p.add_argument("--images", choices=MODES, default=images, help=IMAGES_HELP + " (default %s)" % images)
    p.add_argument("--delay", type=float, default=0.5, help="seconds between two rounds of pages (default 0.5)")
    p.add_argument("--parallel", type=int, default=PARALLEL,
                   help="pages read at once, each in its own browser (default %d; 1 after the site throttles)" % PARALLEL)
    _read_args(p)
    p.add_argument("--screenshots", action="store_true", help="the whole page as PNG strips under shots/<name>/")
    p.add_argument("--screenshot-pages", type=int, default=None, help="how many pages are screenshot, the first read (default all; brand 5)")
    p.add_argument("--styles", action="store_true", help="read computed styles off the first pages into _index/styles.json (browser only)")
    p.add_argument("--style-pages", type=int, default=STYLE_PAGES, help="how many pages styles are read off (default %d)" % STYLE_PAGES)
    p.add_argument("--resume", action="store_true", help="carry on from where an interrupted crawl of the same site stopped")


def folder_args(p):
    """`add` and `import`: into a folder a crawl wrote, keeping its settings."""
    p.add_argument("--out", default=None, help="the folder a crawl wrote (default: the one raw/site/<host>)")
    p.add_argument("--images", choices=MODES, default=None, help=IMAGES_HELP + " (default: what the folder's crawl chose)")
    _read_args(p)
    p.set_defaults(external=False, screenshots=False, screenshot_pages=None, styles=False, style_pages=0, delay=0.5,
                   per_template=None, per_section=None)


def add_parser(sub):
    p = sub.add_parser("site", help="read a site into raw/site/<host>: pages, pictures, inventory, templates, facts")
    _crawl_args(p, DEFAULT_MAX_PAGES, "content")
    p.set_defaults(func=run, profile="site", per_template=None, per_section=None)

    p = sub.add_parser("survey", help="sample a big site: every URL listed by template, two of each read, no pictures fetched")
    _crawl_args(p, DEFAULT_MAX_PAGES, "none")
    p.set_defaults(func=run, profile="survey", per_template=2, per_section=6)

    p = sub.add_parser("brand", help="a business's own site for its facts, voice and look (tt-crawl playbook brand)")
    _crawl_args(p, DEFAULT_MAX_PAGES, "brand")
    p.set_defaults(func=run, profile="brand", per_template=2, per_section=6, styles=True, screenshots=True,
                   screenshot_pages=BRAND_SHOT_PAGES)

    p = sub.add_parser("pages", help="a whole site for a rebuild: every page and picture (tt-crawl playbook rebuild)")
    _crawl_args(p, 1000, "content")
    p.set_defaults(func=run, profile="pages", per_template=None, per_section=None)

    p = sub.add_parser("reference", help="a site the owner admires: a few pages' look and structure (tt-crawl playbook reference)")
    _crawl_args(p, 8, "none")
    p.set_defaults(func=run, profile="reference", per_template=1, per_section=3,
                   external=True, styles=True, screenshots=True)

    p = sub.add_parser("add", help="read more pages into a folder a crawl already wrote, and write it again")
    p.add_argument("urls", nargs="+", metavar="URL")
    folder_args(p)
    p.set_defaults(func=run_add)
