"""`tt-crawl site`, `tt-crawl survey` and `tt-crawl add`: read a site into its
folder (paths.py has the layout).

A crawl reads pages, then writes everything from what it read:

- **read**: the start page, then what the site's own header and nav link
  to, then what the home page links to, then the footer's links, then the
  sitemap, then everything else found along the way (templates.Frontier),
  capped per template and per section when sampling (templates.Sampler).
  Each page is asked for plainly first (status, redirects, throttling), then
  rendered once through Obscura when it is installed (static otherwise), and
  parsed into its content blocks, links, forms, images, reviews and
  structured data. Each page read is saved to `_cache/pages/` as it is read,
  and the crawl's place every few pages, so a long crawl can `--resume`.
- **write**: the page files (frontmatter, then the page's own text,
  verbatim), the pictures (each once, at its largest, linked from every page
  that shows it), and the ledger in `_index/`: inventory, templates,
  furniture, media, facts, reviews, styles, common lines, the run and the
  manifest. `add` reads more pages into a folder and writes it all again
  from the cache, without reading the rest.

No step involves a model: a crawl of thousands of pages costs the machine's
time and nothing else.
"""
import argparse
import hashlib
import json
import math
import os
import re
import sys
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urljoin, urlsplit

from . import __version__, browser, chrome, dom, firecrawl, inventory, net, paths, structured
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
MAX_VIDEO_BYTES = 80 * 1024 * 1024
MAX_SITEMAPS = 50
STYLE_PAGES = 5
MAX_DELAY_S = 8
STATE_EVERY = 10             # pages between two saves of the crawl's place
CACHE_VERSION = 2
# A line on this share of the pages read (and on at least three) is the
# site's furniture wherever the theme put it: a top bar, a skip link, a
# sitewide banner. A call to action on every service page is content.
FURNITURE_SHARE = 0.6
# What the parsed page keeps in the cache: enough to write the folder again.
PARSED_KEEP = ("url", "title", "lang", "meta", "meta_description", "canonical", "links", "internal_links",
               "images", "forms", "landmark_lines", "has_landmarks", "h1", "videos")


def load_robots(start_url, ignore):
    from urllib import robotparser
    rp = robotparser.RobotFileParser()
    if ignore:
        rp.parse(["User-agent: *", "Allow: /"])
        return rp, []
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

    def __init__(self, args, start, profile):
        self.args, self.start, self.profile = args, start, profile
        self.root_host = urlsplit(start).hostname or ""
        self.out = args.out
        self.firecrawl = firecrawl.Client.from_env() if getattr(args, "fetcher", "local") == "firecrawl" else None
        self.obscura = None if args.static or self.firecrawl else browser.find_obscura()
        self.structured_dir = getattr(args, "structured_out", None) or os.path.join(self.out, paths.STRUCTURED)
        manifest = _load_json(paths.index(self.out, "manifest.json")) or {}
        self.earlier = {p["url"]: p["file"] for p in (manifest.get("earlier") or []) + (manifest.get("pages") or [])
                        if p.get("url") and p.get("file")}
        self.previous_hashes = {p["url"]: p.get("hash") for p in manifest.get("pages") or [] if p.get("url")}
        self.shooter = chrome.shooter(getattr(args, "shots_engine", "auto"), self.obscura) \
            if getattr(args, "screenshots", False) else None
        # A site that throttles us once is asked more gently for the rest of the run.
        self.pace = {"delay": args.delay, "throttled": 0}
        self.records = {start: inventory.new_record(start)}
        self.order = []              # names of the pages kept, in reading order (their data is in _cache)
        self.skipped = []
        self.templates = Templates()
        self.frontier = Frontier()
        self.sampler = Sampler(getattr(args, "per_template", None), getattr(args, "per_section", None))
        self.seen = set()
        self.kept_hashes, self.near = set(), NearDuplicates()
        self.style_readings = []
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
        self.robots, robots_sitemaps = load_robots(self.start, self.args.ignore_robots)
        self.frontier.add(self.start, Frontier.START)
        self.templates.add(self.start)
        if self.firecrawl:
            # Firecrawl's map: every URL it knows, one call, beside the sitemap
            try:
                for u in self.firecrawl.map(self.start):
                    n = net.normalize_url(u)
                    if n and net.same_site(n, self.root_host) and net.crawlable(n):
                        self.record(n)
                        self.templates.add(n)
                        self.frontier.add(n, Frontier.SITEMAP)
            except firecrawl.FirecrawlError as e:
                if e.fatal:
                    raise
                sys.stderr.write("firecrawl map: %s\n" % e)
        if not self.args.no_sitemap:
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
    def loop(self):
        requested = 0
        while len(self.frontier):
            if len(self.order) >= self.args.max_pages:
                self.limit_reached = True
                break
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
            # Between every two requests, kept or not, at the pace the site allows.
            if requested:
                time.sleep(self.pace["delay"])
            requested += 1
            self.read(url, from_start=(url == self.start))
            if self.record(url)["reason"] == "redirect":
                self.sampler.refund(url, template)
            if requested % STATE_EVERY == 0:
                self.save_state()
        for url in self.frontier:
            rec = self.record(url)
            rec["template"] = rec["template"] or self.templates.of(url)
            if rec["reason"] is None:
                rec["reason"] = "unread"
        self.save_state()

    def read(self, url, from_start=False):
        """Read one page: kept (saved to the cache), or skipped with its reason."""
        got = self.get(url)
        if got is None:
            return
        html, rendered, styles, fetcher = got
        self.absorb(url, html, rendered=rendered, styles=styles, fetcher=fetcher, from_start=from_start)

    def get(self, url):
        """The page's HTML, or None (skipped, with the reason recorded).
        Through Firecrawl when --fetcher firecrawl; else the plain request
        first, for the status, the final URL and the headers (and the page
        itself when there is no browser): a redirect, an error or a throttled
        answer then costs no render. Then one render through Obscura."""
        rec = self.record(url)
        if self.firecrawl:
            try:
                page = self.firecrawl.scrape(url)
            except firecrawl.FirecrawlError as e:
                if e.fatal:
                    raise
                return self.skip(url, "firecrawl_failed")
            resp = {"status": page["status"], "final_url": page["final_url"], "headers": {}, "body": b""}
        else:
            try:
                resp = net.fetch(url, method="GET" if self.obscura is None else "HEAD")
            except Exception:
                resp = None
            if self.obscura and (resp is None or resp["status"] == 405):
                # some servers refuse or drop a HEAD; ask once more the plain way
                try:
                    resp = net.fetch(url)
                except Exception:
                    resp = None
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
        if self.firecrawl:
            return (page["html"], True, None, "firecrawl") if page["html"].strip() else self.skip(url, "fetch_failed")

        # One render: the page's HTML as the browser built it, annotated with
        # what only the browser knows, and the computed styles when asked.
        html, rendered, styles = None, False, None
        want_styles = self.args.styles and len(self.style_readings) < self.args.style_pages
        if self.obscura:
            got = browser.extract(url, self.obscura, styles=want_styles)
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
        return html, rendered, styles, "obscura" if rendered else "static"

    def absorb(self, url, html, rendered=False, styles=None, fetcher="static", from_start=False, front=None):
        """Keep a page read by any route (a render, a static fetch, Firecrawl,
        or an item a platform's own feed carried): its structured data, its
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
        if self.shooter:  # only the pages kept: a duplicate costs no capture
            shot = self.shooter.shoot(url, os.path.join(self.out, paths.SHOTS, name))
            if shot.get("strips"):
                rec["screenshot"] = paths.SHOTS + "/" + name
            else:
                rec["screenshot_error"] = shot.get("error")

    # ── the crawl's place, for --resume and add ──
    def save_state(self):
        state = {"version": CACHE_VERSION, "start": self.start, "profile": self.profile, "started": self.started,
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
        state = _load_json(os.path.join(self.out, paths.CACHE, "crawl.json"))
        if not state or state.get("version") != CACHE_VERSION:
            return False
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
        self.started = state.get("started") or self.started
        for n in self.order:
            page = self.cached(n)
            if page:
                self.kept_hashes.add(page["digest"])
                self.near.keep(page["minhash"])
        return True

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
        if self.args.keep_boilerplate:
            return page["blocks"]
        return [dict(b, chrome=True) if b["tag"] != "img" and norm_line(b["text"]) in repeated else b
                for b in page["blocks"]]

    def write(self):
        args, out = self.args, self.out
        keep_chrome = args.keep_boilerplate
        repeated = set() if keep_chrome else repeated_lines(p["blocks"] for p in self.pages())
        common_lines, seen_lines = [], set()
        for page in self.pages():
            for t in _landmark_lines(page) + [b["text"] for b in page["blocks"] if b["tag"] != "img" and not b.get("chrome")
                                               and b["text"] and norm_line(b["text"]) in repeated]:
                if norm_line(t) not in seen_lines:
                    seen_lines.add(norm_line(t))
                    common_lines.append(t.strip())
        if common_lines and not keep_chrome:
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
        videos = self.fetch_videos() if getattr(args, "videos", False) else []

        written, manifest_pages = [], []
        for page in self.pages():
            url, rec = page["url"], self.record(page["url"])
            md = to_markdown(self._blocks(page, repeated), keep_chrome=keep_chrome)
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

        _dump(paths.index(out, "media.json"), media.to_json() + videos)
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
            styles["renderer"] = self.obscura
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
        manifest = {"start": self.start, "profile": self.profile, "pages": manifest_pages, "earlier": earlier,
                    "skipped": self.skipped, "images": images_on_disk, "renderer": self.obscura, "limit": args.max_pages,
                    "limit_reached": self.limit_reached, "discovered": len(self.records), "unread": unread,
                    "common_lines": len(common_lines), "throttled": self.pace["throttled"],
                    "structured_dir": self.structured_dir}
        _dump(paths.index(out, "manifest.json"), manifest)
        inventory.write(self.records, os.path.join(out, paths.INDEX), self.start,
                        {"limit": args.max_pages, "limit_reached": self.limit_reached, "renderer": self.obscura})
        self.save_state()

        not_fetched = {t["template"]: t["not_read"] for t in templates if t["not_read"]}
        run = {"tool": "tt-crawl", "version": __version__, "profile": self.profile, "argv": sys.argv[1:],
               "start": self.start, "started": self.started, "finished": _now(), "renderer": self.obscura,
               "read": len(manifest_pages), "skipped": skipped_by, "limit": args.max_pages,
               "limit_reached": self.limit_reached, "throttled": self.pace["throttled"],
               "per_template": self.sampler.per_template, "per_section": self.sampler.per_section,
               "images": {"mode": args.images, "on_disk": images_on_disk, "fetched_now": fetched_images,
                          "seen": len(media.items)},
               "fetcher": "firecrawl" if self.firecrawl else "local",
               "import": getattr(self, "import_stats", None),
               "firecrawl_calls": self.firecrawl.calls if self.firecrawl else None,
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
                   "limit": args.max_pages, "limit_reached": self.limit_reached, "skipped": len(self.skipped),
                   "thin": sum(1 for p in manifest_pages if p["thin"]), "earlier_kept": len(earlier),
                   "new": run["changes"]["new"], "changed": run["changes"]["changed"],
                   "templates": len(templates), "collections": sum(1 for t in templates if t["collection"]),
                   "throttled": self.pace["throttled"],
                   "screenshots": sum(1 for r in self.records.values() if r.get("screenshot")),
                   "images": images_on_disk, "images_seen": len(media.items),
                   "rendered": sum(1 for p in manifest_pages if p["rendered"]),
                   "common_lines": len(common_lines), "renderer": self.obscura,
                   "inventory": len(self.records), "furniture_landmarks": site_view.get("has_landmarks", False),
                   "nav_items": len(site_view.get("nav", [])), "media": len(media.items),
                   "reviews": len(all_reviews), "facts": {k: len(v) for k, v in facts_json.items() if k in Facts.KINDS},
                   "business_markup": bool(business), "documents": sum(len(r["documents"]) for r in self.records.values()),
                   "styles_pages": (styles or {}).get("pages_read", 0), "out": out}
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
        for it in media.select(self.args.images, self.args.max_images):
            known = media.known.get(it["key"])
            if known and os.path.isfile(os.path.join(img_dir, known["file"])):
                it.update(known)
                continue
            for url in media.candidates(it):
                host = urlsplit(url).hostname
                if not host or not net.is_public_host(host):
                    continue
                try:
                    data, ctype = net.fetch_bytes(url, MAX_IMAGE_BYTES)
                except Exception:
                    continue
                if not data:
                    continue
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
                break
        # A picture an earlier run fetched stays linked whether or not this
        # run chose it (a content crawl, then a brand one, keeps every file).
        for it in media.items.values():
            known = media.known.get(it["key"])
            if not it["file"] and known and os.path.isfile(os.path.join(img_dir, known["file"])):
                it.update(known)
        return fetched

    def fetch_videos(self):
        """With --videos: each video file a page plays, under 80 MB. Players
        (YouTube, Vimeo) are recorded in the structured data, never fetched."""
        out, seen = [], set()
        vid_dir = os.path.join(self.out, "videos")
        for page in self.pages():
            for src in page["parsed"].get("videos") or []:
                if src in seen:
                    continue
                seen.add(src)
                row = {"key": src, "kind": "video", "original": src, "file": None, "pages": [{"url": page["url"]}]}
                host = urlsplit(src).hostname
                data = ctype = None
                if host and net.is_public_host(host):
                    try:
                        data, ctype = net.fetch_bytes(src, MAX_VIDEO_BYTES, content_types=("video/", "application/octet-stream"))
                    except Exception:
                        data = None
                if data:
                    os.makedirs(vid_dir, exist_ok=True)
                    fname = image_file_name(src, src, extension(ctype, src))
                    with open(os.path.join(vid_dir, fname), "wb") as f:
                        f.write(data)
                    row.update({"file": "../videos/" + fname, "bytes": len(data)})
                out.append(row)
        return out


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


def _start_run(args, profile, body):
    start = net.normalize_url(args.start_url)
    root_host = urlsplit(start or "").hostname or ""
    if not start or not root_host or not net.is_public_host(root_host):
        sys.stderr.write("refusing: start host is missing or not a public address\n")
        return 2
    args.out = args.out or paths.site_dir(start, external=getattr(args, "external", False))
    if paths.old_layout(args.out):
        sys.stderr.write("%s was written by tt-crawl before 0.2 (pages and ledger at its root); move it to the new "
                         "layout first with `tt-crawl relayout %s`\n" % (args.out, args.out))
        return 2
    note = paths.frozen(args.out)
    if note:
        sys.stderr.write("%s is frozen (%s%s): it keeps the site as it was. Crawl into another folder with --out.\n"
                         % (args.out, note.get("frozen_at"), ", " + note["reason"] if note.get("reason") else ""))
        return 2
    os.makedirs(args.out, exist_ok=True)
    try:
        crawl = Crawl(args, start, profile)
    except firecrawl.FirecrawlError as e:
        sys.stderr.write("stopped: %s\n" % e)
        return 3

    def throttled(url, status, wait):
        crawl.pace["throttled"] += 1
        crawl.pace["delay"] = min(MAX_DELAY_S, max(crawl.pace["delay"], 0.5) * 2)
        sys.stderr.write("%s %s: throttled; waiting %ds, then %.1fs between pages\n" % (status, url, wait, crawl.pace["delay"]))

    net.on_throttle = throttled
    try:
        return body(crawl)
    except firecrawl.FirecrawlError as e:
        sys.stderr.write("stopped: %s\n" % e)
        crawl.save_state()
        return 3
    finally:
        net.on_throttle = None
        if crawl.shooter:
            crawl.shooter.stop()


def run(args):
    def body(crawl):
        if args.resume and crawl.load_state() and crawl.start == net.normalize_url(args.start_url):
            crawl.robots, _ = load_robots(crawl.start, args.ignore_robots)
            sys.stderr.write("resuming: %d pages read, %d waiting\n" % (len(crawl.order), len(crawl.frontier)))
        else:
            if args.resume:
                sys.stderr.write("nothing to resume in %s; starting fresh\n" % crawl.out)
            crawl.seed()
        crawl.loop()
        return crawl.write()
    return _start_run(args, args.profile, body)


def run_add(args):
    out = args.out
    state = _load_json(os.path.join(out or "", paths.CACHE, "crawl.json")) if out else None
    if not state:
        sys.stderr.write("add needs --out: a folder a crawl wrote (it reads %s/crawl.json)\n" % paths.CACHE)
        return 2
    args.start_url = state["start"]

    def body(crawl):
        crawl.load_state()
        crawl.robots, _ = load_robots(crawl.start, args.ignore_robots)
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
    return _start_run(args, "add", body)


def _common_args(p, max_pages, images):
    p.add_argument("--out", default=None, help="the site's folder (default raw/site/<host>, or raw/external/<host> with --external)")
    p.add_argument("--structured-out", default=None, help="where the per-page JSON goes (default <out>/structured)")
    p.add_argument("--max-pages", type=int, default=max_pages,
                   help=f"pages to read (default {max_pages}; the summary says how many were found)")
    p.add_argument("--images", choices=MODES, default=images,
                   help="which pictures to fetch: none; brand (the logo, the og:image and the 60 photos most pages "
                        "show); content (every picture in the pages' own content); all (default %s)" % images)
    p.add_argument("--max-images", type=int, default=0, help="fetch at most N pictures (default 0: no cap)")
    p.add_argument("--videos", action="store_true", help="also fetch the video files pages play (under 80 MB each)")
    p.add_argument("--delay", type=float, default=0.5)
    p.add_argument("--fetcher", choices=("local", "firecrawl"), default="local",
                   help="local (this machine, the default) or firecrawl (the owner's Firecrawl credits, FIRECRAWL_API_KEY)")
    p.add_argument("--static", "--no-render", action="store_true", dest="static", help="plain fetches only, never the browser")
    p.add_argument("--screenshots", action="store_true", help="the whole page as PNG strips under shots/<name>/")
    p.add_argument("--shots-engine", choices=("auto", "chrome", "obscura"), default="auto",
                   help="who takes them: Chrome (installed on first use where it can be) paints as people's browsers "
                        "do; Obscura is the fallback (default auto)")
    p.add_argument("--styles", action="store_true", help="read computed styles off the first pages into _index/styles.json (browser only)")
    p.add_argument("--style-pages", type=int, default=STYLE_PAGES)
    p.add_argument("--keep-boilerplate", action="store_true", help="keep the header, footer and repeated lines in every page")
    p.add_argument("--ignore-robots", action="store_true")


def _crawl_args(p):
    p.add_argument("start_url")
    p.add_argument("--external", action="store_true", help="someone else's site: raw/external/<host> by default")
    p.add_argument("--no-sitemap", action="store_true")
    p.add_argument("--resume", action="store_true", help="carry on from where an interrupted crawl into the same folder stopped")


def add_parser(sub):
    p = sub.add_parser("site", help="read a site into raw/site/<host>: pages, pictures, inventory, templates, facts")
    _crawl_args(p)
    _common_args(p, DEFAULT_MAX_PAGES, "content")
    p.add_argument("--per-template", type=int, default=None, help="read at most N pages of any one template (default: no cap)")
    p.add_argument("--per-section", type=int, default=None, help="read at most N pages under any one top-level path (default: no cap)")
    p.set_defaults(func=run, profile="site")

    p = sub.add_parser("survey", help="sample a big site: every URL listed by template, two of each read, no pictures fetched")
    _crawl_args(p)
    _common_args(p, DEFAULT_MAX_PAGES, "none")
    p.add_argument("--per-template", type=int, default=2, help="pages read per template (default 2)")
    p.add_argument("--per-section", type=int, default=6, help="pages read per top-level path (default 6)")
    p.set_defaults(func=run, profile="survey")

    p = sub.add_parser("brand", help="a business's own site for its facts, voice and look (tt-crawl playbook brand)")
    _crawl_args(p)
    _common_args(p, DEFAULT_MAX_PAGES, "brand")
    p.add_argument("--per-template", type=int, default=2, help="pages read per template (default 2)")
    p.add_argument("--per-section", type=int, default=6, help="pages read per top-level path (default 6)")
    p.set_defaults(func=run, profile="brand", styles=True, screenshots=True)

    p = sub.add_parser("pages", help="a whole site for a rebuild: every page and picture (tt-crawl playbook rebuild)")
    _crawl_args(p)
    _common_args(p, 1000, "content")
    p.add_argument("--per-template", type=int, default=None, help=argparse.SUPPRESS)
    p.add_argument("--per-section", type=int, default=None, help=argparse.SUPPRESS)
    p.set_defaults(func=run, profile="pages")

    p = sub.add_parser("reference", help="a site the owner admires: a few pages' look and structure (tt-crawl playbook reference)")
    _crawl_args(p)
    _common_args(p, 8, "none")
    p.add_argument("--per-template", type=int, default=1, help=argparse.SUPPRESS)
    p.add_argument("--per-section", type=int, default=3, help=argparse.SUPPRESS)
    p.set_defaults(func=run, profile="reference", external=True, styles=True, screenshots=True)

    p = sub.add_parser("add", help="read more pages into a folder a crawl already wrote, and write it again")
    p.add_argument("urls", nargs="+", metavar="URL")
    _common_args(p, 10_000, "content")
    p.set_defaults(func=run_add, profile="add", no_sitemap=True, resume=False, external=False)
