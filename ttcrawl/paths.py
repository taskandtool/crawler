"""Where a crawl's files go. One folder per site, named by its host:

    raw/site/<host>/          the owner's site (raw/external/<host>/ for anyone else's)
      pages/<name>.md         one file per page
      images/                 one file per picture
      shots/<name>/           screenshot strips
      structured/<name>.json  per-page structured data, and business.json
      docs/                   the documents the pages link to
      _index/                 the small ledger: inventory, templates, furniture, media,
                              facts, reviews, styles, common lines, the run, the manifest
      _cache/                 what was read, one file per page, so a crawl can resume
                              and `add` can write the folder again
    raw/audit/<host>/         audits and launch checks (reports only; they never touch pages)

`_index/` stays small whatever the site's size, so it can be mirrored to
another app when the pages and images cannot. A folder can be frozen (the
old site at launch): nothing writes into it again.
"""
import json
import os
from datetime import datetime, timezone
from urllib.parse import urlsplit

PAGES, IMAGES, SHOTS, STRUCTURED, DOCS, INDEX, CACHE = "pages", "images", "shots", "structured", "docs", "_index", "_cache"
FROZEN = "frozen.json"


def host_of(url):
    """A site's folder name: its host, lowercase, without www. (pure)."""
    return (urlsplit(url).hostname or "").lower().removeprefix("www.")


def site_dir(url, external=False, root="raw"):
    return os.path.join(root, "external" if external else "site", host_of(url))


def audit_dir(url, root="raw"):
    return os.path.join(root, "audit", host_of(url))


def the_site(root="raw"):
    """The one site folder under raw/site, when there is exactly one: the
    default for the commands that read a crawl (docs, check)."""
    base = os.path.join(root, "site")
    found = [os.path.join(base, d) for d in sorted(os.listdir(base))] if os.path.isdir(base) else []
    found = [d for d in found if os.path.isdir(d)]
    return found[0] if len(found) == 1 else None


def ledger(folder, name):
    """A ledger file of a crawl folder, new layout first, then a 0.1.x one's."""
    new = index(folder, name)
    return new if os.path.isfile(new) or not os.path.isfile(os.path.join(folder, "_" + name)) else os.path.join(folder, "_" + name)


def today():
    return datetime.now(timezone.utc).date().isoformat()


def register(path, key, entry, defaults=None):
    """Upsert one entry into a small registry file at a fixed path (the
    starter apps' suggestion conditions can only name fixed paths; the site
    and audit folders carry the host in theirs). An entry is merged into the
    one it replaces, so a flag another command set survives; `defaults` fill
    what neither has."""
    try:
        with open(path) as f:
            data = json.load(f)
    except (OSError, ValueError):
        data = {}
    old = next((r for r in data.get("entries", []) if r.get(key) == entry.get(key)), {})
    rows = [r for r in data.get("entries", []) if r.get(key) != entry.get(key)]
    merged = {**(defaults or {}), **old, **entry}
    data["entries"] = sorted(rows + [merged], key=lambda r: r.get("updated", ""), reverse=True)
    data["latest"] = data["entries"][0]
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, path)


SITE_FLAGS = {"docs_fetched": False, "wp_imported": False}


def register_site(out, entry):
    """raw/site/_sites.json: every site folder beside `out`, newest first,
    with the flags a suggestion can test ("docs_fetched": false until
    `tt-crawl docs` ran, "wp_imported": false until `tt-crawl wp` did). Only
    in the standard layout: a crawl into a folder of its own choosing leaves
    the folder around it alone."""
    parent = os.path.dirname(out.rstrip("/")) or "."
    if os.path.basename(parent) in ("site", "external"):
        register(os.path.join(parent, "_sites.json"), "folder", dict(entry, folder=out.rstrip("/")), SITE_FLAGS)


def register_report(report_path, name, entry):
    """raw/audit/_latest.json (audits) or _launch.json (launch checks): the
    newest report per host, and which is newest overall."""
    folder = os.path.dirname(os.path.dirname(report_path.rstrip("/"))) or "."
    if os.path.basename(folder) == "audit":
        register(os.path.join(folder, name), "host", dict(entry, report=report_path))


def index(out, name):
    return os.path.join(out, INDEX, name)


def old_layout(out):
    """A folder a tt-crawl before 0.2 wrote (pages and ledger at its root)."""
    return os.path.isfile(os.path.join(out, "_manifest.json")) or os.path.isfile(os.path.join(out, "_inventory.json"))


def frozen(out):
    """The freeze note of a frozen folder, or None."""
    try:
        with open(index(out, FROZEN)) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def freeze(out, reason=""):
    os.makedirs(os.path.join(out, INDEX), exist_ok=True)
    note = {"frozen_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            "reason": reason}
    with open(index(out, FROZEN), "w") as f:
        json.dump(note, f, indent=2)
    return note
