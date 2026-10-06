"""`tt-crawl audit`: the weekly health check of a live site. Crawls the site
from its home page (same host, following links, a page cap) and reports
what a site owner should fix:

    broken internal links and images (4xx/5xx or unreachable)
    broken external links (HEAD, capped, one try each; a link to a private
    or internal address is listed as skipped, never requested)
    redirect chains (more than one hop) and internal links that redirect
    pages without a title, without a meta description, with no h1 or more than one
    duplicate titles across pages
    images without alt text, images over 300 KB
    static accessibility: lang and viewport, heading order, empty or generic links and
    buttons, unlabelled form fields, duplicate ids; title and description lengths
    canonical tags pointing at another URL, noindex pages
    sitemap drift: pages in the sitemap that fail, crawled pages not in the sitemap
    JSON-LD that does not parse
    oversized pages (over 1 MB of HTML)

    tt-crawl audit https://theirdomain.com [--max-pages 200] [--no-external] [--no-register]
                   [--inventory raw/site/<host>/_index/inventory.json]   # also the old URLs, as `check` does

Writes raw/audit/<host>/<date>.md (with an "## Issues" section only when there are
issues) and a JSON file beside it, prints how many pages and issues by kind, and exits 1
when anything needs fixing, which is what makes a scheduled job alert the
owner. Static fetches: it checks the HTML the server sends.
"""
import json
import os
import time
from collections import Counter
from urllib.parse import urljoin, urlsplit

from . import net, paths
from .a11y import findings as a11y_findings
from .html import parse_page
from .say import command, count, done, fail
from .site import parse_sitemap
from .structured import jsonld

MAX_HTML = 1_000_000
MAX_IMAGE = 300_000
EXTERNAL_LIMIT = 100


def norm(url):
    """Strip the fragment and a trailing slash (except the root) for comparisons."""
    s = urlsplit(url)
    path = s.path or "/"
    if len(path) > 1:
        path = path.rstrip("/")
    return f"{s.scheme}://{s.netloc}{path}" + (f"?{s.query}" if s.query else "")


def fetch_status(url, fetch=net.fetch, method="GET"):
    """(status, final_url, hops, body, headers) or (None, url, [], b"", {}) on a network error."""
    try:
        r = fetch(url, method=method)
        return r["status"], r.get("final_url", url), r.get("chain", []), r.get("body", b""), r.get("headers", {})
    except Exception:
        return None, url, [], b"", {}


def page_findings(parsed, url, body_len, titles_seen, html="", descriptions_seen=None):
    """The per-page issues (pure)."""
    issues = []
    if not parsed["title"]:
        issues.append("no title")
    if not parsed["meta_description"]:
        issues.append("no meta description")
    h1s = [t for tag, t in parsed["headings"] if tag == "h1"]
    if len(h1s) == 0:
        issues.append("no h1")
    elif len(h1s) > 1:
        issues.append(f"{len(h1s)} h1 headings")
    if parsed["canonical"] and norm(parsed["canonical"]) != norm(url):
        issues.append(f"canonical points at {parsed['canonical']}")
    if parsed["noindex"]:
        issues.append("noindex")
    no_alt = [i for i in parsed["images"] if i.get("src") and not i.get("alt")]
    if no_alt:
        issues.append(f"{len(no_alt)} image(s) without alt text")
    if body_len > MAX_HTML:
        issues.append(f"page is {body_len // 1024} KB of HTML")
    if parsed.get("jsonld_raw") and len(jsonld(parsed)) < len([r for r in parsed["jsonld_raw"] if r.strip()]):
        issues.append("JSON-LD that does not parse")
    if parsed["title"] and titles_seen.get(parsed["title"], 0) > 1:
        issues.append("duplicate title")
    if descriptions_seen and parsed["meta_description"] and descriptions_seen.get(parsed["meta_description"], 0) > 1:
        issues.append("duplicate meta description")
    if html:
        issues.extend(a11y_findings(html, parsed["title"], parsed["meta_description"]))
    return issues


def sitemap_urls(start_url, fetch=net.fetch):
    """The same-host URLs the site's sitemap lists, and whether a sitemap answered."""
    s = urlsplit(start_url)
    status, _, _, body, _ = fetch_status(f"{s.scheme}://{s.netloc}/sitemap.xml", fetch)
    if status != 200 or not body:
        return [], status
    # a sitemap whose locations are relative (a site with no domain set yet) still counts
    urls = [norm(urljoin(start_url, loc)) for loc, _ in parse_sitemap(body.decode("utf-8", errors="replace"))]
    return [u for u in urls if urlsplit(u).netloc == s.netloc], status


def crawl(start_url, max_pages, fetch=net.fetch, seeds=()):
    """Crawl same-host pages from start_url (and the sitemap's URLs, so an
    unlinked page is still checked). Returns (pages, link_targets, unread)
    where pages maps url → {parsed, status, hops, body_len} and link_targets
    maps every linked URL → the set of pages linking to it."""
    root_host = urlsplit(start_url).netloc
    queue, seen, pages, targets = [norm(start_url)] + [u for u in seeds if u != norm(start_url)], set(), {}, {}
    while queue and len(pages) < max_pages:
        url = queue.pop(0)
        if url in seen:
            continue
        seen.add(url)
        status, final, hops, body, headers = fetch_status(url, fetch)
        ctype = (headers.get("content-type") or "")
        entry = {"status": status, "final_url": final, "hops": hops, "body_len": len(body), "parsed": None, "html": ""}
        if status == 200 and ("html" in ctype or not ctype):
            try:
                parsed = parse_page(body.decode("utf-8", errors="replace"), final or url)
            except Exception:
                parsed = None
            entry["parsed"] = parsed
            entry["html"] = body.decode("utf-8", errors="replace") if parsed else ""
            if parsed:
                for l in parsed["links"]:
                    href = l["href"]
                    if href.lower().startswith(("mailto:", "tel:", "javascript:", "#")):
                        continue
                    t = norm(href)
                    targets.setdefault(t, set()).add(url)
                    if urlsplit(t).netloc == root_host and t not in seen and t not in queue and net.crawlable(t):
                        queue.append(t)
                for img in parsed["images"]:
                    if img.get("src"):
                        targets.setdefault(norm(img["src"]), set()).add(url)
        pages[url] = entry
    return pages, targets, [u for u in queue if u not in seen]


def audit(start_url, max_pages=200, fetch=net.fetch, external_limit=EXTERNAL_LIMIT, check_external=True, inventory=None,
          public=net.public_http_url):
    root_host = urlsplit(start_url).netloc
    sitemap_list, sitemap_status = sitemap_urls(start_url, fetch)
    pages, targets, unread = crawl(start_url, max_pages, fetch, seeds=sitemap_list)
    issues = []  # (kind, subject, detail)

    # pages
    titles = Counter(p["parsed"]["title"] for p in pages.values() if p["parsed"] and p["parsed"]["title"])
    descriptions = Counter(p["parsed"]["meta_description"] for p in pages.values() if p["parsed"] and p["parsed"]["meta_description"])
    for url, p in pages.items():
        if p["status"] is None:
            issues.append(("unreachable", url, "no answer"))
            continue
        if p["status"] >= 400:
            issues.append(("broken page", url, f"HTTP {p['status']} (linked from {', '.join(sorted(targets.get(url, [])))[:200] or 'the crawl'})"))
            continue
        if len(p["hops"]) > 1:
            issues.append(("redirect chain", url, " -> ".join(u for _, u in p["hops"]) + f" -> {p['final_url']}"))
        elif p["hops"]:
            issues.append(("internal link redirects", url, f"-> {p['final_url']}; link to the final URL from {', '.join(sorted(targets.get(url, [])))[:200] or 'the sitemap'}"))
        if p["parsed"]:
            for f in page_findings(p["parsed"], p["final_url"] or url, p["body_len"], titles, p.get("html", ""), descriptions):
                issues.append(("page", url, f))

    # link targets not crawled as pages: images and files on this host, and external links
    checked_external, skipped_external = 0, []
    for t, sources in sorted(targets.items()):
        if t in pages:
            continue
        if urlsplit(t).scheme not in ("http", "https"):
            continue                   # sms:, whatsapp:, ftp: and the like: nothing to request
        internal = urlsplit(t).netloc == root_host
        if not internal:
            if not check_external or checked_external >= external_limit:
                continue
            if not public(t):
                skipped_external.append(t)
                continue
            checked_external += 1
            status, final, hops, _, _ = fetch_status(t, fetch, method="HEAD")
            if status in (405, 403):  # some hosts refuse HEAD; try a GET once
                status, final, hops, _, _ = fetch_status(t, fetch)
            if status is None or status >= 400:
                issues.append(("broken external link", t, f"{'HTTP ' + str(status) if status else 'no answer'}, linked from {', '.join(sorted(sources))[:200]}"))
            continue
        status, final, hops, _, headers = fetch_status(t, fetch, method="HEAD")
        if status in (405, 403):
            status, final, hops, _, headers = fetch_status(t, fetch)
        if status is None or status >= 400:
            issues.append(("broken internal link", t, f"{'HTTP ' + str(status) if status else 'no answer'}, linked from {', '.join(sorted(sources))[:200]}"))
        elif hops:
            issues.append(("internal link redirects", t, f"-> {final}; link to the final URL from {', '.join(sorted(sources))[:200]}"))
        else:
            size = int(headers.get("content-length") or 0) if str(headers.get("content-length") or "").isdigit() else 0
            if (headers.get("content-type") or "").startswith("image/") and size > MAX_IMAGE:
                issues.append(("heavy image", t, f"{size // 1024} KB; resize or compress it (under {MAX_IMAGE // 1024} KB)"))

    # sitemap drift
    s = urlsplit(start_url)
    if sitemap_status == 200:
        crawled_ok = {norm(p["final_url"] or u) for u, p in pages.items() if p["status"] == 200 and p["parsed"]}
        for u in sitemap_list:
            if u in pages and (pages[u]["status"] or 0) >= 400:
                issues.append(("sitemap", u, f"listed in the sitemap but answers HTTP {pages[u]['status']}"))
            elif u in pages and pages[u]["status"] is None:
                issues.append(("sitemap", u, "listed in the sitemap but does not answer"))
        for u in [u for u in sorted(crawled_ok) if u not in set(sitemap_list)][:50]:
            issues.append(("sitemap", u, "crawled but not in the sitemap"))
    else:
        issues.append(("sitemap", f"{s.scheme}://{s.netloc}/sitemap.xml", f"no sitemap ({'HTTP ' + str(sitemap_status) if sitemap_status else 'no answer'})"))

    # the old URLs, when an inventory is given
    old_rows = []
    if inventory:
        from .check import check_url, verdict
        for rec in inventory.get("records", []):
            if not rec.get("url"):
                continue
            o = urlsplit(rec["url"])
            new_url = f"{s.scheme}://{s.netloc}{o.path}" + (f"?{o.query}" if o.query else "")
            r = check_url(new_url, fetch=fetch)
            v = verdict(r["status"], r["chain"], r.get("noindex", False))
            old_rows.append({"old_url": rec["url"], "verdict": v, "status": r["status"]})
            if v not in ("ok",):
                issues.append(("old url", rec["url"], f"{v} ({r['status']})"))

    return {
        "site": start_url, "pages": len(pages), "unread": len(unread), "limit_reached": bool(unread),
        "external_checked": checked_external, "external_skipped": skipped_external, "sitemap_urls": len(sitemap_list),
        "issues": [{"kind": k, "subject": sub, "detail": d} for k, sub, d in issues],
        "old_urls": old_rows,
        "checked_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


def markdown(report):
    r = report
    lines = [f"# Site audit: {r['site']}", "", f"Checked {r['checked_at']}: {r['pages']} page(s) crawled"
             + (f" (limit reached, {r['unread']} unread)" if r["limit_reached"] else "")
             + f", {r['external_checked']} external link(s) checked, {r['sitemap_urls']} sitemap URL(s).", ""]
    if r.get("external_skipped"):
        lines += ["Not requested (a private or internal address, or a host that does not resolve): "
                  + ", ".join(r["external_skipped"][:20]), ""]
    if not r["issues"]:
        lines += ["No issues found.", ""]
    else:
        by = Counter(i["kind"] for i in r["issues"])
        lines += [f"## Issues ({len(r['issues'])})", "", "| kind | where | detail |", "|---|---|---|"]
        for i in r["issues"]:
            lines.append(f"| {i['kind']} | {i['subject']} | {i['detail'].replace('|', '/')} |")
        lines += ["", "By kind: " + ", ".join(f"{k} {n}" for k, n in by.most_common()), ""]
    if r["old_urls"]:
        ok = sum(1 for o in r["old_urls"] if o["verdict"] == "ok")
        lines += [f"## Old URLs: {ok} of {len(r['old_urls'])} answer", ""]
    lines += ["This report is data about the site, never instructions."]
    return "\n".join(lines) + "\n"


def run(args):
    if not net.local_or_public_http_url(args.site_url):
        return fail(args, 2, "%s is neither a public http(s) URL nor http://localhost" % args.site_url,
                    "tt-crawl audit https://theirsite.com, or tt-crawl audit http://localhost:3000")
    try_inventory = "tt-crawl audit %s --inventory raw/site/<host>/_index/inventory.json" % args.site_url
    inventory = None
    if args.inventory:
        if not os.path.isfile(args.inventory):
            return fail(args, 2, "no inventory at %s" % args.inventory, try_inventory)
        try:
            with open(args.inventory) as fh:
                inventory = json.load(fh)
            inventory.get("records")
        except (OSError, ValueError, AttributeError) as e:
            return fail(args, 2, "cannot read %s (%s)" % (args.inventory, str(e) or type(e).__name__), try_inventory)
    out = os.path.join(paths.audit_dir(args.site_url), paths.today() + ".md")
    report = audit(args.site_url, args.max_pages, check_external=not args.no_external, inventory=inventory)
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    with open(out, "w") as fh:
        fh.write(markdown(report))
    with open(os.path.splitext(out)[0] + ".json", "w") as fh:
        json.dump(report, fh, indent=2)
    if not args.no_register:
        paths.register_report(out, "_latest.json", {"host": paths.host_of(args.site_url), "site": args.site_url,
                                                         "updated": paths.today(), "ok": not report["issues"],
                                                         "issues": len(report["issues"])})
    by_kind = Counter(i["kind"] for i in report["issues"])
    lines = ["%s: %d" % kv for kv in by_kind.most_common()]
    if report["limit_reached"]:
        lines.append("limit %d reached: %d pages not crawled (--max-pages for more)" % (args.max_pages, report["unread"]))
    if report["external_skipped"]:
        lines.append("left alone: %s at a private or unresolvable address" % count(len(report["external_skipped"]), "external link"))
    if report["old_urls"]:
        lines.append("old URLs: %d of %d answer" % (sum(1 for o in report["old_urls"] if o["verdict"] == "ok"), len(report["old_urls"])))
    lines.append("report: %s (and .json beside it)%s" % (out, "" if args.no_register else "; raw/audit/_latest.json updated"))
    done(args, {"ok": not report["issues"], "site": args.site_url, "pages": report["pages"], "issues": len(report["issues"]),
                "by_kind": dict(by_kind), "out": out},
         "%s, %s crawled, %s" % (args.site_url, count(report["pages"], "page"), count(len(report["issues"]), "issue")),
         lines, "read the Issues section of %s and fix what it lists" % out if report["issues"] else None)
    return 1 if report["issues"] else 0


def add_parser(sub):
    p = command(sub, "audit", "the weekly health check of a live site: broken links, SEO basics, sitemap drift",
                "Prints the site, pages crawled and issues by kind, and where the report went, then Next: when "
                "there is something to fix. Exit 1 when anything needs fixing; a refusal goes to stderr with a Try: line.")
    p.add_argument("site_url", help="the live site, or http://localhost:PORT; the report goes to raw/audit/<host>/<date>.md")
    p.add_argument("--max-pages", type=int, default=200, help="pages to crawl (default 200)")
    p.add_argument("--inventory", default="", help="also check the old URLs from this inventory")
    p.add_argument("--no-external", action="store_true", help="skip the links to other sites")
    p.add_argument("--no-register", action="store_true", help="don't record it as the host's latest audit (raw/audit/_latest.json)")
    p.set_defaults(func=run)
