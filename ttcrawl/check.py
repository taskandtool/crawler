"""`tt-crawl check`: the launch check. Every URL the inventory knows is
requested on the new host; each row gets a verdict; the new sitemap is
diffed against the inventory; the home page's JSON-LD must parse."""
import json
import os
import sys
from urllib.parse import urlsplit, urlunsplit

from . import net, paths
from .html import parse_page
from .site import parse_sitemap
from .structured import jsonld

OK, MISSING, CHAIN, NOINDEX, ERROR = "ok", "missing", "chain", "noindex", "error"


def map_url(old_url, new_base):
    o, n = urlsplit(old_url), urlsplit(new_base)
    return urlunsplit((n.scheme, n.netloc, o.path or "/", o.query, ""))


def verdict(status, chain, noindex):
    """The rule per row (pure): 200 or one redirect to a 200 is ok; a 404 is
    missing; more than one hop is a chain; a noindex on a kept page is a
    finding; anything else is an error."""
    if status == 200 and noindex:
        return NOINDEX
    if status == 200 and len(chain) <= 1:
        return OK
    if status == 200:
        return CHAIN
    if status in (404, 410):
        return MISSING
    return ERROR


def check_url(new_url, fetch=net.fetch, retries=1):
    try:
        r = fetch(new_url, cap=2_000_000)
    except Exception as e:
        if retries:
            return check_url(new_url, fetch=fetch, retries=retries - 1)
        return {"status": None, "chain": [], "final_url": None, "error": str(e)[:120]}
    html = r["body"].decode("utf-8", "replace") if r["status"] == 200 else ""
    p = parse_page(html, r["final_url"] or new_url) if html else None
    return {"status": r["status"], "chain": [c[1] for c in r["chain"]], "final_url": r["final_url"],
            "title": p["title"] if p else "", "h1_count": p["h1_count"] if p else 0,
            "meta_description": p["meta_description"] if p else "", "canonical": p["canonical"] if p else None,
            "noindex": p["noindex"] if p else False, "jsonld_types": [t for t in (jsonld_types_of(p) if p else [])]}


def jsonld_types_of(parsed):
    from .structured import jsonld_types
    return jsonld_types(jsonld(parsed))


def run(args):
    site = paths.the_site()
    args.inventory = args.inventory or (paths.index(site, "inventory.json") if site else None)
    if not args.inventory or not os.path.isfile(args.inventory):
        sys.stderr.write("check: %s\n  Crawl folders: %s\n"
                         "  Try: tt-crawl check %s --inventory raw/site/<host>/_index/inventory.json\n"
                         % ("no inventory at %s" % args.inventory if args.inventory
                            else "needs --inventory, the old site's raw/site/<host>/_index/inventory.json",
                            ", ".join(paths.site_folders()) or "none", args.new_base_url))
        return 2
    with open(args.inventory) as f:
        inv = json.load(f)
    out = os.path.join(paths.audit_dir(inv.get("start") or args.new_base_url), "launch-%s.md" % paths.today())
    new_base = args.new_base_url.rstrip("/")
    if not net.local_or_public_http_url(new_base):
        sys.stderr.write("refusing: the new base is neither a public http(s) url nor http://localhost\n")
        return 2
    rows = []
    for rec in inv.get("records", []):
        if rec.get("reason") in ("unread", "robots") or (rec.get("status") not in (200, None) and not rec.get("file")):
            continue
        if rec.get("status") is None and not rec.get("file"):
            continue
        new_url = map_url(rec["url"], new_base)
        r = check_url(new_url)
        v = verdict(r["status"], r["chain"], r.get("noindex", False))
        findings = []
        if v == OK:
            if not r.get("title"):
                findings.append("no title")
            if r.get("h1_count", 0) != 1:
                findings.append("%d h1" % r.get("h1_count", 0))
            if not r.get("meta_description"):
                findings.append("no description")
        rows.append({"old_url": rec["url"], "new_url": new_url, "verdict": v, "status": r["status"], "chain": r["chain"],
                     "final_url": r["final_url"], "title": r.get("title", ""), "old_title": rec.get("title", ""),
                     "findings": findings, "error": r.get("error")})
    # the new sitemap against the inventory's kept pages
    sitemap_urls = set()
    try:
        sm = net.fetch(new_base + "/sitemap.xml")
        if sm["status"] == 200:
            sitemap_urls = {urlsplit(u).path.rstrip("/") or "/" for u, _ in parse_sitemap(sm["body"].decode("utf-8", "replace"))}
    except Exception:
        pass
    kept_paths = {urlsplit(r["old_url"]).path.rstrip("/") or "/" for r in rows if r["verdict"] == OK and not r["chain"]}
    missing_from_sitemap = sorted(kept_paths - sitemap_urls) if sitemap_urls else []
    new_in_sitemap = sorted(sitemap_urls - {urlsplit(r["old_url"]).path.rstrip("/") or "/" for r in rows}) if sitemap_urls else []
    home = check_url(new_base + "/")
    counts = {v: sum(1 for r in rows if r["verdict"] == v) for v in (OK, MISSING, CHAIN, NOINDEX, ERROR)}
    report = {"new_base": new_base, "inventory": args.inventory, "rows": rows, "counts": counts,
              "sitemap": {"fetched": bool(sitemap_urls), "entries": len(sitemap_urls), "missing_from_sitemap": missing_from_sitemap,
                          "new_in_sitemap": new_in_sitemap},
              "home": {"status": home["status"], "jsonld_types": home.get("jsonld_types", []), "title": home.get("title", "")}}
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    with open(out, "w") as f:
        f.write(markdown(report))
    with open(os.path.splitext(out)[0] + ".json", "w") as f:
        json.dump(report, f, indent=2)
    paths.register_report(out, "_launch.json", {"host": paths.host_of(inv.get("start") or new_base), "new_base": new_base,
                                                     "updated": paths.today(), "ok": not (counts[MISSING] or counts[ERROR]),
                                                     "missing": counts[MISSING], "errors": counts[ERROR]})
    print(json.dumps({"checked": len(rows), **counts, "sitemap_entries": len(sitemap_urls),
                      "missing_from_sitemap": len(missing_from_sitemap), "home_jsonld": home.get("jsonld_types", []), "out": out}))
    return 1 if counts[MISSING] or counts[ERROR] else 0


def markdown(report):
    c = report["counts"]
    lines = ["# Launch check: %s" % report["new_base"], "",
             "%d old URLs checked: %d ok, %d missing, %d redirect chains, %d noindex, %d errors." % (
                 len(report["rows"]), c[OK], c[MISSING], c[CHAIN], c[NOINDEX], c[ERROR]), "",
             "| old url | verdict | status | final url | title | findings |", "|---|---|---|---|---|---|"]
    for r in sorted(report["rows"], key=lambda r: (r["verdict"] != MISSING, r["verdict"] != ERROR, r["old_url"])):
        lines.append("| %s | %s | %s | %s | %s | %s |" % (r["old_url"], r["verdict"], r["status"] or (r.get("error") or ""),
                                                     r["final_url"] or "", (r["title"] or "")[:60].replace("|", "/"),
                                                     ", ".join(r["findings"])))
    sm = report["sitemap"]
    lines += ["", "## Sitemap", ""]
    if not sm["fetched"]:
        lines.append("- sitemap.xml was not found on the new site")
    else:
        lines.append("- %d entries" % sm["entries"])
        lines += ["- kept page missing from the sitemap: %s" % p for p in sm["missing_from_sitemap"]]
        lines += ["- new in the sitemap (not on the old site): %s" % p for p in sm["new_in_sitemap"][:40]]
    h = report["home"]
    lines += ["", "## Home page", "", "- status %s, title %s" % (h["status"], json.dumps(h["title"])),
              "- JSON-LD types: %s" % (", ".join(h["jsonld_types"]) or "none parsed")]
    return "\n".join(lines) + "\n"


def add_parser(sub):
    p = sub.add_parser("check", help="the launch check: every inventory URL on the new host, the sitemap, the home page's JSON-LD")
    p.add_argument("new_base_url", help="the new site: http://localhost:PORT before publishing, its public URL after")
    p.add_argument("--inventory", default=None, help="the old site's inventory (default: the one raw/site/<host>)")
    p.set_defaults(func=run)
