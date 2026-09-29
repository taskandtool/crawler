"""Pure text helpers: slugs, dedupe, the repetition fallback for site
furniture, markdown image handling, a plain html-to-text."""
import hashlib
import html as htmlmod
import re
import zlib
from urllib.parse import urlsplit

MIN_MARKDOWN_CHARS = 200
MD_IMAGE_RE = re.compile(r"!\[[^\]]*\]\(([^)\s]+)")
MD_IMAGE_SUB = re.compile(r"(!\[[^\]]*\]\()([^)\s]+)")


def slugify(url):
    path = urlsplit(url).path.strip("/") or "index"
    slug = re.sub(r"[^a-z0-9]+", "-", path.lower()).strip("-")
    return (slug or "index")[:80]


CLEAN_PATH_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*(?:/[a-z0-9]+(?:-[a-z0-9]+)*)*")


def page_name(url, hashed=False):
    """A file stem that is one URL's own (pure). A lowercase, hyphenated path
    with no query spells itself out, `/` as `--` (so /blog/post and /blog-post
    stay apart); anything slugify would lose (case, other characters, a query,
    length) keeps a short hash of the whole URL beside its slug, as does any
    URL with `hashed`."""
    parts = urlsplit(url)
    path = parts.path.strip("/")
    if not hashed and not parts.query and len(path) <= 80 and (not path or CLEAN_PATH_RE.fullmatch(path)):
        return path.replace("/", "--") or "index"
    return "%s-%s" % (slugify(url)[:70], hashlib.sha1(url.encode()).hexdigest()[:8])


def rewrite_images(md, local_map):
    """Rewrite each `![alt](url)` whose url is in local_map, touching only
    the url inside an image tag."""
    return MD_IMAGE_SUB.sub(lambda m: m.group(1) + local_map.get(m.group(2), m.group(2)), md)


def content_digest(md):
    return hashlib.sha256(re.sub(r"\s+", " ", md).strip().encode()).hexdigest()


def shingles(text, k=8):
    words = re.findall(r"\w+", text.lower())
    return {" ".join(words[i:i + k]) for i in range(max(len(words) - k + 1, 1))}


MINHASH_N, MINHASH_BANDS = 64, 16
_PRIME = (1 << 61) - 1
_SEEDS = [(int(hashlib.sha1(b"a%d" % i).hexdigest()[:12], 16) | 1, int(hashlib.sha1(b"b%d" % i).hexdigest()[:12], 16))
          for i in range(MINHASH_N)]


def minhash(text):
    """A 64-number signature of a text's 8-word shingles (pure, stable across
    runs): the share of numbers two signatures have in common estimates how
    much of the two texts overlap."""
    base = [zlib.crc32(s.encode()) for s in shingles(text)]
    return [min((a * x + b) % _PRIME for x in base) for a, b in _SEEDS]


class NearDuplicates:
    """Pages kept so far, by signature. A new page is a near duplicate when
    9 in 10 of its signature matches a kept page's; only pages that share a
    band of the signature are compared, so thousands of pages stay quick."""

    def __init__(self, threshold=0.9):
        self.threshold, self.signatures, self.buckets = threshold, [], {}

    def _bands(self, sig):
        rows = MINHASH_N // MINHASH_BANDS
        return [(i, tuple(sig[i * rows:(i + 1) * rows])) for i in range(MINHASH_BANDS)]

    def check(self, sig):
        """True when `sig` nearly matches a kept signature (pure over the kept set)."""
        seen = set()
        for band in self._bands(sig):
            for j in self.buckets.get(band, ()):
                if j in seen:
                    continue
                seen.add(j)
                other = self.signatures[j]
                if sum(1 for x, y in zip(sig, other) if x == y) / MINHASH_N >= self.threshold:
                    return True
        return False

    def keep(self, sig):
        j = len(self.signatures)
        self.signatures.append(sig)
        for band in self._bands(sig):
            self.buckets.setdefault(band, []).append(j)


def norm_line(line):
    return re.sub(r"\s+", " ", line.strip()).lower()


def html_to_text(html):
    """A readable fallback when no extractor is available."""
    html = re.sub(r"(?is)<(script|style|noscript|svg|template)[^>]*>.*?</\1>", " ", html)
    html = re.sub(r"(?i)</(p|div|section|article|li|h[1-6]|br|tr|header|footer|nav)>", "\n", html)
    text = re.sub(r"<[^>]+>", " ", html)
    text = htmlmod.unescape(text)
    text = re.sub(r"[ \t]+", " ", text)
    return re.sub(r"\n\s*\n+", "\n\n", text).strip() + "\n"


def word_count(text):
    return len(re.findall(r"\w+", text or ""))
