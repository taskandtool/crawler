"""Pure text helpers: slugs, dedupe, the repetition fallback for site
furniture, markdown image handling, a plain html-to-text."""
import hashlib
import html as htmlmod
import math
import os
import re
from urllib.parse import urlsplit

MIN_MARKDOWN_CHARS = 200
MD_IMAGE_RE = re.compile(r"!\[[^\]]*\]\(([^)\s]+)")
MD_IMAGE_SUB = re.compile(r"(!\[[^\]]*\]\()([^)\s]+)")
SIZE_SUFFIX_RE = re.compile(r"(-\d{2,4}x\d{2,4}|@\dx|_\d{2,4}x\d{2,4}|-scaled)(?=\.[a-z0-9]{2,5}$)", re.I)


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


def ext_for(url, ctype):
    for cand in (os.path.splitext(urlsplit(url).path)[1].lstrip("."), ctype.split("/")[-1]):
        if cand and re.fullmatch(r"[a-z0-9]{2,5}", cand.lower()):
            return cand.lower().replace("jpeg", "jpg")
    return "img"


def image_key(url):
    """The identity of an image across its size and format variants."""
    parts = urlsplit(url)
    path = SIZE_SUFFIX_RE.sub("", parts.path)
    return (parts.hostname or "").lower() + path.lower()


def rewrite_images(md, local_map):
    """Rewrite each `![alt](url)` whose url is in local_map, touching only
    the url inside an image tag."""
    return MD_IMAGE_SUB.sub(lambda m: m.group(1) + local_map.get(m.group(2), m.group(2)), md)


def content_digest(md):
    return hashlib.sha256(re.sub(r"\s+", " ", md).strip().encode()).hexdigest()


def shingles(text, k=8):
    words = re.findall(r"\w+", text.lower())
    return {" ".join(words[i:i + k]) for i in range(max(len(words) - k + 1, 1))}


def near_duplicate(text, kept_shingle_sets, threshold=0.9):
    s = shingles(text)
    for other in kept_shingle_sets:
        inter = len(s & other)
        union = len(s | other) or 1
        if inter / union >= threshold:
            return True, s
    return False, s


def norm_line(line):
    return re.sub(r"\s+", " ", line.strip()).lower()


def boilerplate_threshold(n_pages):
    """A line is site furniture when it appears on at least this many pages:
    a third of the site, never fewer than 3."""
    return max(3, math.ceil(n_pages / 3))


def strip_common_lines(pages, threshold=None):
    """The repetition fallback for sites without landmarks: remove the lines
    that repeat across the site, keep each once. Needs 4 or more pages."""
    if len(pages) < 4:
        return list(pages), []
    threshold = threshold or boilerplate_threshold(len(pages))
    counts, first_seen = {}, {}
    for md in pages:
        seen_here = set()
        for line in md.splitlines():
            key = norm_line(line)
            if not key or key in seen_here or re.fullmatch(r"[-*_= ]+", key):
                continue
            seen_here.add(key)
            counts[key] = counts.get(key, 0) + 1
            first_seen.setdefault(key, line.strip())
    common = {k for k, c in counts.items() if c >= threshold}
    cleaned = [strip_lines(md, common) for md in pages]
    common_lines = [first_seen[k] for k in sorted(common, key=lambda k: list(first_seen).index(k))]
    return cleaned, common_lines


def strip_lines(md, normalized_set):
    """Remove every line whose normalized form is in the set (pure)."""
    kept = [line for line in md.splitlines() if norm_line(line) not in normalized_set]
    text = "\n".join(kept)
    return re.sub(r"\n{3,}", "\n\n", text).strip() + "\n"


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
