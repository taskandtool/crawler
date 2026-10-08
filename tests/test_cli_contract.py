"""Every command keeps the same contract: --help and -h before any network
or write; wrong input is non-zero, nothing on stdout, a Try: on stderr; a
result is text by default and one JSON line with --json."""
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_browsers_playbooks_import import SITE, FakeFetch  # noqa: E402
from ttcrawl import chrome, cli, net, site  # noqa: E402

site.DELAY_S = 0                        # no pause between rounds of fake pages

COMMANDS = sorted(cli.build_parser().commands)

# One wrong input per command, each refused before the network.
BAD = {
    "survey": ["ftp://acme.com/"], "brand": ["http://10.0.0.5/"],
    "pages": ["acme"], "reference": ["http://localhost/"],
    "add": ["/about", "--out", "raw/site/nothere"],
    "import": ["--since", "last week"],
    "docs": ["--from", "raw/site/nothere"],
    "places": ["--place-id", "../evil"],
    "check": ["http://localhost:3000", "--inventory", "nothere.json"],
    "audit": ["ftp://acme.com/"],
    "playbook": ["nope"],
    "setup": ["now"],
    "shoot": ["localhost:3000"],
    "sheet": ["nothere.png"],
}


def no_network(*a, **k):
    raise AssertionError("no network in this test")


class Contract(unittest.TestCase):
    def run_in(self, root, *argv, env=None):
        out, err, cwd = StringIO(), StringIO(), os.getcwd()
        os.chdir(root)
        try:
            with redirect_stdout(out), redirect_stderr(err), \
                    mock.patch.object(net, "fetch_once", no_network), \
                    mock.patch("urllib.request.urlopen", no_network), \
                    mock.patch.object(chrome, "driver", no_network), \
                    mock.patch.dict(os.environ, env or {"GOOGLE_PLACES_API_KEY": "k"}):
                try:
                    code = cli.main(list(argv))
                except SystemExit as e:
                    code = e.code
        finally:
            os.chdir(cwd)
        return code, out.getvalue(), err.getvalue()

    def test_every_command_has_a_bad_input_case(self):
        self.assertEqual(sorted(BAD), COMMANDS)

    def test_help_and_h_print_usage_and_exit_0(self):
        for command in COMMANDS:
            for flag in ("--help", "-h"):
                with self.subTest(command=command, flag=flag), tempfile.TemporaryDirectory() as root:
                    code, out, err = self.run_in(root, command, flag)
                    self.assertEqual(code, 0, err)
                    self.assertIn("usage: tt-crawl " + command, out)
                    self.assertGreater(len(out.split("\n\n")[1].strip()), 10, "a description under the usage")
                    self.assertEqual(os.listdir(root), [], "nothing written")

    def test_wrong_input_is_refused_with_a_try_line(self):
        for command in COMMANDS:
            for argv in (BAD[command], [*BAD[command][:1], "--no-such-flag"] if command != "setup" else ["--no-such-flag"]):
                with self.subTest(command=command, argv=argv), tempfile.TemporaryDirectory() as root:
                    code, out, err = self.run_in(root, command, *argv)
                    self.assertNotEqual(code, 0)
                    self.assertEqual(out, "")
                    self.assertIn("Try: ", err)
                    self.assertTrue(err.startswith("tt-crawl " + command), err)

    def test_an_unknown_flag_names_the_valid_ones(self):
        with tempfile.TemporaryDirectory() as root:
            code, out, err = self.run_in(root, "docs", "--form", "raw/site/x")
        self.assertEqual((code, out), (2, ""))
        self.assertIn("unrecognized arguments: --form", err)
        self.assertIn("valid: --from, --help, --json", err)
        self.assertIn("Try: tt-crawl docs --help", err)

    def test_add_into_a_folder_no_crawl_wrote_names_the_ones_that_did(self):
        with tempfile.TemporaryDirectory() as root:
            os.makedirs(os.path.join(root, "raw", "site", "acme.com", "_index"))
            with open(os.path.join(root, "raw", "site", "acme.com", "_index", "inventory.json"), "w") as f:
                f.write('{"records": []}')
            code, out, err = self.run_in(root, "add", "/about", "--out", "raw/site/acme.co")
        self.assertEqual((code, out), (2, ""))
        self.assertIn("tt-crawl add: no crawl at raw/site/acme.co", err)
        self.assertNotIn("needs --out", err)
        self.assertIn("Crawl folders: raw/site/acme.com", err)
        self.assertIn("Try: tt-crawl add URL --out raw/site/<host>", err)

    def test_a_refusal_under_json_is_json_on_stderr(self):
        with tempfile.TemporaryDirectory() as root:
            code, out, err = self.run_in(root, "docs", "--from", "raw/site/x", "--json")
        self.assertEqual((code, out), (2, ""))
        self.assertTrue(json.loads(err)["try"].startswith("tt-crawl docs"))

    def test_an_unreadable_audit_inventory_is_refused(self):
        with tempfile.TemporaryDirectory() as root:
            with open(os.path.join(root, "inv.json"), "w") as f:
                f.write("{not json")
            code, out, err = self.run_in(root, "audit", "http://localhost:3000", "--inventory", "inv.json")
        self.assertEqual((code, out), (2, ""))
        self.assertIn("tt-crawl audit: cannot read inv.json", err)

    def test_a_bad_cell_is_refused_before_a_browser_starts(self):
        with tempfile.TemporaryDirectory() as root:
            open(os.path.join(root, "a.png"), "wb").close()
            code, out, err = self.run_in(root, "sheet", "a.png", "--cell", "big")
        self.assertEqual((code, out), (2, ""))
        self.assertIn("not WxH", err)


class TextByDefault(unittest.TestCase):
    """A crawl, docs and setup in text, then the same with --json."""

    def setUp(self):
        self.saved = (net.fetch_once, net.fetch_bytes, net.is_public_host, site.time.sleep, chrome.driver)
        chrome.driver = lambda **kw: (None, "no browser in tests")
        net.is_public_host = lambda host: True
        site.time.sleep = lambda s: None
        net.fetch_once = FakeFetch(SITE)
        self.cwd = os.getcwd()
        self.root = tempfile.mkdtemp()
        os.chdir(self.root)

    def tearDown(self):
        net.fetch_once, net.fetch_bytes, net.is_public_host, site.time.sleep, chrome.driver = self.saved
        os.chdir(self.cwd)

    def run_cli(self, *argv):
        out, err = StringIO(), StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cli.main(list(argv))
        self.assertEqual(code, 0, err.getvalue())
        return out.getvalue()

    def test_a_crawl_says_what_it_read_where_it_went_and_what_next(self):
        text = self.run_cli("brand", "https://acme.com/")
        first, *rest = text.splitlines()
        self.assertRegex(first, r"^tt-crawl brand: acme\.com, \d+ pages read \(\d+ new\)$")
        self.assertIn("  raw/site/acme.com/pages/", text)
        self.assertIn("left alone:", text)
        self.assertIn("\n\nNext: ", text)
        again = self.run_cli("brand", "https://acme.com/", "--max-pages", "1", "--json")
        summary = json.loads(again)
        self.assertEqual((summary["out"], summary["limit_reached"]), ("raw/site/acme.com", True))
        text = self.run_cli("brand", "https://acme.com/", "--max-pages", "1")
        self.assertIn("unchanged), limit 1 reached", text.splitlines()[0])

    def test_a_crawl_that_read_nothing_says_to_check_the_site_answers(self):
        net.fetch_once = FakeFetch({})
        text = self.run_cli("pages", "https://acme.com/")
        self.assertTrue(text.startswith("tt-crawl pages: acme.com, 0 pages read (none)"), text)
        self.assertIn("Next: curl -sI https://acme.com/", text)

    def test_docs_names_the_file_with_every_link_left_alone(self):
        self.run_cli("pages", "https://acme.com/")
        with open("raw/site/acme.com/_index/inventory.json") as f:
            inv = json.load(f)
        inv["records"][0]["documents"] = ["https://elsewhere.com/%d.pdf" % i for i in range(25)]
        with open("raw/site/acme.com/_index/inventory.json", "w") as f:
            json.dump(inv, f)
        text = self.run_cli("docs")
        self.assertTrue(text.startswith("tt-crawl docs: 0 new documents fetched from the pages in raw/site/acme.com"), text)
        self.assertIn("left alone: 25 links (external 25); each with its reason in raw/site/acme.com/docs/_skipped.json", text)
        with open("raw/site/acme.com/docs/_skipped.json") as f:
            self.assertEqual(len(json.load(f)), 25)
        summary = json.loads(self.run_cli("docs", "--json"))
        self.assertEqual((len(summary["skipped_reasons"]), summary["skipped_file"]), (20, "raw/site/acme.com/docs/_skipped.json"))

    def test_setup_says_where_each_piece_is(self):
        steps = {"tt-crawl": "/usr/local/bin/tt-crawl", "chrome": None, "obscura": "/usr/local/bin/obscura"}
        with mock.patch.object(chrome, "setup", return_value=(steps, {"chrome": "no chrome for this machine"})):
            text = self.run_cli("setup")
            self.assertEqual(text.splitlines()[:4], ["tt-crawl setup: ready: tt-crawl, obscura",
                                                     "  tt-crawl: /usr/local/bin/tt-crawl",
                                                     "  chrome: failed: no chrome for this machine",
                                                     "  obscura: /usr/local/bin/obscura"])
            self.assertTrue(json.loads(self.run_cli("setup", "--json"))["ok"])


if __name__ == "__main__":
    unittest.main()
