"""The migration ledger: one record per discovered URL, and the same as a
table people can read."""
import json
import os

FIELDS = ["url", "status", "final_url", "title", "meta_description", "h1", "h1_count", "canonical", "lang",
          "word_count", "inbound_sitewide", "inbound_body", "outbound_internal", "in_sitemap", "lastmod",
          "jsonld_types", "og_image", "noindex", "hreflang", "forms", "embeds", "tracking", "documents",
          "template", "fingerprint", "file", "thin", "reason", "screenshot", "screenshot_error", "rendered", "traffic"]


def new_record(url):
    return {"url": url, "status": None, "final_url": None, "title": "", "meta_description": "", "h1": "",
            "h1_count": 0, "canonical": None, "lang": "", "word_count": 0,
            "inbound_sitewide": {"count": 0, "from": []}, "inbound_body": {"count": 0, "from": []},
            "outbound_internal": [], "in_sitemap": False, "lastmod": None, "jsonld_types": [],
            "og_image": None, "noindex": False, "hreflang": [], "forms": [], "embeds": [], "tracking": {},
            "documents": [], "template": None, "fingerprint": None, "file": None, "thin": False, "reason": None, "screenshot": None,
            "screenshot_error": None, "rendered": False, "traffic": None}


def add_inbound(records, from_url, to_url, sitewide):
    rec = records.get(to_url)
    if rec is None:
        return
    key = "inbound_sitewide" if sitewide else "inbound_body"
    if from_url not in rec[key]["from"]:
        rec[key]["from"].append(from_url)
        rec[key]["count"] = len(rec[key]["from"])


def write(records, out_dir, start, extra=None):
    ordered = sorted(records.values(), key=lambda r: (r["file"] is None, r["url"]))
    data = {"start": start, "count": len(ordered), "records": ordered}
    if extra:
        data.update(extra)
    with open(os.path.join(out_dir, "inventory.json"), "w") as f:
        json.dump(data, f, indent=2)
    with open(os.path.join(out_dir, "inventory.md"), "w") as f:
        f.write(markdown(ordered, start))
    return data


def markdown(records, start):
    lines = ["# Site inventory: %s" % start, "",
             "One row per URL the crawl discovered. `body links` counts editorial links from other pages",
             "(the signal that a page mattered); `sitewide` counts header and footer links, which hit every",
             "page equally. `file` is the page's markdown under pages/, or why it was skipped.", "",
             "| url | status | title | h1 | words | body links | sitewide | sitemap | noindex | forms | embeds | file |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in records:
        lines.append("| %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s |" % (
            r["url"], r["status"] if r["status"] is not None else "", _cell(r["title"]), _cell(r["h1"]),
            r["word_count"], r["inbound_body"]["count"], r["inbound_sitewide"]["count"],
            "yes" if r["in_sitemap"] else "", "yes" if r["noindex"] else "",
            len(r["forms"]) or "", ", ".join(sorted({e["kind"] for e in r["embeds"]})) or "",
            r["file"] or (r["reason"] or "")))
    tracking = {}
    for r in records:
        for k, vs in (r["tracking"] or {}).items():
            for v in vs:
                tracking.setdefault(k, set()).add(v)
    if tracking:
        lines += ["", "## Tracking IDs found (carry these over on day one)"]
        lines += ["- %s: %s" % (k, ", ".join(sorted(vs))) for k, vs in sorted(tracking.items())]
    return "\n".join(lines) + "\n"


def _cell(text, n=60):
    text = (text or "").replace("|", "/").strip()
    return text if len(text) <= n else text[:n - 1] + "…"
