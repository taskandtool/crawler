"""One pass over a page's HTML that collects everything the other modules
need: head metadata, headings, links with the landmark they sit in, images,
forms, embeds, scripts, JSON-LD and microdata, the text lines of the
header, nav, footer, and aside landmarks, and the page's content as blocks
in document order (blocks.py writes them as markdown). Standard-library
HTMLParser only.

The same parser reads a static fetch and a browser render: the render
annotates what only a browser knows (an image's real size and the source it
chose, a large CSS background) as data-tt-* attributes, so both paths give
the same blocks.
"""
import html as htmlmod
import re
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

from .net import same_site

LANDMARK_TAGS = {"header": "header", "nav": "nav", "footer": "footer", "aside": "aside"}
LANDMARK_ROLES = {"banner": "header", "navigation": "nav", "contentinfo": "footer", "complementary": "aside"}
# div/section ids or class tokens that mean a landmark on sites that use divs
# for them. Deliberately specific: a bare "header" class is as often a
# section's own heading block as the site's header.
LANDMARK_HINTS = {
    "header": {"site-header", "page-header", "masthead", "topbar", "top-bar", "global-header"},
    "nav": {"navbar", "main-nav", "primary-nav", "site-nav", "main-menu", "primary-menu", "main-navigation"},
    "footer": {"site-footer", "page-footer", "colophon", "global-footer"},
}
LANDMARK_IDS = {"header": "header", "site-header": "header", "masthead": "header", "nav": "nav", "navigation": "nav",
                "menu": "nav", "footer": "footer", "site-footer": "footer", "colophon": "footer"}
HEADING_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6"}
# Elements whose text is one block of content. A block inside another (a <p>
# inside an <li>) reports on its own; the outer keeps only its own text.
BLOCK_TAGS = HEADING_TAGS | {"p", "li", "blockquote", "td", "th", "dt", "dd", "figcaption", "pre"}
# Elements that end a run of loose text: page builders put copy straight into
# <div>s and <span>s, and a run between two of these is a paragraph.
BREAK_TAGS = {"div", "section", "article", "main", "header", "footer", "nav", "aside", "form", "table", "tr",
              "ul", "ol", "dl", "figure", "br", "hr", "body", "button", "label", "select", "option", "iframe"}
SKIP_TEXT_TAGS = {"script", "style", "noscript", "template", "svg", "head", "title", "button", "select", "option"}
IMAGE_LINK_RE = re.compile(r"\.(jpe?g|png|webp|gif|avif)$", re.I)
VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}
ICON_SRC_RE = re.compile(r"\.svg(\?|$)|icon|sprite|logo|favicon|spinner|loader|pixel|spacer|blank\.gif", re.I)
MIN_BLOCK_IMAGE_PX = 400
SOCIAL_HOSTS = ("facebook.com", "instagram.com", "linkedin.com", "twitter.com", "x.com", "youtube.com",
                "tiktok.com", "pinterest.com", "threads.net", "yelp.com", "nextdoor.com", "g.page",
                "maps.google.com", "goo.gl", "tripadvisor.com", "houzz.com", "angi.com", "bbb.org")
LEGAL_RE = re.compile(r"privacy|terms|conditions|cookie|legal|refund|returns?\b|accessibility|disclaimer|gdpr|imprint|impressum", re.I)
CTA_RE = re.compile(r"\b(call|book|quote|estimate|contact|get started|schedule|appointment|order|buy|apply|sign up|enquire|inquire|request)\b", re.I)
BADGE_RE = re.compile(r"badge|award|accredit|certif|partner|bbb|rating|guarantee|seal|trust|payment|visa|mastercard|licen[cs]ed|insured|member", re.I)
COPYRIGHT_RE = re.compile(r"©|\(c\)|copyright|all rights reserved", re.I)


class PageParser(HTMLParser):
    def __init__(self, base_url):
        super().__init__(convert_charrefs=True)
        self.base = base_url
        self.stack = []            # (tag, landmark_or_None)
        self.landmark_stack = []   # active landmark names, innermost last
        self.list_depth = 0        # ul/ol nesting inside the current landmark
        self.title = ""
        self.lang = ""
        self.meta = {}             # name/property -> content (last wins)
        self.link_rels = []        # (rel, href, hreflang)
        self.headings = []         # (tag, text)
        self.links = []            # dicts: href, text, landmark, depth, group, classes
        self.images = []           # dicts: src, srcset, alt, width, height, landmark, in_link, classes
        self.forms = []            # dicts: action, method, fields, landmark
        self.iframes = []          # src
        self.scripts = []          # src
        self.script_text = []      # inline script bodies (capped)
        self.jsonld = []           # raw text of application/ld+json blocks
        self.microdata = []        # dicts: type, props
        self.landmark_lines = {}   # landmark -> [text lines]
        self.body_text = []        # text outside landmarks (for word count)
        self._text_target = None   # "title" | "heading" | "script" | "jsonld" | None
        self._heading = None
        self._link_text = None     # list while inside <a>
        self._current_form = None
        self._current_group = None # last heading text inside the current landmark
        self._item_stack = []      # microdata items
        self._itemprop = None      # (name, chars) while collecting itemprop text
        self._in_noscript = 0
        self._main_depth = 0       # inside <main>, <article>, or role=main
        # content blocks, in document order: dicts tag, text, chrome, [href], [src, alt]
        self.blocks = []
        self._open_blocks = []     # [index into self.blocks, pieces] for each open block element
        self._loose = None         # [index, pieces] for a run of text outside any block
        self._skip_depth = 0       # inside script/style/svg/hidden: no block text
        self._link_marks = []      # (block index, piece count, href) per open <a>

    # ── helpers ──
    def _classes(self, a):
        return set((a.get("class") or "").lower().split()) | ({a.get("id", "").lower()} if a.get("id") else set())

    def _landmark_for(self, tag, a):
        if tag in ("header", "footer") and self._main_depth and not a.get("role"):
            return None            # an article's own header (headline, byline, date), not the site's
        if tag in LANDMARK_TAGS:
            return LANDMARK_TAGS[tag]
        role = (a.get("role") or "").lower()
        if role in LANDMARK_ROLES:
            return LANDMARK_ROLES[role]
        if tag in ("div", "section"):
            ident = (a.get("id") or "").lower()
            if ident in LANDMARK_IDS:
                return LANDMARK_IDS[ident]
            cls = set((a.get("class") or "").lower().split())
            for name, hints in LANDMARK_HINTS.items():
                if cls & hints:
                    return name
        return None

    @property
    def landmark(self):
        return self.landmark_stack[-1] if self.landmark_stack else None

    def _srcset(self, srcset):
        """[(url, width)] from a srcset; a density (2x) counts as width 0."""
        out = []
        for part in (srcset or "").split(","):
            bits = part.strip().split()
            if bits and not bits[0].startswith("data:"):
                w = _int(bits[1][:-1]) if len(bits) > 1 and bits[1].endswith("w") else 0
                out.append((self._abs(bits[0]), w or 0))
        return out

    def _abs(self, href):
        return urljoin(self.base, htmlmod.unescape(href).strip())

    # ── blocks ──
    def _chrome(self):
        return self.landmark is not None

    def _new_block(self, tag, **extra):
        self.blocks.append(dict(tag=tag, text="", chrome=self._chrome(), **extra))
        return len(self.blocks) - 1

    def _end_loose(self):
        if self._loose is not None:
            self._finish(self._loose)
            self._loose = None

    def _finish(self, entry):
        index, pieces = entry
        self.blocks[index]["text"] = _squash_lines("".join(pieces))

    def _text_sink(self):
        """The pieces list new text goes into: the innermost open block, else a
        run of loose text (opened on demand)."""
        if self._open_blocks:
            return self._open_blocks[-1]
        if self._loose is None:
            self._loose = [self._new_block("p"), []]
        return self._loose

    def _block_start(self, tag, a):
        if self._skip_depth:
            return
        if tag in BREAK_TAGS or tag in BLOCK_TAGS:
            self._end_loose()
        if tag in BLOCK_TAGS:
            self._open_blocks.append([self._new_block(tag), []])
        elif tag == "br" and self._open_blocks:
            self._open_blocks[-1][1].append("\n")
        elif tag == "img":
            self._end_loose()
            src = a.get("data-tt-src") or a.get("src") or a.get("data-src") or a.get("data-lazy-src") or ""
            width = _int(a.get("data-tt-w")) or _int(a.get("width"))
            if src and not src.startswith("data:") and not ICON_SRC_RE.search(src) \
                    and not (width and width < MIN_BLOCK_IMAGE_PX):
                self._new_block("img", src=self._abs(src), alt=htmlmod.unescape(a.get("alt") or "").strip(),
                                srcset=self._srcset(a.get("srcset") or a.get("data-srcset") or ""),
                                shown=_int(a.get("data-tt-sw")), row=_int(a.get("data-tt-row")))
        if a.get("data-tt-bg"):
            self._end_loose()
            self._new_block("img", src=a["data-tt-bg"], alt="", background=True)
        if tag == "a" and a.get("href"):
            sink = self._text_sink()
            if sink[1] and sink[1][-1].startswith("[") and sink[1][-1].endswith(")"):
                sink[1].append(" ")            # two links side by side stay two
            self._link_marks.append((sink[0], len(sink[1]), self._abs(a["href"]), len(self.blocks)))
        elif tag == "a":
            self._link_marks.append(None)

    def _block_end(self, tag):
        if self._skip_depth:
            return
        if tag == "a" and self._link_marks:
            mark = self._link_marks.pop()
            if mark:
                index, at, href, first_new = mark
                sink = self._open_blocks[-1] if self._open_blocks else self._loose
                if sink is not None and sink[0] == index:
                    text = _squash("".join(sink[1][at:]))
                    if text and href.startswith(("http://", "https://", "mailto:", "tel:")):
                        sink[1][at:] = ["[%s](%s)" % (text.replace("]", ")"), href)]
                else:
                    # a link around whole blocks (a card): its headings carry the target
                    for b in self.blocks[first_new:]:
                        if b["tag"] in HEADING_TAGS and "href" not in b and href.startswith(("http://", "https://")):
                            b["href"] = href
        if tag in BLOCK_TAGS and self._open_blocks and self.blocks[self._open_blocks[-1][0]]["tag"] == tag:
            self._finish(self._open_blocks.pop())
        elif tag in BREAK_TAGS:
            self._end_loose()

    def _block_text(self, data):
        if self._skip_depth or self._text_target in ("script", "jsonld", "title"):
            return
        if not self._open_blocks and self._loose is None and not data.strip():
            return
        sink = self._text_sink()
        # two elements that meet with no space ("thinking.”</span><span>by"):
        # after closing punctuation, a word starts a new phrase
        if sink[1] and data[:1].isalnum() and sink[1][-1][-1:] in '.!?”"):':
            sink[1].append(" ")
        sink[1].append(data)

    def blocks_out(self):
        """The finished blocks: empty ones dropped, and a repeat of the same
        block dropped too (a carousel shows its slides twice), except a short
        line with a figure in it, which belongs to the item beside it (two news
        items can share "Published on 12 May 2026")."""
        for entry in self._open_blocks:
            self._finish(entry)
        self._end_loose()
        out, seen = [], set()
        here = urlsplit(self.base)._replace(fragment="").geturl()
        for b in self.blocks:
            # a skip link ("Skip to content" -> #content) is the page's plumbing
            m = re.fullmatch(r"\[[^\]]*\]\(([^)]*)#[^)]*\)", b.get("text") or "")
            if m and (not m.group(1) or m.group(1) == here):
                continue
            if b["tag"] == "img":
                key = "img|" + b["src"]
            else:
                if not b["text"]:
                    continue
                key = b["tag"] + "|" + b["text"]
                if key in seen and re.search(r"\d", b["text"]) and len(b["text"]) < 60:
                    out.append(b)
                    continue
            if key in seen:
                continue
            seen.add(key)
            out.append(b)
        return out

    # ── parsing ──
    def handle_startendtag(self, tag, attrs):
        # <br/>, <img/>: a void element opens and closes at once
        self.handle_starttag(tag, attrs)
        if tag not in VOID_TAGS:
            self.handle_endtag(tag)

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        void = tag in VOID_TAGS
        hides = not void and (tag in SKIP_TEXT_TAGS or "hidden" in a or (a.get("aria-hidden") or "").lower() == "true")
        mains = not void and (tag in ("main", "article") or (a.get("role") or "").lower() == "main")
        lm = self._landmark_for(tag, a)
        if not void:
            self.stack.append((tag, lm, hides, mains))
        if hides:
            self._skip_depth += 1
        if mains:
            self._main_depth += 1
        if lm and not void:
            self.landmark_stack.append(lm)
            self.landmark_lines.setdefault(lm, [])
            self.list_depth = 0
            self._current_group = None
        self._block_start(tag, a)
        if tag == "noscript":
            self._in_noscript += 1
        if tag == "html" and a.get("lang"):
            self.lang = a["lang"]
        elif tag == "title":
            self._text_target = "title"
        elif tag == "meta":
            key = (a.get("name") or a.get("property") or a.get("http-equiv") or "").lower()
            if key and a.get("content") is not None:
                self.meta[key] = a["content"]
        elif tag == "link" and a.get("href"):
            self.link_rels.append(((a.get("rel") or "").lower(), self._abs(a["href"]), a.get("hreflang")))
        elif tag in HEADING_TAGS:
            self._heading = (tag, [])
            self._text_target = "heading"
        elif tag == "a":
            self._link_text = []
            self._link_attrs = a
            # A lightbox gallery (Elementor, WordPress, most plugins) links each
            # full-size photograph and draws its thumbnail with script: the
            # link is the page's picture, though no <img> carries it.
            href = a.get("href") or ""
            if IMAGE_LINK_RE.search(href.split("?")[0]):
                self.images.append({
                    "src": self._abs(href), "srcset": [], "srcset_w": [],
                    "alt": htmlmod.unescape(a.get("aria-label") or a.get("title") or a.get("data-elementor-lightbox-title") or "").strip(),
                    "width": None, "height": None, "landmark": self.landmark, "in_link": True,
                    "link_href": self._abs(href), "classes": sorted(self._classes(a)),
                })
        elif tag in ("ul", "ol") and self.landmark:
            self.list_depth += 1
        elif tag == "img":
            src = a.get("src") or a.get("data-src") or a.get("data-lazy-src") or ""
            srcset = a.get("srcset") or a.get("data-srcset") or ""
            self.images.append({
                "src": self._abs(src) if src else "",
                "srcset": [self._abs(p.strip().split()[0]) for p in srcset.split(",") if p.strip()],
                "srcset_w": self._srcset(srcset),
                "alt": htmlmod.unescape(a.get("alt") or "").strip(),
                "width": _int(a.get("width")), "height": _int(a.get("height")),
                "landmark": self.landmark, "in_link": self._link_text is not None,
                "link_href": self._abs(self._link_attrs.get("href", "")) if self._link_text is not None and getattr(self, "_link_attrs", None) else "",
                "classes": sorted(self._classes(a)), "row": _int(a.get("data-tt-row")),
            })
        elif tag == "form":
            self._current_form = {"action": self._abs(a.get("action") or self.base), "method": (a.get("method") or "get").lower(),
                                  "fields": [], "landmark": self.landmark}
            self.forms.append(self._current_form)
        elif tag in ("input", "textarea", "select", "button") and self._current_form is not None:
            typ = (a.get("type") or ("textarea" if tag == "textarea" else "select" if tag == "select" else "text")).lower()
            if tag == "button" and typ not in ("submit",):
                pass
            elif typ not in ("hidden", "submit", "button", "reset") or tag == "button":
                self._current_form["fields"].append({"name": a.get("name") or "", "type": typ,
                                                     "required": "required" in a})
        elif tag == "iframe" and (a.get("src") or a.get("data-src")):
            self.iframes.append(self._abs(a.get("src") or a.get("data-src")))
        elif tag == "script":
            if (a.get("type") or "").lower().strip() == "application/ld+json":
                self._text_target = "jsonld"
                self._buf = []
            else:
                if a.get("src"):
                    self.scripts.append(self._abs(a["src"]))
                self._text_target = "script"
                self._buf = []
        # microdata
        if "itemscope" in a:
            item = {"type": a.get("itemtype") or "", "props": {}}
            self.microdata.append(item)
            self._item_stack.append((len(self.stack), item))
        if a.get("itemprop") and self._item_stack:
            name = a["itemprop"]
            value = a.get("content") or a.get("href") or a.get("src") or a.get("datetime")
            if value is not None:
                self._item_stack[-1][1]["props"].setdefault(name, htmlmod.unescape(value))
            else:
                self._itemprop = (name, [])

    def handle_endtag(self, tag):
        self._block_end(tag)
        if tag in ("ul", "ol") and self.landmark and self.list_depth:
            self.list_depth -= 1
        if tag == "title":
            self._text_target = None
        elif tag in HEADING_TAGS and self._heading:
            text = _squash("".join(self._heading[1]))
            if text:
                self.headings.append((self._heading[0], text))
                if self.landmark:
                    self._current_group = text
            self._heading = None
            self._text_target = None
        elif tag == "a" and self._link_text is not None:
            a = getattr(self, "_link_attrs", {}) or {}
            text = _squash("".join(self._link_text))
            if a.get("href"):
                self.links.append({"href": self._abs(a["href"]), "text": text, "landmark": self.landmark,
                                   "in_main": bool(self._main_depth),
                                   "depth": self.list_depth, "group": self._current_group,
                                   "classes": sorted(self._classes(a)), "rel": (a.get("rel") or "").lower()})
            self._link_text = None
        elif tag == "form":
            self._current_form = None
        elif tag == "script":
            text = "".join(getattr(self, "_buf", []))
            if self._text_target == "jsonld":
                self.jsonld.append(text)
            elif self._text_target == "script" and text.strip():
                self.script_text.append(text[:20000])
            self._text_target = None
        elif tag == "noscript" and self._in_noscript:
            self._in_noscript -= 1
        if self._itemprop and tag not in ("br", "img", "meta", "link", "input"):
            name, chars = self._itemprop
            if self._item_stack:
                self._item_stack[-1][1]["props"].setdefault(name, _squash("".join(chars)))
            self._itemprop = None
        # pop the element and any landmark or item it opened
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                popped = self.stack[i:]
                del self.stack[i:]
                for _, lm, hides, mains in popped:
                    if lm and self.landmark_stack:
                        self.landmark_stack.pop()
                        self._current_group = None
                    if hides and self._skip_depth:
                        self._skip_depth -= 1
                    if mains and self._main_depth:
                        self._main_depth -= 1
                while self._item_stack and self._item_stack[-1][0] > len(self.stack):
                    self._item_stack.pop()
                break

    def close(self):
        super().close()
        # an anchor the page never closed still counts as a link
        if self._link_text is not None:
            self.handle_endtag("a")

    def handle_data(self, data):
        self._block_text(data)
        if self._text_target == "title":
            self.title += data
        elif self._text_target == "heading" and self._heading:
            self._heading[1].append(data)
        elif self._text_target in ("script", "jsonld"):
            self._buf.append(data)
            return
        if self._link_text is not None and self._text_target != "heading":
            self._link_text.append(data)
        if self._itemprop:
            self._itemprop[1].append(data)
        text = _squash(data)
        if not text:
            return
        if self.landmark:
            self.landmark_lines[self.landmark].append(text)
        elif not self._in_noscript:
            self.body_text.append(text)


def _int(v):
    try:
        return int(str(v).strip().rstrip("px")) if v not in (None, "") else None
    except ValueError:
        return None


def _squash(text):
    return re.sub(r"\s+", " ", htmlmod.unescape(text or "")).strip()


def _squash_lines(text):
    """_squash, keeping the line breaks a <br> made (an address, a list of hours)."""
    lines = [_squash(line) for line in (text or "").split("\n")]
    return "\n".join(line for line in lines if line)


def parse_page(html, url):
    """Everything the crawl needs from one page, with absolute URLs.
    Never raises on bad HTML."""
    p = PageParser(url)
    try:
        p.feed(html)
        p.close()
    except Exception:
        pass
    root_host = urlsplit(url).hostname or ""
    canonical = next((h for rel, h, _ in p.link_rels if "canonical" in rel.split()), None)
    hreflang = [{"lang": hl, "href": h} for rel, h, hl in p.link_rels if "alternate" in rel.split() and hl]
    robots = (p.meta.get("robots") or "").lower()
    h1s = [t for tag, t in p.headings if tag == "h1"]
    internal = [l for l in p.links if same_site(l["href"], root_host)
                and not l["href"].lower().startswith(("mailto:", "tel:", "javascript:"))]
    return {
        "url": url,
        "title": _squash(p.title),
        "lang": p.lang,
        "meta": p.meta,
        "meta_description": p.meta.get("description", ""),
        "canonical": canonical,
        "hreflang": hreflang,
        "noindex": "noindex" in robots,
        "h1": h1s[0] if h1s else "",
        "h1_count": len(h1s),
        "headings": p.headings,
        "links": p.links,
        "internal_links": internal,
        "images": p.images,
        "forms": [f for f in p.forms if f["fields"]],
        "iframes": p.iframes,
        "scripts": p.scripts,
        "script_text": p.script_text,
        "jsonld_raw": p.jsonld,
        "microdata": [m for m in p.microdata if m["props"]],
        "landmark_lines": p.landmark_lines,
        "body_word_count": sum(len(re.findall(r"\w+", t)) for t in p.body_text),
        "has_landmarks": bool(p.landmark_lines),
        "blocks": p.blocks_out(),
    }
