"""`tt-crawl wp`: a WordPress site's pages and posts through its public
REST API: full content without rendering, plus authors, dates, and
categories a crawl loses."""
import json
import os
import re
import sys
from urllib.parse import urlsplit

from . import net, paths
from .text import html_to_text, slugify


def api_base(site_url, fetch=net.fetch):
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
        hinted = detect_in_html(page["body"].decode("utf-8", "replace")) if page["status"] == 200 else None
        if hinted and net.public_http_url(hinted):
            candidates.insert(0, hinted if hinted.endswith("/") else hinted + "/")
    except Exception:
        pass
    # The API root lists every route and can run to megabytes; one post is
    # a small, sure sign instead.
    for candidate in candidates:
        base_url = candidate.rstrip("/") if "rest_route" not in candidate else candidate
        try:
            r = fetch(_url(base_url, "/wp/v2/posts", "per_page=1"), cap=2_000_000)
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


def detect_in_html(html):
    """The api.w.org link WordPress puts in every page's head (pure)."""
    m = re.search(r"<link[^>]+rel=[\"']https://api\.w\.org/[\"'][^>]*href=[\"']([^\"']+)[\"']", html, re.I)
    return m.group(1) if m else None


def _url(base, path, query):
    if "rest_route" in base:
        return f"{base}{path}&{query}"
    return f"{base}{path}?{query}"


def fetch_all(base, kind, fetch=net.fetch, per_page=100, max_pages=50, extra=""):
    items, page = [], 1
    while page <= max_pages:
        r = fetch(_url(base, f"/wp/v2/{kind}", f"per_page={per_page}&page={page}&_embed=0{extra}"), cap=20_000_000)
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


def to_markdown(html):
    try:
        import trafilatura
        md = trafilatura.extract(html, output_format="markdown", include_links=True, include_images=True, favor_recall=True)
        if md:
            return md
    except ImportError:
        pass
    return html_to_text(html)


def frontmatter(item, authors, categories, kind):
    title = html_to_text(item.get("title", {}).get("rendered", "")).strip()
    cats = [categories.get(c, str(c)) for c in item.get("categories", [])]
    author = authors.get(item.get("author"), "")
    lines = ["---", f"title: {json.dumps(title)}", f"type: wp_{kind[:-1] if kind.endswith('s') else kind}",
             f"url: {item.get('link', '')}", f"date: {item.get('date', '')[:10]}", f"modified: {item.get('modified', '')[:10]}",
             f"author: {json.dumps(author)}", f"status: {item.get('status', '')}", f"slug: {item.get('slug', '')}"]
    if cats:
        lines.append("categories: [" + ", ".join(json.dumps(c) for c in cats) + "]")
    if item.get("parent"):
        lines.append(f"parent: {item['parent']}")
    lines.append("---")
    return "\n".join(lines) + "\n\n"


def run(args):
    args.out = args.out or os.path.join(paths.site_dir(args.site_url), "wp")
    if not net.public_http_url(args.site_url):
        sys.stderr.write("refusing: not a public http url\n")
        return 2
    base = api_base(args.site_url)
    if not base:
        print(json.dumps({"detected": False, "site": args.site_url}))
        return 0
    os.makedirs(args.out, exist_ok=True)
    authors = {}
    for u in fetch_all(base, "users", per_page=100, max_pages=5):
        authors[u.get("id")] = u.get("name", "")
    categories = {}
    for c in fetch_all(base, "categories", per_page=100, max_pages=5):
        categories[c.get("id")] = c.get("name", "")
    index = {"base": base, "site": args.site_url, "pages": [], "posts": []}
    for kind in ("pages", "posts"):
        os.makedirs(os.path.join(args.out, kind), exist_ok=True)
        for item in fetch_all(base, kind):
            slug = slugify("https://x/" + (item.get("slug") or str(item.get("id"))))
            body = to_markdown(item.get("content", {}).get("rendered", "")) or ""
            path = os.path.join(args.out, kind, f"{slug}.md")
            with open(path, "w") as f:
                f.write(frontmatter(item, authors, categories, kind))
                f.write(f"<!-- source: {item.get('link', '')} (WordPress REST API) -->\n\n")
                f.write(body.rstrip() + "\n")
            index[kind].append({"id": item.get("id"), "slug": item.get("slug"), "url": item.get("link"),
                                "title": html_to_text(item.get("title", {}).get("rendered", "")).strip(),
                                "date": item.get("date", "")[:10], "modified": item.get("modified", "")[:10],
                                "file": os.path.join(kind, f"{slug}.md")})
    with open(os.path.join(args.out, "index.json"), "w") as f:
        json.dump(index, f, indent=2)
    # into a site's folder (raw/site/<host>/wp): the site's registry says it is done
    paths.register_site(os.path.dirname(args.out.rstrip("/")), {"wp_imported": True})
    print(json.dumps({"detected": True, "base": base, "pages": len(index["pages"]), "posts": len(index["posts"]),
                      "authors": len(authors), "categories": len(categories), "out": args.out}))
    return 0


def add_parser(sub):
    p = sub.add_parser("wp", help="a WordPress site's pages and posts through its public REST API")
    p.add_argument("site_url")
    p.add_argument("--out", default=None, help="default raw/site/<host>/wp, inside the site's folder")
    p.set_defaults(func=run)
