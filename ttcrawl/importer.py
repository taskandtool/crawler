"""`tt-crawl import`: bring one collection of a site across (its posts, its
products, its events) into the folder a survey or crawl already wrote; or,
for a WordPress site, every post and page its REST API lists.

Each page comes from the platform's own feed when it has one, which carries
what a page's HTML loses or muddles: WordPress's REST API (the post's date,
author and categories), an RSS or Atom feed (date, author, categories, often
only the latest items), Shopify's products.json (vendor, type, tags, prices,
every product photo). A page none of them carry is read as HTML, the usual
way. The item's own HTML becomes the page's blocks, so a feed item and a
crawled page read the same, and the pictures in it are fetched once each
like any other.
"""
import html as htmlmod
import json
import re
import sys
import time
import xml.etree.ElementTree as ET
from urllib.parse import urljoin, urlsplit

from . import net, paths
from .site import _start_run, folder_args, into_folder, load_robots, template_report

SOURCES = ("auto", "wp", "rss", "shopify", "html")
FEED_PATHS = ("/feed", "/feed/", "/rss.xml", "/atom.xml", "/feed.xml", "/index.xml", "/rss", "/blog/feed",
              "/blog/rss.xml", "/blog/atom.xml", "/news/feed")
SHOPIFY_PAGES = 50


def _text(el, *names):
    for n in names:
        found = el.find(n)
        if found is not None and (found.text or "").strip():
            return found.text.strip()
    return ""


def parse_feed(xml, base):
    """{url: item} from an RSS 2.0 or Atom document (pure)."""
    ns = {"atom": "http://www.w3.org/2005/Atom", "content": "http://purl.org/rss/1.0/modules/content/",
          "dc": "http://purl.org/dc/elements/1.1/"}
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return {}
    out = {}
    for it in root.iter("item"):                                   # RSS
        link = _text(it, "link")
        if not link:
            continue
        out[urljoin(base, link)] = {
            "title": _text(it, "title"), "date": _text(it, "pubDate"),
            "author": _text(it, "{%s}creator" % ns["dc"], "author"),
            "categories": [c.text.strip() for c in it.findall("category") if (c.text or "").strip()],
            "html": _text(it, "{%s}encoded" % ns["content"], "description")}
    for it in root.iter("{%s}entry" % ns["atom"]):                  # Atom
        link = next((l.get("href") for l in it.findall("{%s}link" % ns["atom"])
                     if l.get("rel") in (None, "alternate") and l.get("href")), None)
        if not link:
            continue
        author = it.find("{%s}author/{%s}name" % (ns["atom"], ns["atom"]))
        out[urljoin(base, link)] = {
            "title": _text(it, "{%s}title" % ns["atom"]),
            "date": _text(it, "{%s}published" % ns["atom"], "{%s}updated" % ns["atom"]),
            "author": author.text.strip() if author is not None and author.text else "",
            "categories": [c.get("term") for c in it.findall("{%s}category" % ns["atom"]) if c.get("term")],
            "html": _text(it, "{%s}content" % ns["atom"], "{%s}summary" % ns["atom"])}
    return out


def _iso(date):
    """A feed's date as YYYY-MM-DD when it can be read, else as it came."""
    from email.utils import parsedate_to_datetime
    if re.match(r"\d{4}-\d{2}-\d{2}", date or ""):
        return date[:10]
    try:
        return parsedate_to_datetime(date).date().isoformat()
    except (TypeError, ValueError):
        return date or None


def wp_api_base(site_url, fetch=net.fetch):
    """The wp-json base when the site is WordPress with the REST API open,
    else None."""
    parts = urlsplit(site_url)
    base = f"{parts.scheme}://{parts.netloc}"
    candidates = []
    path = parts.path.rstrip("/")
    if path:
        candidates.append(f"{base}{path}/wp-json/")
    candidates += [f"{base}/wp-json/", f"{base}/?rest_route=/"]
    # the page itself says where its API is (<link rel="https://api.w.org/">)
    try:
        page = fetch(site_url, cap=400_000)
        hinted = wp_api_link(page["body"].decode("utf-8", "replace")) if page["status"] == 200 else None
        if hinted and net.public_http_url(hinted):
            candidates.insert(0, hinted if hinted.endswith("/") else hinted + "/")
    except Exception:
        pass
    # The API root lists every route and can run to megabytes; one post is
    # a small, sure sign instead.
    for candidate in candidates:
        base_url = candidate.rstrip("/") if "rest_route" not in candidate else candidate
        try:
            r = fetch(_wp_url(base_url, "/wp/v2/posts", "per_page=1"), cap=2_000_000)
        except Exception:
            continue
        if r["status"] != 200:
            continue
        try:
            data = json.loads(r["body"].decode("utf-8", "replace"))
        except ValueError:
            continue
        if isinstance(data, list):
            return base_url
    return None


def wp_api_link(html):
    """The api.w.org link WordPress puts in every page's head (pure)."""
    m = re.search(r"<link[^>]+rel=[\"']https://api\.w\.org/[\"'][^>]*href=[\"']([^\"']+)[\"']", html, re.I)
    return m.group(1) if m else None


def _wp_url(base, path, query):
    if "rest_route" in base:
        return f"{base}{path}&{query}"
    return f"{base}{path}?{query}"


def wp_fetch_all(base, kind, fetch=net.fetch, per_page=100, max_pages=50, extra=""):
    """Every item of one REST collection, page by page."""
    items, page = [], 1
    while page <= max_pages:
        r = fetch(_wp_url(base, f"/wp/v2/{kind}", f"per_page={per_page}&page={page}&_embed=0{extra}"), cap=20_000_000)
        if r["status"] != 200:
            break
        try:
            batch = json.loads(r["body"].decode("utf-8", "replace"))
        except ValueError:
            break
        if not isinstance(batch, list) or not batch:
            break
        items.extend(batch)
        total_pages = int((r["headers"].get("X-WP-TotalPages") or r["headers"].get("x-wp-totalpages") or "1"))
        if page >= total_pages:
            break
        page += 1
    return items


def from_wordpress(start, fetch=net.fetch, since=None, want=0):
    """Posts and pages through the REST API; newest first, so --limit needs
    only its first pages, and --since is asked of the API itself."""
    base = wp_api_base(start, fetch=fetch)
    if not base:
        return {}
    pages = min(50, want // 100 + 1) if want else 50
    extra = "&after=%sT00:00:00" % since if since else ""
    authors = {u.get("id"): u.get("name", "") for u in wp_fetch_all(base, "users", fetch=fetch, per_page=100, max_pages=5)}
    cats = {c.get("id"): c.get("name", "") for c in wp_fetch_all(base, "categories", fetch=fetch, per_page=100, max_pages=5)}
    out = {}
    for kind in ("posts", "pages"):
        for it in wp_fetch_all(base, kind, fetch=fetch, max_pages=pages, extra=extra):
            link = net.normalize_url(it.get("link") or "")
            if not link:
                continue
            out[link] = {"title": htmlmod.unescape(re.sub(r"<[^>]+>", "", (it.get("title") or {}).get("rendered", ""))),
                         "date": (it.get("date") or "")[:10] or None, "author": authors.get(it.get("author"), ""),
                         "categories": [cats.get(c, str(c)) for c in it.get("categories") or []],
                         "html": (it.get("content") or {}).get("rendered", ""), "source": "wp-rest"}
    return out


def from_feeds(start, fetch=net.fetch):
    """Items from the site's feeds: the ones its home page names, then the usual places."""
    feeds = []
    try:
        home = fetch(start, cap=1_000_000)["body"].decode("utf-8", "replace")
        feeds += [urljoin(start, h) for h in re.findall(
            r"<link[^>]+type=[\"']application/(?:rss|atom)\+xml[\"'][^>]*href=[\"']([^\"']+)", home, re.I)]
    except Exception:
        pass
    feeds += [urljoin(start, p) for p in FEED_PATHS]
    out = {}
    for f in dict.fromkeys(feeds):
        if not net.same_site(f, urlsplit(start).hostname or ""):
            continue
        try:
            r = fetch(f, cap=20_000_000)
        except Exception:
            continue
        if r["status"] != 200 or b"<" not in r["body"][:200]:
            continue
        for url, item in parse_feed(r["body"].decode("utf-8", "replace"), f).items():
            n = net.normalize_url(url)
            if n and n not in out:
                out[n] = dict(item, date=_iso(item["date"]), source="rss")
    return out


def from_shopify(start, fetch=net.fetch):
    out, page = {}, 1
    while page <= SHOPIFY_PAGES:
        try:
            r = fetch(urljoin(start, "/products.json?limit=250&page=%d" % page), cap=30_000_000)
            products = json.loads(r["body"].decode("utf-8", "replace")).get("products") if r["status"] == 200 else None
        except Exception:
            products = None
        if not products:
            break
        for p in products:
            url = net.normalize_url(urljoin(start, "/products/" + p.get("handle", "")))
            prices = sorted({float(v["price"]) for v in p.get("variants") or [] if v.get("price")})
            photos = "".join('<img src="%s" alt="%s">' % (htmlmod.escape(i["src"]), htmlmod.escape(i.get("alt") or p.get("title", "")))
                             for i in p.get("images") or [] if i.get("src"))
            front = {"vendor": p.get("vendor") or None, "product_type": p.get("product_type") or None,
                     "tags": p.get("tags") if isinstance(p.get("tags"), list) else [t.strip() for t in (p.get("tags") or "").split(",") if t.strip()]}
            if prices:
                front["price"] = prices[0] if len(prices) == 1 else "%s-%s" % (prices[0], prices[-1])
            out[url] = {"title": p.get("title", ""), "date": (p.get("published_at") or "")[:10] or None, "author": "",
                        "categories": [], "html": (p.get("body_html") or "") + photos, "source": "shopify", "front": front}
        page += 1
    return out


def item_html(item):
    """A page for a feed item: its title as the h1, its own HTML beneath."""
    return "<html><head><title>%s</title></head><body><main><h1>%s</h1>%s</main></body></html>" % (
        htmlmod.escape(item["title"]), htmlmod.escape(item["title"]), item.get("html") or "")


def run(args):
    found = into_folder(args, "import")
    if not found:
        return 2
    profile, settings = found
    if not args.template and args.source not in ("auto", "wp"):
        sys.stderr.write("--source %s imports one collection: name it with --template\n" % args.source)
        return 2

    def body(crawl):
        crawl.load_state()
        crawl.robots, _ = load_robots(crawl.start)
        sources = [("wp", lambda st: from_wordpress(st, since=args.since, want=args.limit))]
        if args.template:
            sources += [("rss", from_feeds), ("shopify", from_shopify)]
        items, used = {}, []
        for src, get in sources:
            if args.source in ("auto", src):
                got = get(crawl.start)
                if got:
                    used.append("%s (%d)" % (src, len(got)))
                    for u, it in got.items():
                        items.setdefault(u, it)
        wordpress = not args.template and any(it["source"] == "wp-rest" for it in items.values())
        if args.template:
            urls = [u for u, r in crawl.records.items() if r.get("template") == args.template]
        elif wordpress:
            # no template: every post and page the WordPress API lists
            urls = [u for u, it in items.items() if it["source"] == "wp-rest" and net.same_site(u, crawl.root_host)]
        else:
            urls = []
        if not urls:
            rows = [t for t in template_report(crawl.records) if t["collection"]]
            sys.stderr.write("%s; the collections are: %s\n" % (
                "no pages of template %r" % args.template if args.template
                else "no WordPress REST API answered, so import needs --template",
                ", ".join("%s (%d)" % (t["template"], t["count"]) for t in rows) or "none"))
            return 2
        # A page's date: its feed item's, else the sitemap's lastmod. With
        # --since, a page dated earlier, or with no date to judge by, stays out.
        chosen = []
        for u in urls:
            it = items.get(u) if args.source != "html" else None
            date = (it or {}).get("date") or crawl.record(u).get("lastmod")
            if args.since and (not date or date[:10] < args.since):
                continue
            chosen.append((u, it, date or ""))
        chosen.sort(key=lambda x: x[2], reverse=True)
        if args.limit:
            chosen = chosen[:args.limit]
        counts = {}
        for i, (u, it, _date) in enumerate(chosen):
            crawl.seen.add(u)
            rec = crawl.record(u)
            if not rec["template"]:
                crawl.templates.add(u)
                rec["template"] = crawl.templates.of(u)
            rec["reason"] = None
            crawl.skipped = [s for s in crawl.skipped if s["url"] != u]
            crawl.forget(u)
            if it:
                front = {k: v for k, v in (("date", it.get("date")), ("author", it.get("author") or None),
                                           ("categories", it.get("categories") or None)) if v}
                front.update({k: v for k, v in (it.get("front") or {}).items() if v not in (None, [], "")})
                rec["status"], rec["final_url"] = rec["status"] or 200, rec["final_url"] or u
                crawl.absorb(u, item_html(it), fetcher=it["source"], front=front)
                counts[it["source"]] = counts.get(it["source"], 0) + 1
            else:
                if not crawl.robots.can_fetch(net.USER_AGENT, u):
                    crawl.skip(u, "robots")
                    continue
                if i:
                    time.sleep(crawl.pace["delay"])
                crawl.read(u)
                counts["html"] = counts.get("html", 0) + 1
        what = args.template or "wordpress posts and pages"
        crawl.import_stats = {"template": args.template, "items": len(chosen), "by_source": counts,
                              "sources_found": used, "since": args.since, "limit": args.limit}
        sys.stderr.write("import %s: %d pages (%s)\n" % (what, len(chosen),
                                                          ", ".join("%s %d" % kv for kv in counts.items()) or "none"))
        code = crawl.write()
        if wordpress:         # the whole WordPress site, not one collection of it
            paths.register_site(crawl.out, {"wp_imported": True})
        return code
    return _start_run(args, profile, body, settings)


def add_parser(sub):
    p = sub.add_parser("import", help="a collection (posts, products) into a crawled site's folder from its own feed; "
                                      "a WordPress site's posts and pages with no --template")
    p.add_argument("--template", default=None,
                   help="the template to import, as _index/templates.md names it (post, /blog/*); without one, every "
                        "post and page of a WordPress site")
    p.add_argument("--source", choices=SOURCES, default="auto",
                   help="where items come from (default auto: every feed the site answers, a page none carries read as HTML)")
    p.add_argument("--since", default=None, help="only items dated on or after YYYY-MM-DD")
    p.add_argument("--limit", type=int, default=0, help="at most N items, newest first")
    folder_args(p)
    p.set_defaults(func=run)
