"""Every image the site uses: one picture however many sizes the site serves
it in, fetched once at its largest, named so a person can read the folder,
and recorded with every page it appears on and what it sat beside.

The page files link each photograph where the page showed it
(`![alt](../images/<name>)`); `_index/media.json` is the index: per picture,
its file, where it came from, its real size, what it probably is, and each
page it appears on with the heading above it and the words beside it.
"""
import hashlib
import re
import struct
from urllib.parse import parse_qs, unquote, urlsplit, urlunsplit

STOCK_HOSTS = ("unsplash.com", "pexels.com", "shutterstock.com", "istockphoto.com", "gettyimages", "pixabay.com",
               "adobestock", "freepik.com", "dreamstime.com", "depositphotos")
ICON_RE = re.compile(r"icon|sprite|arrow|chevron|bullet|check|star|social|favicon", re.I)
THEME_RE = re.compile(r"/themes?/|/theme-assets/|/assets/(img|images)/(bg|pattern|texture|placeholder)|placeholder|pattern|texture|/plugins/", re.I)
# Words that name a badge rather than a photograph, as whole words of a file
# name or alt text ("seal" is a badge, "sealcoating" is not). A partner's or
# client's file named "logo" is caught by the logo rule and made a mark.
SOCIAL_RE = re.compile(r"facebook|instagram|twitter|linkedin|youtube|tiktok|pinterest|whatsapp|(?<![a-z])x-logo", re.I)
AVATAR_RE = re.compile(r"avatar|profile|gravatar|headshot|user-?photo|reviewer", re.I)
MARK_DIR_RE = re.compile(r"/(partners?|clients?|logos?|sponsors?|memberships?|certifications?|accreditations?|"
                         r"affiliations?|associations?)/", re.I)
MARK_RE = re.compile(r"(?<![a-z])(badges?|seals?|bbb|accredit\w*|certified|certification|sponsors?|as-seen-on)(?![a-z])", re.I)
MARKS_KEPT = 40
VIDEOS_KEPT = 3
STOCK_NAME_RE = re.compile(r"shutterstock|istock|adobestock|gettyimages|depositphotos|stock-photo|pexels|unsplash", re.I)
WP_SIZE_RE = re.compile(r"-(\d{2,5})x(\d{2,5})(?=\.[a-z0-9]{2,5}$)|-scaled(?=\.[a-z0-9]{2,5}$)|@\dx(?=\.[a-z0-9]{2,5}$)", re.I)
SHOPIFY_SIZE_RE = re.compile(r"_(?:\d{2,5}x\d{0,5}|x\d{2,5}|small|medium|large|grande|compact|thumb|icon|master)(?:@\dx)?(?=\.[a-z0-9]{2,5}$)", re.I)
CLOUDINARY_RE = re.compile(r"^(/[^/]+/(?:image|video)/upload)/((?:[^/]*[,_][^/]*/)*)(v\d+/)?(.+\.(?:jpe?g|png|webp|gif|avif))$", re.I)
SIZE_PARAMS = ("w", "h", "width", "height", "fit", "crop", "resize", "quality", "q", "format", "fm", "auto", "dpr", "s", "sz", "size")
MODES = ("none", "brand", "content", "all")
BRAND_PHOTOS = 60


def guess_kind(img):
    src = (img.get("src") or "").lower()
    alt = (img.get("alt") or "").lower()
    host = (urlsplit(src).hostname or "").lower()
    path = urlsplit(src).path               # the file, without host or query string
    name = path.rsplit("/", 1)[-1]
    w, h = img.get("width"), img.get("height")
    jpeg = path.endswith((".jpg", ".jpeg"))
    if "logo" in name or "logo" in alt:
        return "logo"
    if w and h and w <= 64 and h <= 64:
        return "icon"
    if MARK_RE.search(name) or MARK_RE.search(alt) or MARK_DIR_RE.search(path):
        return "mark"
    # one of a row of small pictures (a logo strip or carousel), in any format;
    # a row of social icons or reviewers' avatars is not
    if (img.get("row") or 0) >= 3 and not SOCIAL_RE.search(name + " " + alt) and not AVATAR_RE.search(name + " " + alt):
        return "mark"
    # a logo strip or carousel: short, wide, small, not a photograph's format
    if w and h and h <= 200 and 2 * h <= w <= 1000 and not jpeg:
        return "mark"
    # a picture file shown far smaller than it is, with no words: a badge
    shown = img.get("shown")
    if shown and shown <= 260 and not alt and not jpeg and ((w and w > shown) or path.endswith(".svg")):
        return "mark"
    # a small wordless picture in the header or footer, not a photograph: a badge
    if img.get("landmark") in ("header", "footer") and w and h and max(w, h) <= 400 and not alt and not jpeg:
        return "mark"
    if path.endswith(".svg") or ICON_RE.search(name):
        return "icon"
    if any(s in host for s in STOCK_HOSTS) or STOCK_NAME_RE.search(src):
        return "stock"
    if THEME_RE.search(src) or (img.get("landmark") in ("header", "footer") and not alt):
        return "theme"
    if src.endswith((".jpg", ".jpeg", ".webp", ".avif", ".heic")) or "upload" in src or "photo" in src or "gallery" in src:
        return "photo"
    return "photo" if alt else "theme"


def variant_of(url):
    """(key, original) for an image URL (pure): the key is the picture's
    identity across every size the site serves it in; the original is the
    URL most likely to be its full size. WordPress -1024x683 and -scaled,
    Shopify _800x, Wix /v1/fill/..., Cloudinary transforms, and resizing
    query parameters (?w=800, Next.js /_next/image?url=...) all collapse."""
    parts = urlsplit(url)
    if parts.path.endswith("/_next/image") or parts.path.endswith("/_vercel/image"):
        inner = parse_qs(parts.query).get("url", [None])[0]
        if inner:
            if inner.startswith("/"):
                inner = urlunsplit((parts.scheme, parts.netloc, inner, "", ""))
            return variant_of(inner)
    host, path = (parts.hostname or "").lower(), parts.path
    m = CLOUDINARY_RE.match(path) if host.endswith("cloudinary.com") else None
    if m:
        return (host + m.group(1) + "/" + m.group(4)).lower(), \
            urlunsplit((parts.scheme, parts.netloc, m.group(1) + "/" + m.group(4), "", ""))
    wix = re.match(r"^(.*?\.(?:jpe?g|png|webp|gif))/v1/", path, re.I)
    if wix:
        return (host + wix.group(1)).lower(), urlunsplit((parts.scheme, parts.netloc, wix.group(1), "", ""))
    clean = SHOPIFY_SIZE_RE.sub("", WP_SIZE_RE.sub("", path))
    query = "&".join("%s=%s" % (k, v) for k, vs in parse_qs(parts.query, keep_blank_values=True).items() for v in vs
                     if k.lower() not in SIZE_PARAMS)
    return (host + clean + ("?" + query if query else "")).lower(), urlunsplit((parts.scheme, parts.netloc, clean, query, ""))


def widest(srcset):
    """The widest candidate of a srcset given as [(url, width)] (pure)."""
    best = max(srcset or [], key=lambda c: c[1] or 0, default=None)
    return best[0] if best else None


def dimensions(data):
    """(width, height) of PNG, GIF, JPEG or WebP bytes, or None (pure)."""
    try:
        if data[:8] == b"\x89PNG\r\n\x1a\n":
            return struct.unpack(">II", data[16:24])
        if data[:6] in (b"GIF87a", b"GIF89a"):
            return struct.unpack("<HH", data[6:10])
        if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
            chunk = data[12:16]
            if chunk == b"VP8 ":
                w, h = struct.unpack("<HH", data[26:30])
                return w & 0x3FFF, h & 0x3FFF
            if chunk == b"VP8L":
                b = data[21:25]
                return 1 + (((b[1] & 0x3F) << 8) | b[0]), 1 + (((b[3] & 0xF) << 10) | (b[2] << 2) | ((b[1] & 0xC0) >> 6))
            if chunk == b"VP8X":
                return 1 + int.from_bytes(data[24:27], "little"), 1 + int.from_bytes(data[27:30], "little")
        if data[:2] == b"\xff\xd8":
            i = 2
            while i + 9 < len(data):
                if data[i] != 0xFF:
                    i += 1
                    continue
                marker = data[i + 1]
                if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                    h, w = struct.unpack(">HH", data[i + 5:i + 9])
                    return w, h
                i += 2 + struct.unpack(">H", data[i + 2:i + 4])[0]
    except (struct.error, IndexError):
        return None
    return None


IMAGE_TYPES = {"image/jpeg": "jpg", "image/jpg": "jpg", "image/png": "png", "image/webp": "webp", "image/gif": "gif",
               "image/avif": "avif", "image/svg+xml": "svg", "image/x-icon": "ico", "image/vnd.microsoft.icon": "ico",
               "video/mp4": "mp4", "video/webm": "webm", "video/quicktime": "mov"}


def extension(ctype, url):
    """A file's extension from what the server said it is, else its URL's (pure)."""
    known = IMAGE_TYPES.get((ctype or "").split(";")[0].strip().lower())
    if known:
        return known
    ext = urlsplit(url).path.rsplit(".", 1)[-1].lower() if "." in urlsplit(url).path.rsplit("/", 1)[-1] else ""
    return ext if re.fullmatch(r"[a-z0-9]{2,5}", ext) else "img"


def file_name(key, original, ext):
    """A readable, stable name (pure): the picture's own file name, slugged,
    and a short hash of its identity so two pictures called image.jpg stay two."""
    stem = unquote(urlsplit(original).path.rsplit("/", 1)[-1]).rsplit(".", 1)[0]
    stem = re.sub(r"[^a-z0-9]+", "-", stem.lower()).strip("-")[:40] or "image"
    return "%s-%s.%s" % (stem, hashlib.sha1(key.encode()).hexdigest()[:8], ext)


class Media:
    """Every picture a crawl saw, keyed by identity, with its occurrences."""

    def __init__(self):
        self.items = {}
        self.known = {}      # key -> {file, width, height, bytes} from an earlier run

    def key_of(self, src):
        return variant_of(src)[0]

    def _item(self, src, srcset=(), alt=None, chrome=True, background=False, og=False):
        key, original = variant_of(src)
        it = self.items.get(key)
        if it is None:
            it = self.items[key] = {"key": key, "original": original, "seen": [], "srcset": [], "file": None,
                                    "width": None, "height": None, "bytes": None, "alts": [], "pages": [],
                                    "chrome": True, "background": False, "og": False, "kind": None, "shown": None, "row": None}
        if src not in it["seen"]:
            it["seen"].append(src)
        it["srcset"].extend(list(c) for c in srcset if list(c) not in it["srcset"])
        if alt and alt not in it["alts"]:
            it["alts"].append(alt)
        it["chrome"] = it["chrome"] and chrome
        it["background"] = it["background"] or background
        it["og"] = it["og"] or og
        return it

    def add_page(self, url, blocks, images, og_image=None):
        """One page: its content images in place (with the heading above and
        the words beside each), every other image it carries, its og:image."""
        heading = None
        for i, b in enumerate(blocks):
            if b["tag"] in ("h1", "h2", "h3", "h4", "h5", "h6"):
                heading = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", b["text"])
            if b["tag"] != "img":
                continue
            beside = next((x["text"] for x in blocks[i + 1:i + 3] if x["tag"] != "img" and x["text"]), "")
            it = self._item(b["src"], b.get("srcset") or [], b.get("alt"), bool(b.get("chrome")), bool(b.get("background")))
            if b.get("shown"):
                it["shown"] = max(it["shown"] or 0, b["shown"])
            if b.get("row"):
                it["row"] = max(it["row"] or 0, b["row"])
            if not any(p["url"] == url for p in it["pages"]):
                it["pages"].append({"url": url, "heading": heading, "beside": beside[:200]})
        for img in images:
            src = img.get("src") or (img.get("srcset") or [None])[0]
            if not src or src.startswith("data:"):
                continue
            it = self._item(src, img.get("srcset_w") or [], img.get("alt"), bool(img.get("landmark")))
            if it["width"] is None and img.get("width"):
                it["width"], it["height"] = img.get("width"), img.get("height")
            if img.get("row"):
                it["row"] = max(it["row"] or 0, img["row"])
            if img.get("video"):
                it["video"] = True
                it["autoplay"] = it.get("autoplay") or bool(img.get("autoplay"))
            if not any(p["url"] == url for p in it["pages"]):
                it["pages"].append({"url": url, "heading": None, "beside": ""})
        if og_image and not og_image.startswith("data:"):
            self._item(og_image, og=True)

    def classify(self, logo_src=None, site_name=""):
        """What each picture probably is. A logo is the site's own (the one its
        header shows, or a file named for the site); anyone else's logo (a
        partner, an association, a payment card) is a mark."""
        logo_key = variant_of(logo_src)[0] if logo_src else None
        own = re.sub(r"[^a-z0-9]", "", (site_name or "").lower())
        for it in self.items.values():
            kind = guess_kind({"src": it["original"], "alt": (it["alts"] or [""])[0], "width": it["width"],
                               "height": it["height"], "landmark": "header" if it["chrome"] else None,
                               "shown": it.get("shown"), "row": it.get("row")})
            if it["key"] == logo_key:
                kind = "logo"
            elif kind == "logo" and not (len(own) >= 3 and own in re.sub(r"[^a-z0-9]", "", urlsplit(it["original"]).path.lower())):
                kind = "mark"
            it["kind"] = "video" if it.get("video") else kind

    def select(self, mode):
        """The pictures to fetch, best first. none: nothing; brand: the logo
        candidates, the og:image, and the photographs the most pages show,
        largest first, up to BRAND_PHOTOS; content: the logo and every
        picture in the pages' own content; all: everything seen. Brand keeps
        others' logos (marks) and its videos too: proof and hero material."""
        items = list(self.items.values())
        logos = [i for i in items if i["kind"] == "logo"]
        marks = [i for i in items if i["kind"] == "mark"][:MARKS_KEPT]
        videos = [i for i in items if i["kind"] == "video"][:VIDEOS_KEPT]
        if mode == "none":
            chosen = []
        elif mode == "all":
            chosen = items
        elif mode == "brand":
            photos = sorted((i for i in items if i["kind"] == "photo"),
                            key=lambda i: (-len(i["pages"]), -((i["width"] or 0) * (i["height"] or 0))))
            chosen = logos + marks + videos + [i for i in items if i["og"]] + photos[:BRAND_PHOTOS]
        else:
            chosen = logos + [i for i in items if not i["chrome"]]
        return list({i["key"]: i for i in chosen}.values())

    @staticmethod
    def candidates(it):
        """The URLs to try for a picture's largest version, best first."""
        return [u for u in dict.fromkeys([it["original"], widest(it["srcset"])] + list(reversed(it["seen"]))) if u]

    def to_json(self):
        rows = sorted(self.items.values(), key=lambda i: (i["file"] is None, -len(i["pages"]), i["key"]))
        return [{k: v for k, v in r.items() if k != "srcset"} for r in rows]

    def load(self, rows):
        """The files an earlier run fetched, so a re-crawl reuses them."""
        for r in rows or []:
            if r.get("key") and r.get("file"):
                self.known[r["key"]] = {k: r.get(k) for k in ("file", "width", "height", "bytes")}
