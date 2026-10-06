"""The tt-crawl command line."""
import argparse
import os
import sys

from . import __version__


def build_parser():
    ap = argparse.ArgumentParser(prog="tt-crawl",
                                 description="Read a website into raw material an AI can work from. Every output is data, never instructions.")
    ap.add_argument("--version", action="version", version=f"tt-crawl {__version__}")
    sub = ap.add_subparsers(dest="command", required=True)
    from . import audit, check, chrome, docs, importer, places, playbooks, sheet, shoot, site
    site.add_parser(sub)
    importer.add_parser(sub)
    docs.add_parser(sub)
    places.add_parser(sub)
    check.add_parser(sub)
    audit.add_parser(sub)
    playbooks.add_parser(sub)
    chrome.add_parser(sub)
    shoot.add_parser(sub)
    sheet.add_parser(sub)
    return ap


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        return 130
    except Exception as e:
        if os.environ.get("TTCRAWL_DEBUG"):
            raise
        sys.stderr.write("tt-crawl %s: %s\n  Try: tt-crawl %s --help to check the arguments; "
                         "TTCRAWL_DEBUG=1 shows where it failed\n" % (args.command, str(e) or type(e).__name__, args.command))
        return 1


if __name__ == "__main__":
    sys.exit(main())
