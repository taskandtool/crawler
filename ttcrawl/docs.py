"""`tt-crawl docs`: the documents linked from the crawled pages (brochures,
price lists, menus, application forms) into the site folder's docs/ as
markdown, the originals kept in docs/_files/. Small businesses put their real prices in a
PDF more often than on a page."""
import datetime as dt
import hashlib
import json
import os
import sys
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit

from . import net, paths
from .text import slugify

MAX_DOC_BYTES = 25 * 1024 * 1024
DOC_TYPES = ("application/pdf", "application/msword", "application/vnd.openxmlformats", "application/vnd.ms-",
             "application/octet-stream")


def linked_documents(inventory_path):
    """Document URLs the crawl recorded, with the page that linked each."""
    found = {}
    try:
        with open(inventory_path) as f:
            data = json.load(f)
    except (OSError, ValueError):
        return found
    for rec in data.get("records", []):
        for d in rec.get("documents", []):
            found.setdefault(d, rec["url"])
    return found


def convert(path):
    """The document as markdown through markitdown, or None when it is not
    installed or the conversion fails."""
    try:
        from markitdown import MarkItDown
    except ImportError:
        return None
    try:
        return MarkItDown().convert(path).text_content
    except Exception as e:
        sys.stderr.write("could not convert %s: %s\n" % (os.path.basename(path), str(e).splitlines()[0][:160]))
        return None


def run(args):
    args.from_dir = args.from_dir or paths.the_site()
    if not args.from_dir:
        sys.stderr.write("docs needs --from: the crawl folder (raw/site/<host>) whose pages link the documents\n")
        return 2
    out = os.path.join(args.from_dir, paths.DOCS)
    docs = linked_documents(paths.index(args.from_dir, "inventory.json"))
    if not docs:
        paths.register_site(args.from_dir, {"docs_fetched": True, "docs": 0})
        print(json.dumps({"documents": 0, "note": "no documents linked from %s" % args.from_dir}))
        return 0
    root_host = None
    try:
        with open(paths.index(args.from_dir, "manifest.json")) as f:
            root_host = urlsplit(json.load(f).get("start", "")).hostname
    except (OSError, ValueError):
        pass
    os.makedirs(os.path.join(out, "_files"), exist_ok=True)
    index_path = os.path.join(out, "_documents.json")
    index = []
    if os.path.isfile(index_path):
        with open(index_path) as f:
            index = json.load(f)
    known = {d["sha256"]: d for d in index}
    written, skipped = 0, []
    for url, linked_from in docs.items():
        if not net.public_http_url(url):
            skipped.append({"url": url, "reason": "not_public"})
            continue
        if root_host and not net.same_site(url, root_host):
            skipped.append({"url": url, "reason": "external"})
            continue
        try:
            data, ctype = net.fetch_bytes(url, MAX_DOC_BYTES, content_types=DOC_TYPES)
        except Exception:
            skipped.append({"url": url, "reason": "fetch_failed"})
            continue
        if not data:
            skipped.append({"url": url, "reason": "too_large_or_not_a_document"})
            continue
        sha = hashlib.sha256(data).hexdigest()
        if sha in known:
            skipped.append({"url": url, "reason": "duplicate_of:" + known[sha]["file"]})
            continue
        name = os.path.basename(urlsplit(url).path) or "document"
        stem, ext = os.path.splitext(name)
        date = _date(url)
        original = os.path.join(out, "_files", f"{sha[:12]}-{name}")
        with open(original, "wb") as f:
            f.write(data)
        md = convert(original)
        fname = f"{date}-{slugify('https://x/' + stem)}.md"
        path = os.path.join(out, fname)
        n = 1
        while os.path.exists(path):
            path = os.path.join(out, f"{date}-{slugify('https://x/' + stem)}-{n}.md")
            n += 1
        with open(path, "w") as f:
            f.write(f"<!-- source: {url} -->\n")
            if linked_from:
                f.write(f"<!-- linked from: {linked_from} -->\n")
            f.write(f"<!-- original: {os.path.relpath(original, out)} ({len(data)} bytes) -->\n\n")
            f.write(md if md else "(markitdown is not installed or could not read this file; the original is kept beside it)\n")
        entry = {"url": url, "linked_from": linked_from, "file": os.path.basename(path), "original": os.path.relpath(original, out),
                 "sha256": sha, "bytes": len(data), "converted": md is not None, "date": date}
        index.append(entry)
        known[sha] = entry
        written += 1
    with open(index_path, "w") as f:
        json.dump(index, f, indent=2)
    paths.register_site(args.from_dir, {"docs_fetched": True, "docs": len(index)})
    print(json.dumps({"documents": written, "skipped": len(skipped), "unconverted": sum(1 for d in index if not d["converted"]),
                      "out": out, "skipped_reasons": skipped[:20]}))
    return 0


def _date(url):
    try:
        r = net.fetch(url, method="HEAD")
        lm = r["headers"].get("Last-Modified") or r["headers"].get("last-modified")
        if lm:
            return parsedate_to_datetime(lm).date().isoformat()
    except Exception:
        pass
    return dt.date.today().isoformat()


def add_parser(sub):
    p = sub.add_parser("docs", help="the documents linked from the crawled pages, converted to markdown")
    p.add_argument("--from", dest="from_dir", default=None,
                   help="the crawl to read the links from, its docs/ the destination (default: the one raw/site/<host>)")
    p.set_defaults(func=run)
