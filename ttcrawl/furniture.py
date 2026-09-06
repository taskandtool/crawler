"""The header and footer as structure: the old site's information
architecture, read from the DOM landmarks rather than reconstructed from
repeated text."""
import re
from urllib.parse import urlsplit

from .html import BADGE_RE, COPYRIGHT_RE, CTA_RE, LEGAL_RE, SOCIAL_HOSTS
from .net import same_site
from .text import norm_line


def _host(url):
    return (urlsplit(url).hostname or "").lower().removeprefix("www.")


def is_social(href):
    return any(_host(href) == h or _host(href).endswith("." + h) for h in SOCIAL_HOSTS)


def nav_tree(links):
    """Nested nav from flat links carrying a list depth: a link at depth d+1
    following one at depth d is its child."""
    root, stack = [], []   # stack of (depth, node)
    for i, l in enumerate(links):
        node = {"label": l["text"], "href": l["href"], "order": i, "children": []}
        depth = l.get("depth") or 0
        while stack and stack[-1][0] >= depth:
            stack.pop()
        (stack[-1][1]["children"] if stack else root).append(node)
        stack.append((depth, node))
    return root


def page_furniture(parsed, root_host):
    """One page's header and footer as structure."""
    links = parsed["links"]
    header_links = [l for l in links if l["landmark"] in ("header", "nav")]
    footer_links = [l for l in links if l["landmark"] == "footer"]
    nav_links = [l for l in header_links if l["text"] and not l["href"].startswith(("tel:", "mailto:"))
                 and same_site(l["href"], root_host) and not _looks_like_cta(l) and not _skip_link(l)]
    cta = next((l for l in header_links if l["href"].startswith(("tel:", "mailto:")) or _looks_like_cta(l)), None)
    if cta is None:
        cta = next((l for l in footer_links if l["href"].startswith("tel:")), None)
    groups = {}
    for l in footer_links:
        if is_social(l["href"]) or LEGAL_RE.search(l["text"] or "") or LEGAL_RE.search(urlsplit(l["href"]).path):
            continue
        groups.setdefault(l.get("group") or "", []).append({"label": l["text"], "href": l["href"]})
    footer_groups = [{"heading": h, "links": ls} for h, ls in groups.items() if ls]
    social = _unique([{"label": l["text"], "href": l["href"]} for l in links if is_social(l["href"])])
    legal = _unique([{"label": l["text"], "href": l["href"]} for l in links
                     if l["landmark"] == "footer" and (LEGAL_RE.search(l["text"] or "") or LEGAL_RE.search(urlsplit(l["href"]).path))])
    copyright_line = next((t for t in parsed["landmark_lines"].get("footer", []) if COPYRIGHT_RE.search(t)), "")
    logo = next(({"src": i["src"], "alt": i["alt"]} for i in parsed["images"]
                 if i["landmark"] in ("header", "nav") and i["src"] and (i["in_link"] or "logo" in (i["alt"] + i["src"]).lower())), None)
    badges = _unique([{"src": i["src"], "alt": i["alt"]} for i in parsed["images"]
                      if i["landmark"] in ("footer", "header", "aside") and i["src"] and i is not None
                      and (not logo or i["src"] != logo["src"]) and BADGE_RE.search(i["alt"] + " " + i["src"])])
    return {
        "url": parsed["url"],
        "has_landmarks": parsed["has_landmarks"],
        "logo": logo,
        "nav": nav_tree(nav_links),
        "primary_cta": {"label": cta["text"], "href": cta["href"]} if cta else None,
        "footer_groups": footer_groups,
        "social": social,
        "legal": legal,
        "copyright": copyright_line,
        "badges": badges,
        "landmarks": sorted(parsed["landmark_lines"].keys()),
    }


def _skip_link(l):
    """Accessibility skip links and menu toggles are not navigation."""
    text = (l["text"] or "").lower()
    path = urlsplit(l["href"])
    return text.startswith("skip ") or (not path.path.strip("/") and path.fragment and "#" in l["href"]) \
        or re.fullmatch(r"[\W_]*(menu|close|open|toggle)?[\W_]*", text) is not None


def _looks_like_cta(l):
    cls = " ".join(l.get("classes") or [])
    return bool(re.search(r"btn|button|cta", cls)) or (bool(CTA_RE.search(l["text"] or "")) and len(l["text"] or "") < 32)


def _unique(items):
    out, seen = [], set()
    for it in items:
        key = it.get("href") or it.get("src")
        if key and key not in seen:
            seen.add(key)
            out.append(it)
    return out


def site_furniture(per_page):
    """The site-level view: the nav and footer of the page that carries the
    most of them (usually every page carries the same), plus the union of
    social and legal links, and which pages differ."""
    pages = [p for p in per_page if p and p["has_landmarks"]]
    if not pages:
        return {"has_landmarks": False, "pages": len(per_page)}
    canonical = max(pages, key=lambda p: (len(_flat(p["nav"])) + sum(len(g["links"]) for g in p["footer_groups"]),
                                          -p.get("_order", 0)))
    sig = lambda p: [n["href"] for n in _flat(p["nav"])]
    differing = [p["url"] for p in pages if sig(p) != sig(canonical)]
    return {
        "has_landmarks": True,
        "pages": len(per_page),
        "from": canonical["url"],
        "logo": canonical["logo"] or next((p["logo"] for p in pages if p["logo"]), None),
        "nav": canonical["nav"],
        "primary_cta": canonical["primary_cta"] or next((p["primary_cta"] for p in pages if p["primary_cta"]), None),
        "footer_groups": canonical["footer_groups"],
        "social": _unique([s for p in pages for s in p["social"]]),
        "legal": _unique([s for p in pages for s in p["legal"]]),
        "copyright": canonical["copyright"] or next((p["copyright"] for p in pages if p["copyright"]), ""),
        "badges": _unique([b for p in pages for b in p["badges"]]),
        "pages_with_a_different_nav": differing,
    }


def _flat(nodes):
    out = []
    for n in nodes:
        out.append(n)
        out.extend(_flat(n["children"]))
    return out


def landmark_line_set(parsed_pages):
    """Every normalized text line and link label that sits inside a landmark
    on any page: what is stripped from the page bodies."""
    lines = set()
    for p in parsed_pages:
        for texts in p["landmark_lines"].values():
            for t in texts:
                key = norm_line(t)
                if key and len(key) > 1:
                    lines.add(key)
        for l in p["links"]:
            if l["landmark"] and l["text"]:
                lines.add(norm_line(l["text"]))
    return lines
