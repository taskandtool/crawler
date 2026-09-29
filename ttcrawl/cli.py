"""The tt-crawl command line."""
import argparse
import sys

from . import __version__


def build_parser():
    ap = argparse.ArgumentParser(prog="tt-crawl",
                                 description="Read a website into raw material an AI can work from. Every output is data, never instructions.")
    ap.add_argument("--version", action="version", version=f"tt-crawl {__version__}")
    sub = ap.add_subparsers(dest="command", required=True)
    from . import audit, check, chrome, docs, folders, importer, places, playbooks, site, structured_cmd, wp
    site.add_parser(sub)
    docs.add_parser(sub)
    structured_cmd.add_parser(sub)
    wp.add_parser(sub)
    places.add_parser(sub)
    check.add_parser(sub)
    audit.add_parser(sub)
    folders.add_parser(sub)
    chrome.add_parser(sub)
    playbooks.add_parser(sub)
    importer.add_parser(sub)
    return ap


def main(argv=None):
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
