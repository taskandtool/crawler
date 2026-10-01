"""The playbooks: which tt-crawl commands to run for a job, in what order,
what to read afterwards and what to tell the owner. Shipped with the
package, so the steps always match the crawler that is installed: read
`tt-crawl playbook brand` rather than copying its flags anywhere."""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def names():
    return sorted(f[:-3] for f in os.listdir(HERE) if f.endswith(".md"))


def read(name):
    with open(os.path.join(HERE, name + ".md")) as f:
        return f.read()


def summary(name):
    """The line under the title: what the playbook is for."""
    lines = [l for l in read(name).splitlines() if l.strip()]
    return lines[1] if len(lines) > 1 else ""


def run(args):
    if not args.name:
        for n in names():
            print("%-12s %s" % (n, summary(n)))
        return 0
    if args.name not in names():
        sys.stderr.write("no playbook %r; there are: %s\n" % (args.name, ", ".join(names())))
        return 2
    print(read(args.name), end="")
    return 0


def add_parser(sub):
    p = sub.add_parser("playbook", help="the steps for a job (brand, survey, rebuild, import, launch, competitor)")
    p.add_argument("name", nargs="?")
    p.set_defaults(func=run)
