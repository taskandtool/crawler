"""`tt-crawl sheet`: several pictures as one numbered contact sheet, so a
model names them all in one look (a site's logos, say) instead of opening
each file. Drawn by the browser from the files themselves, inlined: no
server and no image library.

    tt-crawl sheet logos/*.png --out logos.png
    tt-crawl sheet --from raw/site/example.com            # the business card's photographs
    tt-crawl sheet --from raw/site/example.com --marks    # others' logos the crawl kept
"""
import argparse
import base64
import html
import json
import mimetypes
import os

from . import card, cdp, chrome
from .say import command, done, fail

COLUMNS, CELL_W, CELL_H = 4, 260, 150
# photographs want bigger cells, fewer to a row
PHOTO_COLUMNS, PHOTO_CELL = 3, (380, 300)


def page(paths, columns=COLUMNS, cell=(CELL_W, CELL_H)):
    """The sheet as HTML (pure): each picture in a numbered cell on mid grey,
    so both dark and white logos show; `cell` is (width, height)."""
    cells = []
    for i, p in enumerate(paths, 1):
        mime = mimetypes.guess_type(p)[0] or "image/png"
        with open(p, "rb") as f:
            data = base64.b64encode(f.read()).decode()
        cells.append('<figure><b>%d</b><img src="data:%s;base64,%s"><figcaption>%s</figcaption></figure>'
                     % (i, mime, data, html.escape(os.path.basename(p))))
    return """<!doctype html><meta charset="utf-8"><style>
body{margin:0;padding:16px;background:#fff;font:14px/1.3 sans-serif}
main{display:grid;grid-template-columns:repeat(%d,%dpx);gap:12px}
figure{margin:0;position:relative;height:%dpx;background:#c8c8c8;display:flex;flex-direction:column;
  align-items:center;justify-content:center;gap:6px;padding:8px;box-sizing:border-box}
img{max-width:%dpx;max-height:%dpx;object-fit:contain}
b{position:absolute;top:4px;left:8px;font-size:18px}
figcaption{font-size:11px;color:#333;max-width:240px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
</style><main>%s</main>""" % (columns, cell[0], cell[1], cell[0] - 40, cell[1] - 60, "".join(cells))


def draw(browser, paths, out, columns=COLUMNS, cell=(CELL_W, CELL_H), timeout=45):
    width = 32 + columns * cell[0] + (columns - 1) * 12
    s = cdp.Session(browser.ws_url, timeout=timeout)
    try:
        s.call("Page.enable")
        s.call("Emulation.setDeviceMetricsOverride", {"width": width, "height": 600, "deviceScaleFactor": 1, "mobile": False})
        frame = s.call("Page.getFrameTree")["frameTree"]["frame"]["id"]
        s.call("Page.setDocumentContent", {"frameId": frame, "html": page(paths, columns, cell)})
        s.evaluate("Promise.all([...document.images].map(i => i.decode().catch(() => null)))", await_promise=True)
        height = int(s.evaluate("Math.ceil(document.querySelector('main').getBoundingClientRect().bottom) + 16") or 600)
        shot = s.call("Page.captureScreenshot", {"format": "png", "captureBeyondViewport": True,
                                                 "clip": {"x": 0, "y": 0, "width": width, "height": height, "scale": 1}})
    finally:
        s.close()
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    with open(out, "wb") as f:
        f.write(base64.b64decode(shot["data"]))
    return {"out": out, "pictures": len(paths), "width": width, "height": height}


def from_crawl(folder, marks):
    """The pictures a crawl folder offers, in order: the business card's
    photographs, or with `marks` the others' logos the crawl kept."""
    if not marks:
        return [os.path.join(folder, p["file"]) for p in card.build(folder)["photos"]]
    try:
        with open(os.path.join(folder, "_index", "media.json")) as f:
            media = json.load(f)
    except (OSError, ValueError):
        media = []
    return [os.path.join(folder, "images", it["file"]) for it in media if it.get("kind") == "mark" and it.get("file")]


def run(args):
    if bool(args.pictures) == bool(args.from_):
        return fail(args, 2, "name the pictures, or a crawl folder with --from (one or the other)",
                    "tt-crawl sheet --from raw/site/<host>, or tt-crawl sheet a.png b.png --out sheet.png")
    if args.marks and not args.from_:
        return fail(args, 2, "--marks reads a crawl folder: give it --from", "tt-crawl sheet --from raw/site/<host> --marks")
    if args.from_ and not os.path.isdir(os.path.join(args.from_, "_index")):
        return fail(args, 2, "no crawl at %s (no _index/ in it)" % args.from_,
                    "ls raw/site/, then tt-crawl sheet --from raw/site/<host>")
    photos = bool(args.from_) and not args.marks
    pictures = from_crawl(args.from_, args.marks) if args.from_ else args.pictures
    out = args.out or (os.path.join(args.from_, "_index", "marks.png" if args.marks else "photos.png")
                       if args.from_ else "sheet.png")
    columns = args.columns or (PHOTO_COLUMNS if photos else COLUMNS)
    size = args.cell or (PHOTO_CELL if photos else (CELL_W, CELL_H))
    paths = [p for p in pictures if os.path.isfile(p)]
    missing = [p for p in pictures if not os.path.isfile(p)]
    if not paths:
        if args.from_:
            what = "others' logos" if args.marks else "photographs %dpx wide or more" % card.PHOTO_MIN_WIDTH
            return fail(args, 1, "%s holds no %s" % (args.from_, what),
                        "ls %s/images/, or tt-crawl brand <url> --resume to read more pages" % args.from_)
        return fail(args, 2, "no pictures to draw: not found: %s" % ", ".join(missing),
                    "tt-crawl sheet logos/*.png --out logos.png")
    driver, note = chrome.driver()
    if driver is None:
        return fail(args, 1, note or "no browser on this machine", "tt-crawl setup, then this command again")
    try:
        result = driver._use(lambda b: draw(b, paths, out, columns, size))
    except cdp.CDPError as e:
        return fail(args, 1, "the browser failed: %s" % str(e).split("\n")[0][:300],
                    "the same command again; tt-crawl setup when it fails twice")
    finally:
        driver.stop()
    result["numbered"] = [os.path.basename(p) for p in paths]
    lines = ["%d  %s" % (i, p) for i, p in enumerate(paths, 1)]
    if missing:
        lines.append("left alone, not found: %s" % ", ".join(missing))
    done(args, result, "%s (%d pictures, numbered in this order)" % (out, len(paths)), lines,
         "look at the sheet once and name each picture by its number")
    return 0


def cell(value):
    """--cell: WxH in pixels."""
    try:
        w, h = (int(x) for x in value.lower().split("x"))
    except ValueError:
        raise argparse.ArgumentTypeError("%r is not WxH in pixels, e.g. 380x300" % value)
    if w < 80 or h < 80:
        raise argparse.ArgumentTypeError("%r is too small: each side at least 80 pixels" % value)
    return w, h


def add_parser(sub):
    p = command(sub, "sheet", "several pictures as one numbered contact sheet, to name them in one look",
                "Prints where the sheet went and each picture's number, then Next:. A refusal goes to stderr with a "
                "Try: line.")
    p.add_argument("pictures", nargs="*", help="image files (or --from)")
    p.add_argument("--from", dest="from_", default="", metavar="FOLDER",
                   help="a crawl folder: its business card's photographs (%dpx wide or more, widest first, %d at most), "
                        "drawn to <folder>/_index/photos.png" % (card.PHOTO_MIN_WIDTH, card.PHOTOS_KEPT))
    p.add_argument("--marks", action="store_true", help="with --from: others' logos instead, to _index/marks.png")
    p.add_argument("--out", default="", help="where the sheet goes (default sheet.png, or into the crawl folder with --from)")
    p.add_argument("--columns", type=int, default=0,
                   help="pictures a row (default %d; %d for photographs)" % (COLUMNS, PHOTO_COLUMNS))
    p.add_argument("--cell", type=cell, default=None,
                   help="each cell's size in pixels, WxH (default %dx%d; %dx%d for photographs)"
                        % (CELL_W, CELL_H, *PHOTO_CELL))
    p.set_defaults(func=run)
