"""The structured-data harvest: JSON-LD, Open Graph, microdata, tracking
IDs, and embedded third-party widgets, exactly as the page carries them."""
import json
import re
from urllib.parse import urlsplit

TRACKING_PATTERNS = [
    ("ga4", re.compile(r"\bG-[A-Z0-9]{6,12}\b")),
    ("gtm", re.compile(r"\bGTM-[A-Z0-9]{4,10}\b")),
    ("ua", re.compile(r"\bUA-\d{4,10}-\d{1,3}\b")),
    ("meta_pixel", re.compile(r"fbq\(\s*['\"]init['\"]\s*,\s*['\"](\d{6,20})['\"]")),
    ("meta_pixel", re.compile(r"facebook\.com/tr\?id=(\d{6,20})")),
    ("hotjar", re.compile(r"hjid\s*:\s*(\d{4,10})")),
    ("clarity", re.compile(r"clarity\.ms/tag/([a-z0-9]{5,20})")),
    ("google_ads", re.compile(r"\bAW-\d{6,12}\b")),
]
VERIFICATION_METAS = {"google-site-verification": "google_site_verification", "msvalidate.01": "bing_verification",
                      "facebook-domain-verification": "facebook_domain_verification", "p:domain_verify": "pinterest_verification"}
EMBED_HOSTS = [
    ("map", ("google.com/maps", "maps.google", "openstreetmap.org", "mapbox.com")),
    ("booking", ("calendly.com", "acuityscheduling.com", "squareup.com/appointments", "setmore.com", "simplybook", "booksy.com", "vagaro.com", "housecallpro.com", "jobber.com", "servicetitan.com")),
    ("chat", ("intercom", "crisp.chat", "tawk.to", "drift.com", "hubspot.com/messaging", "js.hs-scripts.com", "tidio", "livechat", "zendesk.com", "podium.com")),
    ("reviews", ("trustpilot.com", "elfsight.com", "reviews.io", "yotpo.com", "birdeye.com", "widget.reviews", "google.com/reviews")),
    ("video", ("youtube.com", "youtu.be", "vimeo.com", "wistia", "loom.com")),
    ("form", ("typeform.com", "jotform.com", "hsforms.net", "forms.gle", "cognitoforms.com", "formstack.com")),
    ("social", ("facebook.com/plugins", "instagram.com/embed", "platform.twitter.com", "tiktok.com/embed")),
    ("payments", ("stripe.com", "paypal.com/sdk", "square.js")),
]


def jsonld(parsed):
    """Parsed JSON-LD blocks, @graph flattened; bad JSON is skipped."""
    out = []
    for raw in parsed.get("jsonld_raw", []):
        txt = raw.strip()
        if not txt:
            continue
        try:
            data = json.loads(txt)
        except json.JSONDecodeError:
            try:
                data = json.loads(re.sub(r",\s*([}\]])", r"\1", txt))
            except json.JSONDecodeError:
                continue
        items = data if isinstance(data, list) else [data]
        for item in items:
            if isinstance(item, dict) and isinstance(item.get("@graph"), list):
                out.extend(i for i in item["@graph"] if isinstance(i, dict))
            elif isinstance(item, dict):
                out.append(item)
    return out


def jsonld_types(items):
    types = []
    for it in items:
        t = it.get("@type")
        for name in (t if isinstance(t, list) else [t]):
            if isinstance(name, str) and name not in types:
                types.append(name)
    return types


def open_graph(parsed):
    return {k: v for k, v in parsed.get("meta", {}).items() if k.startswith(("og:", "twitter:", "article:"))}


def tracking_ids(html):
    """Analytics and pixel IDs anywhere in the page, plus site verification
    metas: what must carry over on day one."""
    found = {}
    for kind, rx in TRACKING_PATTERNS:
        for m in rx.finditer(html):
            value = m.group(1) if m.groups() else m.group(0)
            found.setdefault(kind, [])
            if value not in found[kind]:
                found[kind].append(value)
    for m in re.finditer(r"<meta\s+[^>]*name=[\"']([^\"']+)[\"'][^>]*content=[\"']([^\"']+)[\"']", html, re.I):
        name = m.group(1).lower()
        if name in VERIFICATION_METAS:
            found.setdefault(VERIFICATION_METAS[name], []).append(m.group(2))
    return found


def embeds(parsed):
    """Third-party pieces the new site must reproduce or consciously drop."""
    out, seen = [], set()
    for src in list(parsed.get("iframes", [])) + list(parsed.get("scripts", [])):
        host = (urlsplit(src).hostname or "").lower()
        kind = next((k for k, hosts in EMBED_HOSTS if any(h in src.lower() for h in hosts)), None)
        if kind and src not in seen:
            seen.add(src)
            out.append({"kind": kind, "host": host, "src": src})
    return out


def page_structured(parsed, html):
    items = jsonld(parsed)
    return {
        "url": parsed["url"],
        "jsonld": items,
        "jsonld_types": jsonld_types(items),
        "open_graph": open_graph(parsed),
        "microdata": parsed.get("microdata", []),
        "tracking": tracking_ids(html),
        "embeds": embeds(parsed),
    }


BUSINESS_TYPES = ("LocalBusiness", "Organization", "Store", "Restaurant", "Dentist", "Plumber", "Electrician",
                  "RoofingContractor", "HomeAndConstructionBusiness", "MedicalBusiness", "LegalService",
                  "AutoRepair", "HairSalon", "BeautySalon", "Hotel", "RealEstateAgent", "ProfessionalService",
                  "FinancialService", "Physician", "Attorney", "FoodEstablishment", "Corporation")
BUSINESS_FIELDS = ("name", "legalName", "alternateName", "url", "telephone", "email", "address", "geo",
                   "openingHours", "openingHoursSpecification", "sameAs", "image", "logo", "priceRange",
                   "areaServed", "description", "founder", "foundingDate", "hasMap", "aggregateRating")


def merge_business(per_page_items):
    """One business record from every LocalBusiness or Organization item
    across the site: first value wins per field, sameAs and images union.
    `sources` lists the pages each field came from."""
    merged, sources, types = {}, {}, []
    for url, items in per_page_items:
        for it in items:
            t = it.get("@type")
            names = t if isinstance(t, list) else [t]
            if not any(isinstance(n, str) and (n in BUSINESS_TYPES or n.endswith(("Business", "Store", "Service", "Shop"))) for n in names) \
               and not ("telephone" in it and "address" in it):
                continue
            for n in names:
                if isinstance(n, str) and n not in types:
                    types.append(n)
            for field in BUSINESS_FIELDS:
                if field not in it:
                    continue
                used = False
                if field in ("sameAs", "image"):
                    vals = it[field] if isinstance(it[field], list) else [it[field]]
                    merged.setdefault(field, [])
                    for v in vals:
                        if v not in merged[field]:
                            merged[field].append(v)
                            used = True
                elif field not in merged:
                    merged[field] = it[field]
                    used = True
                if used:
                    sources.setdefault(field, [])
                    if url not in sources[field]:
                        sources[field].append(url)
    if not merged:
        return None
    merged["@types"] = types
    merged["sources"] = sources
    return merged
