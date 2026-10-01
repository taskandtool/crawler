"""A page's content blocks (html.py collects them in document order) as
markdown, and the page's shape as a short fingerprint.

The text is the page's own, verbatim: headings, paragraphs, list items,
quotes, captions and table cells as the site wrote them, links kept inline,
photographs where the page showed them. Nothing is summarized or merged.
"""
import hashlib
import re

HEADING_LEVEL = {"h1": 1, "h2": 2, "h3": 3, "h4": 4, "h5": 5, "h6": 6}


def to_markdown(blocks):
    """Markdown from blocks (pure). The site's header, nav, footer and aside
    blocks are left out."""
    out, last = [], None
    for b in blocks:
        if b.get("chrome"):
            continue
        tag = b["tag"]
        if tag == "img":
            line = "![%s](%s)" % ((b.get("alt") or "").replace("]", ")"), b["src"])
        else:
            text = b["text"]
            if tag in HEADING_LEVEL:
                one_line = text.replace("\n", " ")
                heading = "[%s](%s)" % (one_line, b["href"]) if b.get("href") and "](" not in one_line else one_line
                line = "#" * HEADING_LEVEL[tag] + " " + heading
            elif tag == "li":
                line = "- " + text.replace("\n", "\n  ")
            elif tag == "blockquote":
                line = "> " + text.replace("\n", "\n> ")
            elif tag == "figcaption":
                line = "*%s*" % text.replace("\n", " ")
            elif tag == "pre":
                line = "```\n%s\n```" % text
            else:
                line = text.replace("\n", "  \n")      # a <br> is a hard line break
        # list items sit together; everything else is its own paragraph
        if out and not (tag == "li" and last == "li"):
            out.append("")
        out.append(line)
        last = tag
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip() + "\n" if out else ""


def fingerprint(blocks, first=8):
    """The shape of the top of a page's own content (pure): its block tags in
    order, repeats collapsed (three paragraphs read as one run), the first
    `first` runs, hashed. The top is where a template shows (a post's title,
    date, lead image and first paragraphs); the rest varies with the copy.
    It tells a flat site's posts from its pages where the URLs cannot."""
    runs = []
    for b in blocks:
        if b.get("chrome"):
            continue
        tag = "h" if b["tag"] in HEADING_LEVEL else b["tag"]
        if not runs or runs[-1] != tag:
            runs.append(tag)
    return hashlib.sha1(" ".join(runs[:first]).encode()).hexdigest()[:10] if runs else ""
