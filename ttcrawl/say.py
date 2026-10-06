"""How every command speaks, in one shape: `tt-crawl <command>: what
happened`, the details indented, then `Next:`; a refusal on stderr ending in
`Try:`. `--json` prints the result as one JSON line instead, and a refusal
as one JSON object on stderr."""
import argparse
import json
import sys


class Parser(argparse.ArgumentParser):
    """argparse, with misuse said as a refusal: what was wrong, the valid
    flags, and `Try: ... --help` (exit 2)."""

    commands = {}

    def parse_args(self, args=None, namespace=None):
        # A stray flag after a command is that command's misuse, said as its own.
        ns, extras = self.parse_known_args(args, namespace)
        if extras:
            self.commands.get(getattr(ns, "command", None), self).error("unrecognized arguments: " + " ".join(extras))
        return ns

    def error(self, message):
        flags = sorted({o for a in self._actions for o in a.option_strings if o.startswith("--")})
        sys.stderr.write("%s: %s\n" % (self.prog, message)
                         + ("  valid: %s\n" % ", ".join(flags) if message.startswith("unrecognized") else "")
                         + "  Try: %s --help\n" % self.prog)
        sys.exit(2)


def command(sub, name, help, output, with_json=True):
    """A subcommand: `--help` says what it does, then what it prints."""
    p = sub.add_parser(name, help=help, description=help[0].upper() + help[1:] + ".", epilog=output)
    if with_json:
        p.add_argument("--json", action="store_true", help="the result as one JSON line (a refusal as JSON on stderr)")
    return p


def fail(args, code, what, try_cmd, *details):
    """A refusal or failure on stderr; returns `code`."""
    if getattr(args, "json", False):
        sys.stderr.write(json.dumps({"error": what, "details": list(details), "try": try_cmd}) + "\n")
    else:
        sys.stderr.write("tt-crawl %s: %s\n" % (args.command, what)
                         + "".join("  %s\n" % d for d in details) + "  Try: %s\n" % try_cmd)
    return code


def done(args, data, what, lines=(), next=None):
    """The result on stdout: text, or `data` as one JSON line under --json."""
    if getattr(args, "json", False):
        print(json.dumps(data))
        return
    out = ["tt-crawl %s: %s" % (args.command, what)] + ["  " + l for l in lines if l]
    if next:
        out += ["", "Next: " + next]
    print("\n".join(out))


def count(n, word, plural=None):
    return "%d %s" % (n, word if n == 1 else plural or word + "s")
