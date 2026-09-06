"""Every image the site uses, with what it probably is."""
import re
from urllib.parse import urlsplit

STOCK_HOSTS = ("unsplash.com", "pexels.com", "shutterstock.com", "istockphoto.com", "gettyimages", "pixabay.com",
               "adobestock", "freepik.com", "dreamstime.com", "depositphotos")
ICON_RE = re.compile(r"icon|sprite|arrow|chevron|bullet|check|star|social|favicon", re.I)
THEME_RE = re.compile(r"/themes?/|/theme-assets/|/assets/(img|images)/(bg|pattern|texture|placeholder)|placeholder|pattern|texture|/plugins/", re.I)
STOCK_NAME_RE = re.compile(r"shutterstock|istock|adobestock|gettyimages|depositphotos|stock-photo|pexels|unsplash", re.I)


def guess_kind(img, logo_src=None):
    src = (img.get("src") or "").lower()
    alt = (img.get("alt") or "").lower()
    host = (urlsplit(src).hostname or "").lower()
    w, h = img.get("width"), img.get("height")
    if logo_src and src == logo_src.lower():
        return "logo"
    if "logo" in src or "logo" in alt:
        return "logo"
    if src.endswith(".svg") or ICON_RE.search(src) or (w and h and w <= 64 and h <= 64):
        return "icon"
    if any(s in host for s in STOCK_HOSTS) or STOCK_NAME_RE.search(src):
        return "stock"
    if THEME_RE.search(src) or (img.get("landmark") in ("header", "footer") and not alt):
        return "theme"
    if src.endswith((".jpg", ".jpeg", ".webp", ".avif", ".heic")) or "upload" in src or "photo" in src or "gallery" in src:
        return "photo"
    return "photo" if alt else "theme"


def largest_variant(img):
    """The biggest srcset candidate by the width descriptor when present."""
    best, best_w = img.get("src") or "", -1
    for cand in img.get("srcset_full") or []:
        url, w = cand
        if w > best_w:
            best, best_w = url, w
    return best


def build_media(per_page_images, logo_src=None):
    """per_page_images: [(page_url, [image dicts])] -> the _media.json list."""
    by_src = {}
    for page_url, images in per_page_images:
        for img in images:
            key = img.get("src") or (img.get("srcset") or [None])[0]
            if not key or key.startswith("data:"):
                continue
            rec = by_src.setdefault(key, {"url": key, "pages": [], "alt": img.get("alt") or "",
                                          "width": img.get("width"), "height": img.get("height"),
                                          "srcset": [], "guess": None, "landmark": img.get("landmark")})
            if page_url not in rec["pages"]:
                rec["pages"].append(page_url)
            if not rec["alt"] and img.get("alt"):
                rec["alt"] = img["alt"]
            for s in img.get("srcset") or []:
                if s not in rec["srcset"]:
                    rec["srcset"].append(s)
            if rec["width"] is None and img.get("width"):
                rec["width"], rec["height"] = img.get("width"), img.get("height")
    out = []
    for rec in by_src.values():
        rec["largest"] = rec["srcset"][-1] if rec["srcset"] else rec["url"]
        rec["guess"] = guess_kind({"src": rec["url"], "alt": rec["alt"], "width": rec["width"], "height": rec["height"],
                                   "landmark": rec["landmark"]}, logo_src)
        rec["local"] = None
        out.append(rec)
    out.sort(key=lambda r: (-len(r["pages"]), r["url"]))
    return out
