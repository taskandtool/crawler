"""Business facts read off a site, each with where it came from: reviews as
records, and the contact facts (phones, emails, addresses, hours, social
profiles, the action links: book, quote, order).

What is recorded is what the site states, verbatim; nothing is derived, and
where two pages disagree both values are kept for the owner to settle.
Contact facts come only from structured data, the site's header and footer,
and its own home, contact, about and location pages: never from a review, a
comment, or a post, where a customer's phone number or a supplier's address
is someone else's fact.
"""
import re
from urllib.parse import urlsplit

REVIEW_AUTHOR_SEL_RE = re.compile(r"author|reviewer|(^|[\s_-])name($|[\s_-])|testimonial-name|client-name")
CARD_RE = re.compile(r"review|testimonial|quote|card")
QUOTE_CLASS_RE = re.compile(r"text|body|content|quote|review|testimonial")
DATE_RE = re.compile(r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]* \d{1,2},? \d{4}\b|\b\d{4}-\d{2}-\d{2}\b"
                     r"|\b\d{1,2} (?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]* \d{4}\b")
PLATFORMS = (("Google", re.compile(r"google|#4285f4", re.I)), ("Facebook", re.compile(r"facebook|#1877f2", re.I)),
             ("Yelp", re.compile(r"yelp", re.I)), ("Trustpilot", re.compile(r"trustpilot", re.I)),
             ("Houzz", re.compile(r"houzz", re.I)), ("Angi", re.compile(r"angi\b|angieslist", re.I)))

PHONE_RE = re.compile(r"(?:\+?\d{1,2}[\s.-]?)?\(?\d{3}\)?[\s.-]\d{3}[\s.-]\d{4}\b|\+44\s?\d{2,4}\s?\d{3,4}\s?\d{3,4}|\b0\d{2,4}\s\d{3,4}\s\d{3,4}\b")
EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")
ADDRESS_RE = re.compile(r"\b\d{1,6}\s+[A-Z][\w.'-]*(?:\s+[A-Z][\w.'-]*){0,5}\s+(?:Rd|Road|St|Street|Ave|Avenue|Blvd|Boulevard|Dr|Drive|"
                        r"Way|Lane|Ln|Ct|Court|Pl|Place|Hwy|Highway|Pkwy|Parkway|Terrace|Ter|Circle|Cir)\.?,?\s+[A-Z][\w.' -]+,?\s+"
                        r"[A-Z]{2}\s+\d{5}(?:-\d{4})?")
HOURS_RE = re.compile(r"\b(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)[a-z]*\.?(?:\s*(?:-|–|to|through)\s*(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)[a-z]*\.?)?"
                      r"[:,]?\s*\d{1,2}(?::\d{2})?\s*(?:am|pm|a\.m\.|p\.m\.)?\s*(?:-|–|to)\s*\d{1,2}(?::\d{2})?\s*(?:am|pm|a\.m\.|p\.m\.)?", re.I)
OWN_PAGE_RE = re.compile(r"contact|about|team|staff|location|find-us|visit|hours|directions|our-story|who-we-are|company", re.I)
BOOKING_HOST_RE = re.compile(r"(^|\.)(opentable\.[a-z.]+|resy\.com|exploretock\.com|sevenrooms\.com|calendly\.com|acuityscheduling\.com|"
                             r"squareup\.com|square\.site|toasttab\.com|housecallpro\.com|getjobber\.com|jobber\.com|servicetitan\.com|"
                             r"booksy\.com|vagaro\.com|mindbodyonline\.com|zocdoc\.com|typeform\.com|jotform\.com|hubspot\.com|cal\.com)$", re.I)
ACTION_TEXT_RE = re.compile(r"\b(?:book(?:ing)?|reserv(?:e|ation)|quote|estimates?|appointments?|schedul(?:e|ing)|order online|"
                            r"order now|get started|request|consult(?:ation)?)\b", re.I)
SOCIAL_HOST_RE = re.compile(r"(^|\.)(instagram|tiktok|facebook|fb|youtube|youtu|x|twitter|pinterest|linkedin|threads|yelp|nextdoor)\.(com|be|net)$", re.I)


# ── reviews ──

def _is_author(n):
    return n.tag == "cite" or n.attrs.get("itemprop") == "author" or bool(REVIEW_AUTHOR_SEL_RE.search(n.cls))


def reviews(root, url):
    """Review records on a page (pure over the element tree): a quote, the
    name beside it, and the date, platform and stars its card shows."""
    out = []
    for a in root.iter():
        if a.tag == "#root" or not _is_author(a) or a.in_chrome() or re.fullmatch(r"h[1-6]", a.tag):
            continue
        # the innermost: a <cite> holding the name and a job title is not the name
        if any(_is_author(n) for n in a.iter() if n is not a):
            continue
        # a button label, a heading, a menu item or a Drupal field wrapper is not an author
        if a.closest(lambda n: n.tag in ("a", "button")) or any(re.fullmatch(r"h[1-6]", n.tag) for n in a.iter()) \
                or "field--name-" in a.cls:
            continue
        name = re.sub(r"^[\s\-–—~•|]+", "", a.text())      # "– Susan R.": the dash is the site's typography
        if not name or len(name) > 60:
            continue
        # The card is the nearest wrapper that holds a quote beside the name:
        # page builders put the name in its own small footer, a level or two
        # below the card that holds the words.
        card, quote = None, ""
        for n in a.ancestors():
            if n.tag in ("#root", "body", "main") or len(n.links()) > 4:
                break
            if not (CARD_RE.search(n.cls) or n.tag in ("article", "li", "blockquote", "figure")
                    or "Review" in (n.attrs.get("itemtype") or "")):
                continue
            inside_a = set(map(id, a.iter()))
            quotes = [q for q in n.iter() if q is not a and id(q) not in inside_a and a not in list(q.iter())
                      and (q.tag in ("p", "blockquote") or q.attrs.get("itemprop") == "reviewBody" or QUOTE_CLASS_RE.search(q.cls))]
            quote = max((q.text() for q in quotes), key=len, default="")
            if len(quote) > 20 and re.search(r"[a-z]", quote) and quote != name and name not in quote:
                card = n
                break
        if card is None:
            continue
        raw = card.raw()
        platform = next((p for p, rx in PLATFORMS if rx.search(raw)), None)
        date = (DATE_RE.search(card.text()) or [None])[0]
        stars = sum(1 for n in card.iter() if "star" in n.cls and not n.children) or None
        rec = {"quote": quote, "name": name, "date": date, "platform": platform, "stars": stars, "url": url}
        if not any(r["quote"] == quote for r in out) and name not in quote:
            out.append(rec)
    return out


def jsonld_reviews(items, url):
    """Review records from structured data: Review items, and the ratings an
    AggregateRating states (pure)."""
    out, ratings = [], []

    def walk(o):
        if isinstance(o, list):
            for x in o:
                walk(x)
        elif isinstance(o, dict):
            types = o.get("@type") if isinstance(o.get("@type"), list) else [o.get("@type")]
            if "Review" in types:
                author = o.get("author")
                name = author.get("name") if isinstance(author, dict) else author
                rating = o.get("reviewRating") or {}
                out.append({"quote": o.get("reviewBody") or o.get("description") or "", "name": name,
                            "date": o.get("datePublished"), "platform": None,
                            "stars": rating.get("ratingValue") if isinstance(rating, dict) else None, "url": url,
                            "from": "jsonld"})
            if "AggregateRating" in types or isinstance(o.get("aggregateRating"), dict):
                agg = o if "AggregateRating" in types else o["aggregateRating"]
                ratings.append({"value": agg.get("ratingValue"), "count": agg.get("reviewCount") or agg.get("ratingCount"),
                                "best": agg.get("bestRating"), "url": url})
            for v in o.values():
                if isinstance(v, (list, dict)):
                    walk(v)
    walk(items)
    return [r for r in out if r["quote"]], ratings


# ── contact facts ──

def phones(text):
    """One entry per real phone number, however the site spelled it (pure)."""
    seen = {}
    for raw in dict.fromkeys(m.strip() for m in PHONE_RE.findall(text or "")):
        digits = re.sub(r"\D", "", raw)
        if not 10 <= len(digits) <= 13:
            continue
        key = digits if raw.startswith("+") and not raw.startswith("+1") else digits[-10:]
        seen.setdefault(key, raw)
    return list(seen.values())


def action_score(href, text=""):
    """2 for a known booking host, 1 when the words say book/quote/order, 0
    otherwise; a social profile is never the action (pure)."""
    if not href or re.match(r"(tel|mailto|sms|javascript):", href, re.I) or href.endswith("#"):
        return 0
    host = (urlsplit(href).hostname or "").lower()
    if not host or SOCIAL_HOST_RE.search(host):
        return 0
    if BOOKING_HOST_RE.search(host):
        return 2
    return 1 if ACTION_TEXT_RE.search(text or "") else 0


def own_page(url, template):
    """A page whose facts are the business's own: the home page, and its
    contact, about, team and location pages (not a post, not a product)."""
    path = urlsplit(url).path.strip("/")
    return not path or (bool(OWN_PAGE_RE.search(path)) and not (template or "").endswith("*"))


class Facts:
    """Facts gathered across the pages read, each value with its sources."""
    KINDS = ("phone", "email", "address", "hours", "social", "action")

    def __init__(self):
        self.values = {k: {} for k in self.KINDS}
        self.reviews, self.ratings = [], []

    def add(self, kind, value, url, where, key=None):
        value = re.sub(r"\s+", " ", str(value)).strip()
        if not value:
            return
        key = key or (re.sub(r"[^\w]+", " ", value.lower()).strip() if kind == "address" else value.lower())
        entry = self.values[kind].setdefault(key, {"value": value, "sources": []})
        src = {"url": url, "where": where}
        if src not in entry["sources"]:
            entry["sources"].append(src)

    def from_jsonld(self, business, url):
        """The business markup (structured.merge_business's shape)."""
        if not business:
            return
        for k, kind in (("telephone", "phone"), ("email", "email")):
            v = business.get(k)
            for x in v if isinstance(v, list) else [v]:
                if x:
                    self.add(kind, x, url, "jsonld", key=re.sub(r"\D", "", str(x))[-10:] if kind == "phone" else None)
        addr = business.get("address")
        for a in addr if isinstance(addr, list) else [addr]:
            if isinstance(a, dict):
                line = ", ".join(str(a[k]) for k in ("streetAddress", "addressLocality", "addressRegion", "postalCode") if a.get(k))
                self.add("address", line, url, "jsonld")
            elif a:
                self.add("address", a, url, "jsonld")
        hours = business.get("openingHours")
        for h in hours if isinstance(hours, list) else [hours]:
            if isinstance(h, str) and h:
                self.add("hours", h, url, "jsonld")
        same = business.get("sameAs")
        for s in same if isinstance(same, list) else [same]:
            if isinstance(s, str) and s:
                self.add("social", s, url, "jsonld")

    def from_page(self, url, template, blocks, links, review_quotes):
        """Contact facts in the site's header and footer (any page) and in the
        text of its own pages; review quotes are never read for facts."""
        own = own_page(url, template)
        for b in blocks:
            if b["tag"] == "img" or not b["text"]:
                continue
            if b["text"] in review_quotes or any(q and q in b["text"] for q in review_quotes):
                continue
            if not (b.get("chrome") or own):
                continue
            where = "header/footer" if b.get("chrome") else "page"
            text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", b["text"])
            for p in phones(text):
                self.add("phone", p, url, where, key=re.sub(r"\D", "", p)[-10:])
            for e in dict.fromkeys(EMAIL_RE.findall(text)):
                if not re.search(r"\.(png|jpe?g|gif|webp|svg)$", e, re.I):
                    self.add("email", e, url, where)
            for a in dict.fromkeys(ADDRESS_RE.findall(text)):
                self.add("address", a, url, where)
            for h in dict.fromkeys(m.group(0) for m in HOURS_RE.finditer(text)):
                self.add("hours", h, url, where)
        for l in links:
            href = l.get("href") or ""
            host = (urlsplit(href).hostname or "").lower()
            if l.get("landmark") or own:
                if href.startswith("tel:"):
                    self.add("phone", href[4:], url, "link", key=re.sub(r"\D", "", href)[-10:])
                elif href.startswith("mailto:"):
                    self.add("email", href[7:].split("?")[0], url, "link")
                elif SOCIAL_HOST_RE.search(host):
                    self.add("social", href.split("?")[0].rstrip("/"), url, "link")
            score = action_score(href, l.get("text"))
            if score:
                self.add("action", href, url, "link" if score == 1 else "booking host",
                         key=href.split("#")[0].rstrip("/").lower())
                self.values["action"][href.split("#")[0].rstrip("/").lower()].setdefault("text", l.get("text"))
                self.values["action"][href.split("#")[0].rstrip("/").lower()]["score"] = score

    def to_json(self):
        out = {}
        for kind, vals in self.values.items():
            rows = sorted(vals.values(), key=lambda e: (-e.get("score", 0), -len(e["sources"]), e["value"]))
            out[kind] = rows
        # more than one value: often right (a main line and a mobile), sometimes stale; the owner says which
        out["several"] = [k for k in ("phone", "email", "address") if len(self.values[k]) > 1]
        return out
