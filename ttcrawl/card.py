"""The business card: a crawl's business facts in the forms a listing uses
(schema.org opening hours, an address in parts, the IANA time zone where the
address leaves no doubt), with the logo and the photographs worth using.

Built from the files a crawl wrote, so any crawl folder can be read again.
Each field names the file it came from in `sources`; facts.json keeps the
words as the site wrote them.
"""
import json
import os
import re
from urllib.parse import urlsplit

from . import paths

FILE = "business-card.json"
PHOTOS_KEPT = 16

# Only where the region or country has one zone: anywhere else the card
# leaves time_zone empty rather than guess.
US_ONE_ZONE = {
    "AL": "America/Chicago", "AR": "America/Chicago", "CA": "America/Los_Angeles", "CO": "America/Denver",
    "CT": "America/New_York", "DC": "America/New_York", "DE": "America/New_York", "GA": "America/New_York",
    "HI": "Pacific/Honolulu", "IA": "America/Chicago", "IL": "America/Chicago", "LA": "America/Chicago",
    "MA": "America/New_York", "MD": "America/New_York", "ME": "America/New_York", "MN": "America/Chicago",
    "MO": "America/Chicago", "MS": "America/Chicago", "MT": "America/Denver", "NC": "America/New_York",
    "NH": "America/New_York", "NJ": "America/New_York", "NM": "America/Denver", "NY": "America/New_York",
    "OH": "America/New_York", "OK": "America/Chicago", "PA": "America/New_York", "RI": "America/New_York",
    "SC": "America/New_York", "UT": "America/Denver", "VA": "America/New_York", "VT": "America/New_York",
    "WA": "America/Los_Angeles", "WI": "America/Chicago", "WV": "America/New_York", "WY": "America/Denver",
}
COUNTRY_ONE_ZONE = {
    "GB": "Europe/London", "UK": "Europe/London", "UNITED KINGDOM": "Europe/London", "IE": "Europe/Dublin",
    "IRELAND": "Europe/Dublin", "FR": "Europe/Paris", "DE": "Europe/Berlin", "NL": "Europe/Amsterdam",
    "BE": "Europe/Brussels", "IT": "Europe/Rome", "CH": "Europe/Zurich", "AT": "Europe/Vienna",
    "SE": "Europe/Stockholm", "NO": "Europe/Oslo", "DK": "Europe/Copenhagen", "FI": "Europe/Helsinki",
    "PL": "Europe/Warsaw", "NZ": "Pacific/Auckland", "SG": "Asia/Singapore", "JP": "Asia/Tokyo",
    "ZA": "Africa/Johannesburg",
}

DAY_CODES = ["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"]
HOURS_LINE_RE = re.compile(r"^\s*(mon|tue|wed|thu|fri|sat|sun)[a-z]*\.?\s*:?\s*(\d{1,2})(?::(\d{2}))?\s*([ap])\.?m\.?\s*"
                           r"[-–to]+\s*(\d{1,2})(?::(\d{2}))?\s*([ap])\.?m", re.I)
SCHEMA_HOURS_RE = re.compile(r"^[A-Z][a-z](-[A-Z][a-z])?(,[A-Z][a-z])*\s+\d")
US_LINE_RE = re.compile(r"^(.+?),\s*([^,]+),\s*([A-Z]{2})\s+(\d{5}(?:-\d{4})?)(?:,\s*(?:USA|US|United States))?$")
TITLE_SPLIT_RE = re.compile(r"\s[|–-]\s")
# a title part that names the page, not the business ("Home | PGT Windows")
GENERIC_TITLE_RE = re.compile(r"^(home|home ?page|welcome|index|main|official site|official website)$", re.I)


def name_from_title(title, host=""):
    """The business's name in the homepage's title (pure): the part that
    shares a word with the domain ("PGT Windows" for pgtwindows.com), else
    the first part that is not a page's own name ("Home")."""
    parts = [p.strip() for p in TITLE_SPLIT_RE.split(title or "") if p.strip()]
    named = [p for p in parts if not GENERIC_TITLE_RE.match(p)]
    label = re.sub(r"^www\.", "", host or "").split(".")[0].lower()
    for p in named:
        words = [w for w in re.findall(r"[a-z0-9]+", p.lower()) if len(w) > 2]
        if label and any(w in label for w in words):
            return p
    return named[0] if named else ""


def runs(by_day):
    """{day code: "08:00-17:00"} → schema.org strings in day order, a run of
    days with the same times joined ("Mo-Fr 08:00-17:00")."""
    out, i = [], 0
    while i < 7:
        t = by_day.get(DAY_CODES[i])
        if not t:
            i += 1
            continue
        j = i
        while j + 1 < 7 and by_day.get(DAY_CODES[j + 1]) == t:
            j += 1
        out.append("%s%s %s" % (DAY_CODES[i], "-" + DAY_CODES[j] if j > i else "", t))
        i = j + 1
    return out


def schema_hours(lines):
    """"Mon: 8:00am - 6:00pm" lines → schema.org strings; a line that does not
    read cleanly (a closed day, a range of days) adds nothing."""
    def t24(h, m, ap):
        return "%02d:%s" % (int(h) % 12 + (12 if ap.lower() == "p" else 0), m or "00")
    by_day = {}
    for line in lines:
        m = HOURS_LINE_RE.match(str(line))
        if m:
            by_day[m.group(1)[:2].capitalize()] = "%s-%s" % (t24(m.group(2), m.group(3), m.group(4)),
                                                              t24(m.group(5), m.group(6), m.group(7)))
    return runs(by_day)


def address_from(a, line):
    """The markup's address `a` in parts (a PostalAddress, or one line), else
    the page's `line`: a one-line US address is split, any other stays whole
    as the street. (address, True when it came from the markup), or
    (None, False) when there is none."""
    if isinstance(a, dict) and (a.get("streetAddress") or a.get("addressLocality")):
        country = a.get("addressCountry") or ""
        if isinstance(country, dict):
            country = country.get("name") or ""
        return {"street": a.get("streetAddress") or "", "locality": a.get("addressLocality") or "",
                "region": a.get("addressRegion") or "", "postal_code": a.get("postalCode") or "",
                "country": country}, True
    marked = isinstance(a, str) and bool(a)
    line = a if marked else line
    if not line:
        return None, False
    m = US_LINE_RE.match(line)
    if m:
        return {"street": m.group(1), "locality": m.group(2), "region": m.group(3), "postal_code": m.group(4),
                "country": "US"}, marked
    return {"street": line, "locality": "", "region": "", "postal_code": "", "country": ""}, marked


def time_zone(address):
    if not address:
        return ""
    country = str(address.get("country") or "").strip()
    if country.upper() in ("", "US", "USA", "UNITED STATES"):
        zone = US_ONE_ZONE.get(address.get("region") or "")
        if zone:
            return zone
    return COUNTRY_ONE_ZONE.get(country.upper(), "")


def profiles(urls):
    """Each profile once: http and https, www or not, a trailing slash or a
    capital are the same page; the https form is kept, in first-seen order."""
    kept = {}
    for u in urls:
        s = urlsplit(u)
        key = ((s.hostname or "").lower().removeprefix("www."), s.path.rstrip("/").lower())
        if key not in kept or (kept[key].startswith("http:") and u.startswith("https:")):
            kept[key] = u
    return list(kept.values())


def build(out):
    """The card for the crawl folder `out`, from its _index/ and structured/ files."""
    def read(rel, fallback):
        p = os.path.join(out, rel)
        try:
            with open(p) as f:
                return json.load(f)
        except (OSError, ValueError):
            return fallback

    facts_file, markup_file, inventory_file, media_file = (
        "_index/facts.json", "structured/business.json", "_index/inventory.json", "_index/media.json")
    facts = read(facts_file, {})
    markup = read(markup_file, {})
    records = read(inventory_file, {}).get("records") or []
    media = read(media_file, [])
    home = records[0] if records else {}
    host = paths.host_of(home.get("url") or "") if home.get("url") else ""

    def first(kind):
        rows = facts.get(kind) or []
        return rows[0]["value"] if rows else ""

    def text(v):
        return v if isinstance(v, str) else ""

    sources = {}

    def put(field, value, source):
        if value:
            sources[field] = source
        return value

    title = name_from_title(home.get("title") or "", host)
    name = (put("name", text(markup.get("name")), markup_file) or put("name", title, inventory_file)
            or put("name", host, inventory_file))
    telephone = put("telephone", first("phone"), facts_file) or put("telephone", text(markup.get("telephone")), markup_file)
    email = put("email", first("email"), facts_file) or put("email", text(markup.get("email")), markup_file)
    address, marked_address = address_from(markup.get("address"), first("address"))
    if address:
        sources["address"] = markup_file if marked_address else facts_file
    hours_raw = markup.get("openingHours")
    marked = [h for h in (hours_raw if isinstance(hours_raw, list) else [hours_raw])
              if isinstance(h, str) and SCHEMA_HOURS_RE.match(h)]
    hours = put("opening_hours", marked, markup_file) or put(
        "opening_hours", schema_hours(r["value"] for r in facts.get("hours") or []), facts_file)
    zone = time_zone(address)
    if zone:
        sources["time_zone"] = sources["address"]
    same_raw = markup.get("sameAs")
    same_as = put("same_as", profiles(
        [r["value"] for r in facts.get("social") or []]
        + [s for s in (same_raw if isinstance(same_raw, list) else [same_raw]) if isinstance(s, str) and s]), facts_file)
    description = (put("description", text(markup.get("description")), markup_file)
                   or put("description", home.get("meta_description") or "", inventory_file))

    def on_disk(it):
        return it.get("file") and os.path.isfile(os.path.join(out, "images", it["file"]))

    def picture(it):
        return {"file": "images/" + it["file"], "width": it.get("width"), "height": it.get("height")}

    logos = sorted((it for it in media if it.get("kind") == "logo" and on_disk(it)), key=lambda it: -(it.get("width") or 0))
    photos = sorted((it for it in media if it.get("kind") == "photo" and on_disk(it)), key=lambda it: -(it.get("width") or 0))
    if logos:
        sources["logo"] = media_file
    if photos:
        sources["photos"] = media_file
    return {
        "name": name, "telephone": telephone, "email": email, "address": address,
        "opening_hours": hours, "time_zone": zone, "same_as": same_as, "description": description,
        "logo": picture(logos[0]) if logos else None,
        "photos": [dict(picture(it), alt=(it.get("alts") or [""])[0] or "",
                        heading=((it.get("pages") or [{}])[0] or {}).get("heading") or "")
                   for it in photos[:PHOTOS_KEPT]],
        "sources": sources,
    }


def write(out):
    card = build(out)
    with open(paths.index(out, FILE), "w") as f:
        json.dump(card, f, indent=2, ensure_ascii=False)
    return card


def load(out):
    """The card a crawl folder holds, or None when it has none."""
    try:
        with open(paths.index(out, FILE)) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None
