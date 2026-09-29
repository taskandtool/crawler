"""Templates, and the order pages are read in.

A template is a kind of page the site has many of: its posts, its products,
its locations. A page the site has one of (about, contact, a service) is its
own template. The crawl tells them apart from what it can see before
fetching anything, so it can sample a big site instead of exhausting it:

- the sitemap a URL was listed in: WordPress, Yoast, Shopify and most
  builders split theirs by kind (post-sitemap.xml, sitemap_products_1.xml);
- the URL's shape: many leaf pages under one parent (/blog/<slug>,
  /products/<slug>), with numbers and dates read as one pattern;
- the site's own navigation: a page the header or nav links to is its own
  page, whatever its URL looks like.

A fingerprint of each fetched page's shape (blocks.fingerprint) is recorded
beside its template, so a flat site whose posts and pages share one URL
pattern shows up as one template with two shapes.
"""
import re
from collections import OrderedDict
from urllib.parse import urlsplit

# A parent with at least this many leaf children is a collection.
MIN_COLLECTION = 5
NUMERIC_SEG_RE = re.compile(r"^(\d+|\d{4}-\d{2}(-\d{2})?)$")
# Sitemap names that say nothing about the kind of page in them.
GENERIC_SITEMAP = {"", "sitemap", "index", "main", "pages", "page", "misc", "home", "sitemaps", "wp", "core"}
# Top-level sections whose pages are posts: capped like a template, not a section.
POST_SECTIONS_RE = re.compile(r"^(blog|news|posts?|articles?|press|stories|insights|events?|updates|journal|resources)$", re.I)


PAGE_SITEMAPS = {"page", "pages"}


def sitemap_words(sitemap_url):
    """A sitemap file name as its words (pure): post-sitemap2.xml -> post."""
    name = urlsplit(sitemap_url or "").path.rsplit("/", 1)[-1].lower()
    name = re.sub(r"\.xml(\.gz)?$", "", name)
    if name.startswith("wp-sitemap-"):                 # WordPress core: wp-sitemap-posts-<type>-<n>
        parts = name.split("-")
        name = parts[3] if len(parts) > 3 and parts[2] in ("posts", "taxonomies", "users") else ""
    words = [re.sub(r"\d+$", "", w) for w in re.split(r"[-_.]+", name)]
    return "-".join(w for w in words if w and w != "sitemap")


def sitemap_label(sitemap_url):
    """The kind of page a sitemap lists, from its file name (pure):
    post-sitemap2.xml -> post, sitemap_products_1.xml.gz -> products,
    wp-sitemap-posts-post-1.xml -> post; None when the name says nothing
    (a page sitemap lists pages, each its own template)."""
    label = sitemap_words(sitemap_url)
    return None if label in GENERIC_SITEMAP else label


def segments(url):
    return [s for s in urlsplit(url).path.split("/") if s]


def section(url):
    """The top-level path segment ("" for the home page)."""
    segs = segments(url)
    return segs[0].lower() if segs else ""


class Templates:
    """What every known URL's template is, from the URLs known so far."""

    def __init__(self):
        self.children = {}            # parent path -> set of child segments
        self.labels = {}              # url -> sitemap label
        self.nav = set()              # urls the site's header/nav links to
        self.own = set()              # urls a page sitemap lists: pages, each its own

    def add(self, url, sitemap=None):
        segs = segments(url)
        if segs:
            # parents are read as patterns (/2024/05 is /{n}/{n}); children as spelled
            parent = "/".join(self._norm(s) for s in segs[:-1])
            self.children.setdefault(parent, set()).add(segs[-1].lower())
        if sitemap and sitemap_words(sitemap) in PAGE_SITEMAPS:
            self.own.add(url)
        label = sitemap_label(sitemap) if sitemap else None
        if label:
            self.labels[url] = label

    def mark_nav(self, urls):
        self.nav.update(urls)

    @staticmethod
    def _norm(seg):
        return "{n}" if NUMERIC_SEG_RE.match(seg) else seg.lower()

    def of(self, url):
        raw = segments(url)
        segs = [self._norm(s) for s in raw]
        if not segs:
            return "/"
        # A listing's later pages (/blog/page/3, /tag/x/page/2) are one
        # template with the listing they page through.
        if len(segs) >= 2 and segs[-1] == "{n}" and segs[-2] in ("page", "p"):
            base = "/".join(raw[:-2])
            head = self.of(urlsplit(url)._replace(path="/" + base, query="").geturl()) if base else ""
            return (head if head != "/" else "") + "/" + segs[-2] + "/{n}"
        own = "/" + "/".join(segs)
        if url in self.nav or url in self.own:
            return own
        label = self.labels.get(url)
        if label:
            return label
        parent = "/".join(segs[:-1])
        numbered = any(s == "{n}" for s in segs)
        if len(self.children.get(parent, ())) >= MIN_COLLECTION or numbered:
            return "/" + (parent + "/" if parent else "") + "*"
        return own


def is_collection(template):
    """A template of many pages (a sitemap kind, a /parent/* pattern, a
    numbered one), not one page's own path (pure)."""
    return not template.startswith("/") or "*" in template or "{n}" in template


class Frontier:
    """The URLs waiting to be read, best first. Tiers, lowest first, first
    come first served within one: the start page; what the site's header and
    nav link to; what the home page's own content links to; what its footer
    links to; the sitemap; everything else found along the way. A URL found
    again through a better door moves up."""
    START, NAV, HOME_BODY, FOOTER, SITEMAP, FOUND = range(6)

    def __init__(self):
        self.tiers = [OrderedDict() for _ in range(6)]
        self.where = {}

    def add(self, url, tier):
        at = self.where.get(url)
        if at is not None and at <= tier:
            return False
        if at is not None:
            del self.tiers[at][url]
        self.tiers[tier][url] = True
        self.where[url] = tier
        return at is None

    def pop(self):
        for tier in self.tiers:
            if tier:
                url, _ = tier.popitem(last=False)
                del self.where[url]
                return url
        return None

    def __len__(self):
        return len(self.where)

    def __iter__(self):
        for tier in self.tiers:
            yield from tier


class Sampler:
    """Per-template and per-section caps: a site of 3,000 posts needs two of
    them read to know what a post is, not a budget's worth. Nav pages skip
    the section cap (a site's own pages are why it was crawled) but not the
    template one, so a mega-menu listing every product model is still one
    template. A post section (blog, news, ...) is capped like a template."""

    def __init__(self, per_template=None, per_section=None):
        self.per_template, self.per_section = per_template, per_section
        self.by_template, self.by_section = {}, {}

    def admit(self, url, template, nav=False):
        sec = section(url)
        sec_cap = self.per_section
        if sec_cap is not None and self.per_template is not None and POST_SECTIONS_RE.match(sec or "-"):
            sec_cap = self.per_template
        if self.per_template is not None and is_collection(template) \
                and self.by_template.get(template, 0) >= self.per_template:
            return False
        if sec_cap is not None and not nav and sec and self.by_section.get(sec, 0) >= sec_cap:
            return False
        self.by_template[template] = self.by_template.get(template, 0) + 1
        self.by_section[sec] = self.by_section.get(sec, 0) + 1
        return True

    def refund(self, url, template):
        """Give back an admitted page's place: it redirected, and the page it
        points to is read in its stead."""
        self.by_template[template] = max(0, self.by_template.get(template, 0) - 1)
        sec = section(url)
        self.by_section[sec] = max(0, self.by_section.get(sec, 0) - 1)
