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
from .say import command, count, done, fail
from .text import slugify

MAX_DOC_BYTES = 25 * 1024 * 1024
DOC_TYPES = ("application/pdf", "application/msword", "application/vnd.openxmlformats", "application/vnd.ms-",
             "application/octet-stream")


def linked_documents(inventory_path):
    """Document URLs the crawl recorded, with the page that linked each.
    Raises OSError or ValueError when the inventory cannot be read."""
    found = {}
    with open(inventory_path) as f:
        data = json.load(f)
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
    folders = "Crawl folders: %s" % (", ".join(paths.site_folders()) or "none")
    if not args.from_dir:
        return fail(args, 2, "needs --from, the crawl folder whose pages link the documents",
                    "tt-crawl docs --from raw/site/<host>", folders)
    inventory = paths.index(args.from_dir, "inventory.json")
    if not os.path.isfile(inventory):
        return fail(args, 2, "no crawl at %s (no %s)" % (args.from_dir, inventory),
                    "tt-crawl docs --from raw/site/<host>, or crawl first: tt-crawl site URL", folders)
    out = os.path.join(args.from_dir, paths.DOCS)
    try:
        docs = linked_documents(inventory)
    except (OSError, ValueError, AttributeError, KeyError, TypeError) as e:
        return fail(args, 2, "cannot read %s (%s)" % (inventory, str(e) or type(e).__name__),
                    "tt-crawl site URL --out %s, to crawl it again" % args.from_dir)
    if not docs:
        paths.register_site(args.from_dir, {"docs_fetched": True, "docs": 0})
        done(args, {"documents": 0, "note": "no documents linked from %s" % args.from_dir},
             "no documents linked from the pages in %s" % args.from_dir,
             next="read %s/_index/facts.json, then the pages" % args.from_dir)
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
    skipped_path = os.path.join(out, "_skipped.json")
    with open(skipped_path, "w") as f:
        json.dump(skipped, f, indent=2)
    paths.register_site(args.from_dir, {"docs_fetched": True, "docs": len(index)})
    unconverted = sum(1 for d in index if not d["converted"])
    by_reason = {}
    for x in skipped:
        reason = x["reason"].split(":")[0]
        by_reason[reason] = by_reason.get(reason, 0) + 1
    lines = ["%s in %s, each as markdown beside its original in _files/; %s lists them" % (
        count(len(index), "document"), out, index_path)]
    if unconverted:
        lines.append("%s could not be read as text; the original is kept" % count(unconverted, "document"))
    if skipped:
        lines.append("left alone: %s (%s); each with its reason in %s" % (
            count(len(skipped), "link"), ", ".join("%s %d" % kv for kv in sorted(by_reason.items())), skipped_path))
    done(args, {"documents": written, "skipped": len(skipped), "unconverted": unconverted, "out": out,
                "skipped_reasons": skipped[:20], "skipped_file": skipped_path},
         "%s fetched from the pages in %s" % (count(written, "new document"), args.from_dir), lines,
         "read the documents in %s/ for prices, services and facts" % out)
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
    p = command(sub, "docs", "the documents linked from the crawled pages, converted to markdown",
                "Prints how many documents were fetched into which folder, which could not be read as text, and "
                "the links left alone (all of them, with reasons, in docs/_skipped.json), then Next:. "
                "A refusal goes to stderr with a Try: line.")
    p.add_argument("--from", dest="from_dir", default=None,
                   help="the crawl to read the links from, its docs/ the destination (default: the one raw/site/<host>)")
    p.set_defaults(func=run)
