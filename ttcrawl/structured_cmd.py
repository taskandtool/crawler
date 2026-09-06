"""`tt-crawl structured`: the harvest for given URLs, the same code path the
site crawl runs inline."""
import json
import os
import sys

from . import browser, net, structured
from .html import parse_page
from .text import slugify


def run(args):
    os.makedirs(args.out, exist_ok=True)
    obscura = None if args.static else browser.find_obscura()
    results, business_inputs = [], []
    for url in args.urls:
        if not net.public_http_url(url):
            sys.stderr.write("skipping (not a public http url): %s\n" % url)
            continue
        html = browser.render_html(url, obscura) if obscura else None
        if html is None:
            try:
                html = net.fetch_text(url)
            except Exception as e:
                sys.stderr.write("failed: %s (%s)\n" % (url, e))
                continue
        parsed = parse_page(html, url)
        rec = structured.page_structured(parsed, html)
        with open(os.path.join(args.out, slugify(url) + ".json"), "w") as f:
            json.dump(rec, f, indent=2)
        results.append({"url": url, "jsonld_types": rec["jsonld_types"], "tracking": list(rec["tracking"].keys()),
                        "embeds": len(rec["embeds"])})
        business_inputs.append((url, rec["jsonld"]))
    business = structured.merge_business(business_inputs)
    if business:
        with open(os.path.join(args.out, "business.json"), "w") as f:
            json.dump(business, f, indent=2)
    print(json.dumps({"pages": len(results), "business_markup": bool(business), "results": results, "out": args.out}))
    return 0


def add_parser(sub):
    p = sub.add_parser("structured", help="JSON-LD, Open Graph, microdata, tracking IDs, and embeds for given pages")
    p.add_argument("urls", nargs="+")
    p.add_argument("--out", default="raw/structured")
    p.add_argument("--static", action="store_true")
    p.set_defaults(func=run)
