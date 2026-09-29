"""A light element tree over a page's HTML, for the judgements that need to
know what an element sits inside: a review is a quote and a name in one
card, a contact fact is in the site's header or on its contact page.
Standard-library HTMLParser only; never raises on bad HTML."""
import html as htmlmod
import re
from html.parser import HTMLParser

VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}
SKIP = {"script", "style", "noscript", "template", "svg"}
CHROME_TAGS = {"header", "nav", "footer", "aside"}
CHROME_ROLES = {"banner", "navigation", "contentinfo", "complementary"}
CHROME_HINT_RE = re.compile(r"(^|[\s_-])(site-header|site-footer|masthead|colophon|navbar|main-nav|primary-nav|"
                            r"site-nav|main-menu|primary-menu|global-header|global-footer|page-footer|topbar|top-bar)($|[\s_-])")


class Node:
    __slots__ = ("tag", "attrs", "children", "parent", "text_parts")

    def __init__(self, tag, attrs, parent):
        self.tag, self.attrs, self.parent = tag, attrs, parent
        self.children, self.text_parts = [], []

    @property
    def cls(self):
        return (self.attrs.get("class") or "").lower()

    def text(self):
        out = []

        def walk(n):
            for part in n.text_parts:
                if isinstance(part, Node):
                    if part.tag not in SKIP:
                        out.append(" ")          # two elements side by side are two words
                        walk(part)
                        out.append(" ")
                else:
                    out.append(part)
        walk(self)
        return re.sub(r"\s+", " ", htmlmod.unescape("".join(out))).strip()

    def iter(self):
        yield self
        for c in self.children:
            yield from c.iter()

    def ancestors(self):
        n = self.parent
        while n is not None:
            yield n
            n = n.parent

    def closest(self, pred):
        for n in [self, *self.ancestors()]:
            if pred(n):
                return n
        return None

    def in_chrome(self):
        """Inside the site's header, nav, footer or sidebar; an article's own
        header or footer (inside <main> or <article>) is not the site's."""
        for n in [self, *self.ancestors()]:
            role = (n.attrs.get("role") or "").lower()
            if n.tag in CHROME_TAGS or role in CHROME_ROLES or CHROME_HINT_RE.search(n.cls + " " + (n.attrs.get("id") or "").lower()):
                if n.tag in ("header", "footer") and not role and any(a.tag in ("main", "article") for a in n.ancestors()):
                    continue
                return True
        return False

    def links(self):
        return [n for n in self.iter() if n.tag == "a" and n.attrs.get("href")]

    def raw(self):
        """The element's markup, near enough to search for a platform's name
        or brand colour (a review card's Google or Yelp mark)."""
        bits = []
        for n in self.iter():
            bits.append(n.tag + " " + " ".join("%s=%s" % kv for kv in n.attrs.items() if kv[1]))
        return " ".join(bits) + " " + self.text()


class _Builder(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = Node("#root", {}, None)
        self.cur = self.root

    def handle_starttag(self, tag, attrs):
        node = Node(tag, {k: (v or "") for k, v in attrs}, self.cur)
        self.cur.children.append(node)
        self.cur.text_parts.append(node)
        if tag not in VOID:
            self.cur = node

    def handle_startendtag(self, tag, attrs):
        node = Node(tag, {k: (v or "") for k, v in attrs}, self.cur)
        self.cur.children.append(node)
        self.cur.text_parts.append(node)

    def handle_endtag(self, tag):
        n = self.cur
        while n is not self.root and n.tag != tag:
            n = n.parent
        if n is not self.root:
            self.cur = n.parent

    def handle_data(self, data):
        self.cur.text_parts.append(data)


def build(html):
    b = _Builder()
    try:
        b.feed(html)
        b.close()
    except Exception:
        pass
    return b.root
