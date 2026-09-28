"""`tt-crawl site`: read a whole site into raw/web.

Starting from one URL it discovers every same-site page (the sitemap first,
then every link on every page), renders each through the Obscura browser
when it is installed (static fetch otherwise), extracts the content as
markdown, reads the header and footer as structure and strips their lines
from the page bodies, pulls the content images locally, drops duplicates,
and writes the inventory, furniture, media, and structured-data records
beside the pages. The page limit is deliberate and visible: the summary
says how many pages were found versus read.
"""
import hashlib
import json
import os
import re
import sys
import time
from email.utils import parsedate_to_datetime
from urllib.parse import urljoin, urlsplit

from . import browser, cdp, inventory, net, structured
from .furniture import landmark_line_set, page_furniture, site_furniture
from .html import parse_page
from .media import build_media
from .styles import merge_styles
from .text import (MD_IMAGE_RE, MIN_MARKDOWN_CHARS, content_digest, ext_for, image_key, near_duplicate,
                   page_name, rewrite_images, strip_common_lines, strip_lines, word_count)

DEFAULT_MAX_PAGES = 100
MAX_IMAGE_BYTES = 15 * 1024 * 1024
MAX_SITEMAPS = 10
STYLE_PAGES = 5
MAX_DELAY_S = 8


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
    """Every same-site page URL the site's sitemaps list, with lastmod."""
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
            if loc.lower().endswith(".xml") and "sitemap" in loc.lower():
                todo.append(loc)
            elif net.same_site(loc, root_host):
                found.append((loc, lastmod))
    return found


def run(args):
    start = net.normalize_url(args.start_url)
    root_host = urlsplit(start or "").hostname or ""
    if not start or not root_host or not net.is_public_host(root_host):
        sys.stderr.write("refusing: start host is missing or not a public address\n")
        return 2

    obscura = None if args.static else browser.find_obscura()
    out = args.out
    img_dir = os.path.join(out, "images")
    os.makedirs(img_dir, exist_ok=True)
    earlier = earlier_files(out)
    shooter = cdp.Shooter(obscura) if args.screenshots and obscura else None
    # A site that throttles us once is asked more gently for the rest of the run.
    pace = {"delay": args.delay, "throttled": 0}

    def throttled(url, status, wait):
        pace["throttled"] += 1
        pace["delay"] = min(MAX_DELAY_S, max(pace["delay"], 0.5) * 2)
        sys.stderr.write("%s %s: throttled; waiting %ds, then %.1fs between pages\n" % (status, url, wait, pace["delay"]))

    net.on_throttle = throttled
    try:
        return _crawl(args, start, root_host, obscura, out, img_dir, earlier, shooter, pace)
    finally:
        net.on_throttle = None
        if shooter:
            shooter.stop()


def earlier_files(out):
    """{url: file} for the pages an earlier run wrote into `out`, so a re-crawl
    refreshes each page in place under the same name."""
    try:
        with open(os.path.join(out, "_manifest.json")) as f:
            manifest = json.load(f)
    except (OSError, ValueError):
        return {}
    pages = (manifest.get("earlier") or []) + (manifest.get("pages") or [])
    return {p["url"]: p["file"] for p in pages if p.get("url") and p.get("file")}


def _crawl(args, start, root_host, obscura, out, img_dir, earlier, shooter, pace):
    import trafilatura

    robots, robots_sitemaps = load_robots(start, args.ignore_robots)
    # The harvest sits beside the owner's crawl (raw/web -> raw/structured);
    # any other crawl keeps it inside its own folder.
    if args.structured_out is not None:
        structured_dir = args.structured_out
    elif os.path.basename(out.rstrip("/")) == "web":
        structured_dir = os.path.join(os.path.dirname(out.rstrip("/")) or ".", "structured")
    else:
        structured_dir = os.path.join(out, "_structured")
    os.makedirs(structured_dir, exist_ok=True)

    records = {start: inventory.new_record(start)}
    queue = [start]
    if not args.no_sitemap:
        for u, lastmod in sitemap_urls(start, robots_sitemaps, root_host):
            n = net.normalize_url(u)
            if not n:
                continue
            rec = records.setdefault(n, inventory.new_record(n))
            rec["in_sitemap"], rec["lastmod"] = True, lastmod or rec["lastmod"]
            if n not in queue:
                queue.append(n)
    discovered = set(queue)
    seen = set()
    kept = []                       # [{url, md, rendered, parsed, html}]
    kept_hashes, kept_shingles = set(), []
    parsed_pages, furniture_pages, structured_pages, style_readings = [], [], [], []
    manifest = {"start": start, "pages": [], "skipped": [], "images": 0,
                "renderer": obscura, "limit": args.max_pages, "limit_reached": False}

    def skip(url, reason):
        manifest["skipped"].append({"url": url, "reason": reason})
        records[url]["reason"] = reason

    def enqueue(links):
        for l in links:
            n = net.normalize_url(l)
            if n and net.same_site(n, root_host) and n not in discovered and net.crawlable(n):
                discovered.add(n)
                records.setdefault(n, inventory.new_record(n))
                queue.append(n)

    # One name per URL for its page file, its structured JSON and its
    # screenshots: the name an earlier run gave it, else its own page_name,
    # hashed when another URL already holds that one.
    owners = {os.path.splitext(f)[0]: u for u, f in earlier.items()}
    names = {}

    def name_for(url):
        if url not in names:
            stem = os.path.splitext(earlier[url])[0] if url in earlier else page_name(url)
            if owners.get(stem, url) != url:
                stem = page_name(url, hashed=True)
            names[url], owners[stem] = stem, url
        return names[url]

    requested = 0
    while queue:
        if len(kept) >= args.max_pages:
            manifest["limit_reached"] = True
            break
        url = queue.pop(0)
        if url in seen:
            continue
        seen.add(url)
        rec = records[url]
        if not robots.can_fetch(net.USER_AGENT, url):
            skip(url, "robots")
            continue
        # Between every two requests, kept or not, at the pace the site allows.
        if requested:
            time.sleep(pace["delay"])
        requested += 1

        # The plain request first, for the status, the final URL and the
        # headers (and the page itself when there is no browser): a redirect,
        # an error or a throttled answer then costs no render.
        try:
            resp = net.fetch(url, method="GET" if obscura is None else "HEAD")
        except Exception:
            resp = None
        if obscura and (resp is None or resp["status"] == 405):
            # some servers refuse or drop a HEAD; ask once more the plain way
            try:
                resp = net.fetch(url)
            except Exception:
                resp = None
        if resp is None:
            skip(url, "fetch_failed")
            continue
        rec["status"], rec["final_url"] = resp["status"], resp["final_url"]
        lm = resp["headers"].get("Last-Modified") or resp["headers"].get("last-modified")
        if lm and not rec["lastmod"]:
            try:
                rec["lastmod"] = parsedate_to_datetime(lm).date().isoformat()
            except Exception:
                pass
        final = net.normalize_url(resp["final_url"]) if resp["final_url"] else None
        if final and final != url:
            if net.same_site(final, root_host) and net.crawlable(final):
                enqueue([final])
            skip(url, "redirect")
            continue
        if resp["status"] and resp["status"] >= 400:
            skip(url, "http_%d" % resp["status"])
            continue

        html, rendered = None, False
        if obscura:
            html = browser.render_html(url, obscura)
            rendered = html is not None
        if html is None:
            if not resp["body"]:
                try:
                    resp = net.fetch(url)
                except Exception:
                    skip(url, "fetch_failed")
                    continue
            html = resp["body"].decode("utf-8", "replace")
        if not html or not html.strip():
            skip(url, "fetch_failed")
            continue

        parsed = parse_page(html, url)
        page_structured = structured.page_structured(parsed, html)
        furniture = page_furniture(parsed, root_host)
        furniture["_order"] = len(parsed_pages)
        parsed_pages.append(parsed)
        furniture_pages.append(furniture)
        structured_pages.append(page_structured)
        with open(os.path.join(structured_dir, name_for(url) + ".json"), "w") as f:
            json.dump(page_structured, f, indent=2)

        rec.update({
            "title": parsed["title"], "meta_description": parsed["meta_description"], "h1": parsed["h1"],
            "h1_count": parsed["h1_count"], "canonical": parsed["canonical"], "lang": parsed["lang"],
            "noindex": parsed["noindex"], "hreflang": parsed["hreflang"],
            "jsonld_types": page_structured["jsonld_types"], "og_image": parsed["meta"].get("og:image"),
            "forms": [{"action": f["action"], "method": f["method"], "fields": [x["name"] or x["type"] for x in f["fields"]]} for f in parsed["forms"]],
            "embeds": page_structured["embeds"], "tracking": page_structured["tracking"], "rendered": rendered,
            "documents": sorted({l["href"] for l in parsed["links"] if net.is_document(l["href"]) and net.same_site(l["href"], root_host)}),
        })
        internal = []
        for l in parsed["internal_links"]:
            n = net.normalize_url(l["href"])
            if n and n != url and n not in internal:
                internal.append(n)
        rec["outbound_internal"] = internal
        enqueue([l["href"] for l in parsed["internal_links"]])
        for l in parsed["internal_links"]:
            n = net.normalize_url(l["href"])
            if n and n != url:
                inventory.add_inbound(records, url, n, sitewide=l["landmark"] in ("header", "nav", "footer", "aside"))

        # A page with little text of its own (contact, gallery, a short
        # landing page) is still the site's page: kept and marked thin, since
        # it is often where the phone number, the photos or the form are.
        md = trafilatura.extract(html, output_format="markdown", include_links=True,
                                 include_images=True, favor_recall=True) or ""
        if not md.strip():
            skip(url, "empty")
            continue
        digest = content_digest(md)
        if digest in kept_hashes:
            skip(url, "exact_duplicate")
            continue
        dup, sset = near_duplicate(md, kept_shingles)
        if dup:
            skip(url, "near_duplicate")
            continue
        kept_hashes.add(digest)
        kept_shingles.append(sset)
        kept.append({"url": url, "md": md, "rendered": rendered, "parsed": parsed})
        if shooter:  # only the pages kept: a duplicate costs no capture
            shot = shooter.shoot(url, os.path.join(out, "pages", name_for(url)))
            if shot.get("strips"):
                rec["screenshot"] = "pages/" + name_for(url)
            else:
                rec["screenshot_error"] = shot.get("error")
        if args.styles and obscura and len(style_readings) < args.style_pages:
            style_readings.append(browser.read_styles(url, obscura))

    # Furniture: landmark lines out of every page body; the repetition
    # heuristic only for pages that had no landmarks at all.
    common_lines = []
    if not args.keep_boilerplate and kept:
        landmark_lines = landmark_line_set([p["parsed"] for p in kept])
        with_landmarks = [p for p in kept if p["parsed"]["has_landmarks"]]
        without = [p for p in kept if not p["parsed"]["has_landmarks"]]
        for page in with_landmarks:
            page["md"] = strip_lines(page["md"], landmark_lines)
        if without:
            cleaned, fallback_lines = strip_common_lines([p["md"] for p in without])
            for page, md in zip(without, cleaned):
                page["md"] = md
            common_lines.extend(fallback_lines)
        common_lines = _ordered_landmark_lines(kept) + common_lines
    if common_lines:
        with open(os.path.join(out, "_common.md"), "w") as f:
            f.write("<!-- the site's header, nav, and footer lines (and, for pages without landmarks, "
                    "lines that repeated across the site); removed from every page and kept here once. "
                    "_furniture.json has the same as structure -->\n\n")
            f.write("\n".join(common_lines) + "\n")

    # Images: what the cleaned content references, one fetch per picture
    # across its size variants, one file per distinct byte content.
    image_hashes, key_to_local = {}, {}
    for page in kept:
        local_map = {}
        for raw_ref in dict.fromkeys(MD_IMAGE_RE.findall(page["md"])):
            img_url = urljoin(page["url"], raw_ref)
            key = image_key(img_url)
            if key in key_to_local:
                local_map[raw_ref] = key_to_local[key]
                continue
            if manifest["images"] >= args.max_images:
                break
            host = urlsplit(img_url).hostname
            if not host or not net.is_public_host(host):
                continue
            try:
                data, ctype = net.fetch_bytes(img_url, MAX_IMAGE_BYTES)
            except Exception:
                continue
            if not data:
                continue
            ihash = hashlib.sha256(data).hexdigest()[:16]
            if ihash not in image_hashes:
                fname = f"{ihash}.{ext_for(img_url, ctype)}"
                with open(os.path.join(img_dir, fname), "wb") as f:
                    f.write(data)
                image_hashes[ihash] = fname
                manifest["images"] += 1
            key_to_local[key] = f"images/{image_hashes[ihash]}"
            local_map[raw_ref] = key_to_local[key]
        page["md"] = rewrite_images(page["md"], local_map)

    # Pages. A page that was all furniture is not worth a file; a short one is
    # kept and marked thin. Each is written under its URL's own name, so a
    # re-crawl refreshes it in place and what cites it still resolves.
    for page in kept:
        rec = records[page["url"]]
        if not page["md"].strip():
            skip(page["url"], "boilerplate_only")
            continue
        fname = name_for(page["url"]) + ".md"
        thin = len(page["md"].strip()) < MIN_MARKDOWN_CHARS
        with open(os.path.join(out, fname), "w") as f:
            f.write(f"<!-- source: {page['url']} -->\n")
            if thin:
                f.write("<!-- thin: little text of its own; the inventory row has its title, forms and links -->\n")
            f.write(f"\n{page['md']}")
        rec["file"], rec["word_count"], rec["thin"] = fname, word_count(page["md"]), thin
        manifest["pages"].append({"url": page["url"], "file": fname, "chars": len(page["md"]),
                                  "rendered": page["rendered"], "thin": thin})
    # Pages an earlier run wrote and this one did not read (a smaller limit,
    # a page gone from the site): the files stay, listed so the next run and
    # the reader know them.
    read_now = {p["url"] for p in manifest["pages"]}
    manifest["earlier"] = [{"url": u, "file": f} for u, f in sorted(earlier.items())
                           if u not in read_now and os.path.isfile(os.path.join(out, f))]

    for url in queue:
        if url not in seen and records[url]["reason"] is None:
            records[url]["reason"] = "unread"

    # The records beside the pages.
    site_view = site_furniture(furniture_pages)
    for p in furniture_pages:
        p.pop("_order", None)
    with open(os.path.join(out, "_furniture.json"), "w") as f:
        json.dump({"site": site_view, "pages": furniture_pages}, f, indent=2)
    logo_src = (site_view.get("logo") or {}).get("src")
    media = build_media([(p["url"], p["images"]) for p in parsed_pages], logo_src)
    for m in media:
        local = key_to_local.get(image_key(m["url"]))
        if local:
            m["local"] = local
    with open(os.path.join(out, "_media.json"), "w") as f:
        json.dump(media, f, indent=2)
    business = structured.merge_business([(s["url"], s["jsonld"]) for s in structured_pages])
    with open(os.path.join(structured_dir, "business.json"), "w") as f:
        json.dump(business or {"note": "no LocalBusiness or Organization markup found on the crawled pages"}, f, indent=2)
    styles = None
    if args.styles:
        styles = merge_styles(style_readings)
        styles["renderer"] = obscura
        with open(os.path.join(out, "_styles.json"), "w") as f:
            json.dump(styles, f, indent=2)

    manifest["discovered"] = len(discovered)
    manifest["unread"] = len([u for u in queue if u not in seen])
    manifest["common_lines"] = len(common_lines)
    manifest["throttled"] = pace["throttled"]
    with open(os.path.join(out, "_manifest.json"), "w") as f:
        json.dump(manifest, f, indent=2)
    inventory.write(records, out, start, {"limit": args.max_pages, "limit_reached": manifest["limit_reached"],
                                         "renderer": obscura})

    summary = {"pages": len(manifest["pages"]), "discovered": manifest["discovered"], "unread": manifest["unread"],
               "limit": args.max_pages, "limit_reached": manifest["limit_reached"], "skipped": len(manifest["skipped"]),
               "thin": sum(1 for p in manifest["pages"] if p["thin"]), "earlier_kept": len(manifest["earlier"]),
               "throttled": pace["throttled"], "screenshots": sum(1 for r in records.values() if r.get("screenshot")),
               "images": manifest["images"], "rendered": sum(1 for p in manifest["pages"] if p["rendered"]),
               "common_lines": len(common_lines), "renderer": obscura,
               "inventory": len(records), "furniture_landmarks": site_view.get("has_landmarks", False),
               "nav_items": len(site_view.get("nav", [])), "media": len(media),
               "business_markup": bool(business), "documents": sum(len(r["documents"]) for r in records.values()),
               "styles_pages": (styles or {}).get("pages_read", 0), "out": out}
    print(json.dumps(summary))
    return 0


def _ordered_landmark_lines(kept):
    """The landmark lines as they first appeared, once each."""
    out, seen = [], set()
    for p in kept:
        for lm in ("header", "nav", "footer", "aside"):
            for t in p["parsed"]["landmark_lines"].get(lm, []):
                key = re.sub(r"\s+", " ", t.strip()).lower()
                if key and key not in seen:
                    seen.add(key)
                    out.append(t.strip())
    return out


def add_parser(sub):
    p = sub.add_parser("site", help="read a whole site into raw/web: pages, inventory, furniture, media, structured data")
    p.add_argument("start_url")
    p.add_argument("--out", default="raw/web")
    p.add_argument("--structured-out", default=None, help="where the per-page JSON goes (default: raw/structured beside raw/web; <out>/_structured for any other crawl)")
    p.add_argument("--max-pages", type=int, default=DEFAULT_MAX_PAGES,
                   help=f"pages to read (default {DEFAULT_MAX_PAGES}; the summary says how many were found)")
    p.add_argument("--max-images", type=int, default=200)
    p.add_argument("--delay", type=float, default=0.5)
    p.add_argument("--static", "--no-render", action="store_true", dest="static", help="plain fetches only, never the browser")
    p.add_argument("--screenshots", action="store_true", help="the whole page as PNG strips under pages/<slug>/ (browser only)")
    p.add_argument("--styles", action="store_true", help="read computed styles off the first pages into _styles.json (browser only)")
    p.add_argument("--style-pages", type=int, default=STYLE_PAGES)
    p.add_argument("--keep-boilerplate", action="store_true", help="keep the header and footer lines in every page")
    p.add_argument("--ignore-robots", action="store_true")
    p.add_argument("--no-sitemap", action="store_true")
    p.set_defaults(func=run)
