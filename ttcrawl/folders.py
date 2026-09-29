"""`tt-crawl relayout` and `tt-crawl freeze`: the folder itself.

`relayout` moves a folder an earlier tt-crawl wrote (raw/web: pages and
ledger at its root) into the layout paths.py describes, and writes
`_index/moved.json`, every old path beside its new one, so what cited the
old paths (a brain's notes, its ingest record) can be rewritten once.

`freeze` marks a folder as the site as it was (the old site, at launch):
no crawl writes into it again.
"""
import json
import os
import re
import shutil
import sys

from . import paths

LEDGER = {"_inventory.json": "inventory.json", "_inventory.md": "inventory.md", "_manifest.json": "manifest.json",
          "_furniture.json": "furniture.json", "_media.json": "media.json", "_styles.json": "styles.json",
          "_common.md": "common.md", "_audit.md": None, "_launch-check.md": None, "_launch-check.json": None}
SOURCE_RE = re.compile(r"\A<!-- source: (\S+) -->\n")


def relayout(old, new, moved=None, audit_dir=None):
    """Move `old` (a 0.1.x crawl) into `new`; returns {old path: new path}.
    Paths are joined as given, so a map made from the app's root cites as
    the app's files do."""
    moved = {} if moved is None else moved

    def move(src, dst):
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.move(src, dst)
        moved[src] = dst

    os.makedirs(os.path.join(new, paths.INDEX), exist_ok=True)
    for name in sorted(os.listdir(old)):
        src = os.path.join(old, name)
        if name.endswith(".md") and not name.startswith("_") and os.path.isfile(src):
            with open(src) as f:
                text = f.read()
            # a page file: the source comment becomes frontmatter; images now sit one folder up
            m = SOURCE_RE.match(text)
            if m:
                text = '---\nurl: %s\n---\n%s' % (json.dumps(m.group(1)), text[m.end():])
            text = re.sub(r"(!\[[^\]]*\]\()images/", r"\1../images/", text)
            dst = os.path.join(new, paths.PAGES, name)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            with open(dst, "w") as f:
                f.write(text)
            os.remove(src)
            moved[src] = dst
        elif name == "images" and os.path.isdir(src):
            if not os.path.exists(os.path.join(new, paths.IMAGES)):
                move(src, os.path.join(new, paths.IMAGES))
        elif name == "pages" and os.path.isdir(src):
            # screenshots: 0.1.4 strips pages/<name>/, 0.1.3 single pages/<name>.png
            for shot in sorted(os.listdir(src)):
                s = os.path.join(src, shot)
                if os.path.isdir(s):
                    move(s, os.path.join(new, paths.SHOTS, shot))
                elif shot.endswith(".png"):
                    move(s, os.path.join(new, paths.SHOTS, shot[:-4], "01.png"))
            if not os.listdir(src):
                os.rmdir(src)
        elif name in LEDGER and os.path.isfile(src):
            target = LEDGER[name]
            if target:
                move(src, os.path.join(new, paths.INDEX, target))
            elif audit_dir:
                move(src, os.path.join(audit_dir, name.lstrip("_")))
        elif name == "_structured" and os.path.isdir(src):
            move(src, os.path.join(new, paths.STRUCTURED))
    # raw/web's harvest sat beside it in raw/structured
    beside = os.path.join(os.path.dirname(old.rstrip("/")), "structured")
    if os.path.basename(old.rstrip("/")) == "web" and os.path.isdir(beside) and not os.path.exists(os.path.join(new, paths.STRUCTURED)):
        move(beside, os.path.join(new, paths.STRUCTURED))
    # the manifest's page list names the new files
    manifest_path = os.path.join(new, paths.INDEX, "manifest.json")
    if os.path.isfile(manifest_path):
        with open(manifest_path) as f:
            manifest = json.load(f)
        for p in manifest.get("pages", []) + manifest.get("earlier", []):
            if p.get("file") and "/" not in p["file"]:
                p["file"] = "%s/%s" % (paths.PAGES, p["file"])
        with open(manifest_path, "w") as f:
            json.dump(manifest, f, indent=2)
    with open(os.path.join(new, paths.INDEX, "moved.json"), "w") as f:
        json.dump(moved, f, indent=2)
    if os.path.isdir(old) and not os.listdir(old):
        os.rmdir(old)
    return moved


TEXT_EXTS = (".md", ".json", ".yaml", ".yml", ".txt", ".csv")


def rewrite_citations(targets, moved, old_root, new_root):
    """Rewrite every mention of an old path in the text files under
    `targets` (folders or files): a moved file to its new path, a moved folder
    as a prefix, and anything else under the old folder to the new one.
    Whole paths only (`raw/web/about.md`, never `raw/web/about.md.bak`).
    Returns the files changed."""
    pairs = sorted(list(moved.items()) + [(old_root, new_root)], key=lambda kv: -len(kv[0]))
    pattern = re.compile(r"(?<![\w./-])(%s)(?=/|[^\w./-]|\.(?:\s|$)|$)" % "|".join(re.escape(o) for o, _ in pairs))
    lookup = dict(pairs)
    changed = []
    files = []
    for t in targets:
        if os.path.isfile(t):
            files.append(t)
        for dirpath, dirnames, filenames in os.walk(t):
            dirnames[:] = [d for d in dirnames if d not in (".git", "node_modules", "raw")]
            files.extend(os.path.join(dirpath, f) for f in filenames)
    for path in files:
        if not path.endswith(TEXT_EXTS):
            continue
        try:
            with open(path) as f:
                text = f.read()
        except (OSError, UnicodeDecodeError):
            continue
        new = pattern.sub(lambda m: lookup[m.group(1)], text)
        if new != text:
            with open(path, "w") as f:
                f.write(new)
            changed.append(path)
    return changed


def run_relayout(args):
    old = args.folder.rstrip("/")
    if not paths.old_layout(old):
        sys.stderr.write("%s is not a folder an earlier tt-crawl wrote (no _manifest.json or _inventory.json)\n" % old)
        return 2
    try:
        with open(os.path.join(old, "_manifest.json")) as f:
            start = json.load(f).get("start")
    except (OSError, ValueError):
        with open(os.path.join(old, "_inventory.json")) as f:
            start = json.load(f).get("start")
    parent = os.path.dirname(old) or "."
    external = os.path.basename(parent) == "external"
    # raw/web -> raw/site/<host>; raw/external/<host> keeps its name, relaid in place
    new = (args.out or old) if external else (args.out or paths.site_dir(start, root=parent))
    if new == old:
        os.rename(old, old + ".relayout")
        old = old + ".relayout"
    elif os.path.exists(new) and os.listdir(new):
        sys.stderr.write("%s already holds files; pass --out for another folder\n" % new)
        return 2
    root = parent
    shown_old = old[:-len(".relayout")] if old.endswith(".relayout") else old
    moved = relayout(old, new, audit_dir=None if external else paths.audit_dir(start, root=root))
    if shown_old != old:             # relaid in place: the map names the folder as it was called
        moved = {k.replace(old, shown_old, 1): v for k, v in moved.items()}
        with open(os.path.join(new, paths.INDEX, "moved.json"), "w") as f:
            json.dump(moved, f, indent=2)
    rewritten = rewrite_citations(args.rewrite, moved, shown_old, new) if args.rewrite else []
    print(json.dumps({"from": shown_old, "to": new, "moved": len(moved), "map": os.path.join(new, paths.INDEX, "moved.json"),
                      "rewritten": rewritten}))
    return 0


def run_freeze(args):
    if not os.path.isdir(args.folder):
        sys.stderr.write("no such folder: %s\n" % args.folder)
        return 2
    note = paths.freeze(args.folder, args.reason)
    print(json.dumps({"frozen": args.folder, **note}))
    return 0


def add_parser(sub):
    p = sub.add_parser("relayout", help="move a folder an earlier tt-crawl wrote (raw/web) into raw/site/<host>")
    p.add_argument("folder")
    p.add_argument("--out", default=None, help="the new folder (default raw/site/<host> beside the old one)")
    p.add_argument("--rewrite", nargs="*", default=[], metavar="PATH",
                   help="folders or files whose citations of the old paths are rewritten to the new ones (notes, briefs)")
    p.set_defaults(func=run_relayout)
    p = sub.add_parser("freeze", help="keep a site's folder as it is: no crawl writes into it again")
    p.add_argument("folder")
    p.add_argument("--reason", default="", help="why, for whoever finds it frozen (\"the old site, at launch\")")
    p.set_defaults(func=run_freeze)
