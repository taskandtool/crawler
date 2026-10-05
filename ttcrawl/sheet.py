"""`tt-crawl sheet`: several pictures as one numbered contact sheet, so a
model names them all in one look (a site's logos, say) instead of opening
each file. Drawn by the browser from the files themselves, inlined: no
server and no image library.

    tt-crawl sheet static/images/logos/*.png --out raw/logos.png
"""
import base64
import html
import json
import mimetypes
import os
import sys

from . import cdp, chrome

COLUMNS, CELL_W, CELL_H = 4, 260, 150


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


def run(args):
    paths = [p for p in args.pictures if os.path.isfile(p)]
    missing = [p for p in args.pictures if not os.path.isfile(p)]
    if not paths:
        sys.stderr.write("sheet: no pictures to draw%s\n" % (": not found: " + ", ".join(missing) if missing else ""))
        return 2
    driver, note = chrome.driver("chrome")
    if driver is None:
        sys.stderr.write("sheet: %s\n" % (note or "no browser on this machine (tt-crawl setup installs one)"))
        return 1
    try:
        w, h = (int(x) for x in args.cell.lower().split("x"))
        result = driver._use(lambda b: draw(b, paths, args.out, args.columns, (w, h)))
    except cdp.CDPError as e:
        sys.stderr.write("sheet: the browser failed: %s\n" % str(e).split("\n")[0][:300])
        return 1
    finally:
        driver.stop()
    result["numbered"] = [os.path.basename(p) for p in paths]
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print("sheet: %s  (%d pictures, numbered in this order)" % (args.out, len(paths)))
        for i, p in enumerate(paths, 1):
            print("  %d  %s" % (i, p))
        if missing:
            print("  not found: %s" % ", ".join(missing))
        print("Next: look at the sheet once and name each picture by its number.")
    return 0


def add_parser(sub):
    p = sub.add_parser("sheet", help="several pictures as one numbered contact sheet, to name them in one look")
    p.add_argument("pictures", nargs="+", help="image files")
    p.add_argument("--out", default="sheet.png", help="where the sheet goes (default sheet.png)")
    p.add_argument("--columns", type=int, default=COLUMNS, help="pictures a row (default %d)" % COLUMNS)
    p.add_argument("--cell", default="%dx%d" % (CELL_W, CELL_H),
                   help="each cell's size in pixels, WxH (default %dx%d; 380x300 suits photographs)" % (CELL_W, CELL_H))
    p.add_argument("--json", action="store_true", help="the result as JSON")
    p.set_defaults(func=run)
