"""Static accessibility and on-page SEO checks over one page's HTML, pure.
What a parser can see without rendering: the common WCAG failures and the
title and description hygiene search engines care about. Rendered checks
(focus visibility, tap targets, colour used alone) belong to a person
looking at the page.
"""
from collections import Counter
from html.parser import HTMLParser

GENERIC_LINK_TEXT = {"click here", "here", "read more", "learn more", "more", "link", "this", "continue"}


class _A11y(HTMLParser):
    def __init__(self):
        super().__init__()
        self.lang = None
        self.viewport = False
        self.headings = []       # (level, text)
        self.links = []          # (text, has_img_alt, aria)
        self.buttons = []        # (text, aria)
        self.inputs = []         # (id, aria, type, tag)
        self.label_for = set()
        self.ids = Counter()
        self._link = None
        self._button = None
        self._heading = None
        self._label_wraps = 0

    def handle_starttag(self, tag, attrs):
        a = {k.lower(): (v or "") for k, v in attrs}
        if a.get("id"):
            self.ids[a["id"]] += 1
        if tag == "html":
            self.lang = a.get("lang", "")
        elif tag == "meta" and (a.get("name") or "").lower() == "viewport":
            self.viewport = True
        elif tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self._heading = [int(tag[1]), []]
        elif tag == "a" and "href" in a:
            self._link = {"text": [], "img_alt": False, "aria": a.get("aria-label", "").strip() or a.get("title", "").strip()}
        elif tag == "button":
            self._button = {"text": [], "aria": a.get("aria-label", "").strip() or a.get("title", "").strip()}
        elif tag == "img":
            if self._link is not None and a.get("alt", "").strip():
                self._link["img_alt"] = True
            if self._button is not None and a.get("alt", "").strip():
                self._button["text"].append(a["alt"])
        elif tag == "label":
            if a.get("for"):
                self.label_for.add(a["for"])
            self._label_wraps += 1
        elif tag in ("input", "textarea", "select"):
            typ = (a.get("type") or "text").lower()
            if tag == "input" and typ in ("hidden", "submit", "button", "reset", "image"):
                return
            labelled = bool(a.get("aria-label") or a.get("aria-labelledby") or self._label_wraps)
            self.inputs.append((a.get("id", ""), labelled, typ, tag))

    def handle_endtag(self, tag):
        if tag in ("h1", "h2", "h3", "h4", "h5", "h6") and self._heading:
            self.headings.append((self._heading[0], " ".join(self._heading[1]).strip()))
            self._heading = None
        elif tag == "a" and self._link is not None:
            self.links.append((" ".join(self._link["text"]).strip(), self._link["img_alt"], self._link["aria"]))
            self._link = None
        elif tag == "button" and self._button is not None:
            self.buttons.append((" ".join(self._button["text"]).strip(), self._button["aria"]))
            self._button = None
        elif tag == "label" and self._label_wraps:
            self._label_wraps -= 1

    def handle_data(self, data):
        t = " ".join(data.split())
        if not t:
            return
        if self._heading:
            self._heading[1].append(t)
        if self._link is not None:
            self._link["text"].append(t)
        if self._button is not None:
            self._button["text"].append(t)


def findings(html, title="", description=""):
    """The page's static accessibility and SEO findings, as short strings."""
    p = _A11y()
    try:
        p.feed(html)
    except Exception:
        return ["HTML could not be parsed"]
    out = []
    if p.lang is None or not p.lang.strip():
        out.append("no lang attribute on <html>")
    if not p.viewport:
        out.append("no viewport meta tag")
    last = 0
    for level, _text in p.headings:
        if last and level > last + 1:
            out.append(f"heading order skips from h{last} to h{level}")
            break
        last = level
    empty = sum(1 for text, img_alt, aria in p.links if not text and not img_alt and not aria)
    if empty:
        out.append(f"{empty} link(s) with no text")
    generic = sorted({text.lower().rstrip(".!") for text, _, _ in p.links if text.lower().rstrip(".!") in GENERIC_LINK_TEXT})
    if generic:
        out.append("generic link text: " + ", ".join(f'"{g}"' for g in generic))
    empty_buttons = sum(1 for text, aria in p.buttons if not text and not aria)
    if empty_buttons:
        out.append(f"{empty_buttons} button(s) with no text")
    unlabelled = sum(1 for id_, labelled, _typ, _tag in p.inputs if not labelled and not (id_ and id_ in p.label_for))
    if unlabelled:
        out.append(f"{unlabelled} form field(s) without a label")
    dupes = [i for i, n in p.ids.items() if n > 1]
    if dupes:
        out.append(f"duplicate id(s): {', '.join(sorted(dupes)[:5])}")
    if title and len(title) > 60:
        out.append(f"title is {len(title)} characters (keep it under 60)")
    if description and (len(description) < 70 or len(description) > 160):
        out.append(f"meta description is {len(description)} characters (70 to 160 reads as a snippet)")
    return out
