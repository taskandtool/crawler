import json
import os
import sys
import tempfile
import unittest
import urllib.error
import urllib.request
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fixtures import WP_HTML  # noqa: E402
from ttcrawl import check, cli, importer, net, site  # noqa: E402


def _resp(status, body=b"", headers=None, final=None, chain=None):
    return {"status": status, "body": body, "headers": headers or {}, "final_url": final, "chain": chain or [], "truncated": False}


class WordPressTests(unittest.TestCase):
    def test_detect_in_html(self):
        self.assertEqual(importer.wp_api_link(WP_HTML), "https://blog.example.com/news/wp-json/")
        self.assertIsNone(importer.wp_api_link("<html></html>"))

    def test_api_base_uses_the_page_hint_and_falls_back(self):
        calls = []

        def fetch(url, **kw):
            calls.append(url)
            if url == "https://blog.example.com/news/":
                return _resp(200, WP_HTML.encode())
            if url == "https://blog.example.com/news/wp-json/wp/v2/posts?per_page=1":
                return _resp(200, b'[{"id":1}]')
            return _resp(404)

        self.assertEqual(importer.wp_api_base("https://blog.example.com/news/", fetch=fetch), "https://blog.example.com/news/wp-json")
        self.assertIsNone(importer.wp_api_base("https://plain.example.com/", fetch=lambda u, **k: _resp(404)))

    def test_fetch_all_paginates(self):
        pages = {1: [{"id": 1}], 2: [{"id": 2}]}

        def fetch(url, **kw):
            n = int(url.split("&page=")[1].split("&")[0])
            import json
            return _resp(200, json.dumps(pages.get(n, [])).encode(), {"X-WP-TotalPages": "2"})

        self.assertEqual([i["id"] for i in importer.wp_fetch_all("https://b.com/wp-json", "posts", fetch=fetch)], [1, 2])


class CheckTests(unittest.TestCase):
    def test_the_site_on_this_machine_may_be_checked(self):
        for url in ("http://localhost:3000", "http://localhost", "http://127.0.0.1:8080/x"):
            self.assertTrue(net.local_or_public_http_url(url), url)
        for url in ("https://localhost:3000", "http://localhost.attacker.example", "http://10.0.0.5:3000",
                    "http://169.254.169.254/", "file:///etc/passwd"):
            self.assertFalse(net.local_or_public_http_url(url), url)

    def test_check_runs_against_localhost(self):
        with tempfile.TemporaryDirectory() as root:
            inv = os.path.join(root, "inventory.json")
            with open(inv, "w") as f:
                json.dump({"start": "https://old.example.com/", "records": [
                    {"url": "https://old.example.com/about", "status": 200, "file": "pages/about.md"}]}, f)
            asked = []

            def fetch_once(url, cap=0, timeout=0, method="GET"):
                asked.append(url)
                body = b"<html><head><title>About</title></head><body><h1>About</h1></body></html>"
                return {"status": 200, "final_url": url, "chain": [], "headers": {}, "body": body, "truncated": False}

            saved, cwd = net.fetch_once, os.getcwd()
            net.fetch_once = fetch_once
            os.chdir(root)
            try:
                with redirect_stdout(StringIO()) as out, redirect_stderr(StringIO()):
                    args = cli.build_parser().parse_args(["check", "http://localhost:3000", "--inventory", inv, "--json"])
                    code = args.func(args)
            finally:
                net.fetch_once = saved
                os.chdir(cwd)
            self.assertEqual(code, 0)
            self.assertIn("http://localhost:3000/about", asked)
            self.assertEqual(json.loads(out.getvalue())["ok"], 1)

    def test_a_local_site_redirects_within_itself_only(self):
        guard = net._GuardedRedirect()
        req = urllib.request.Request("http://localhost:3000/old")
        self.assertEqual(guard.redirect_request(req, None, 301, "Moved", {}, "http://localhost:3000/new").full_url,
                         "http://localhost:3000/new")
        for elsewhere in ("http://localhost:5432/", "http://10.0.0.5/", "http://169.254.169.254/latest"):
            with self.assertRaises(urllib.error.URLError):
                guard.redirect_request(req, None, 301, "Moved", {}, elsewhere)
        with self.assertRaises(urllib.error.URLError):     # a public site never redirects onto this machine
            guard.redirect_request(urllib.request.Request("http://93.184.216.34/"), None, 301, "Moved", {},
                                   "http://localhost:3000/")

    def test_map_url(self):
        self.assertEqual(check.map_url("https://old.com/about?x=1", "https://new.com"), "https://new.com/about?x=1")
        self.assertEqual(check.map_url("https://old.com/", "https://new.com"), "https://new.com/")

    def test_verdicts(self):
        self.assertEqual(check.verdict(200, [], False), check.OK)
        self.assertEqual(check.verdict(200, [(301, "x")], False), check.OK)
        self.assertEqual(check.verdict(200, [(301, "x"), (301, "y")], False), check.CHAIN)
        self.assertEqual(check.verdict(200, [], True), check.NOINDEX)
        self.assertEqual(check.verdict(404, [], False), check.MISSING)
        self.assertEqual(check.verdict(410, [], False), check.MISSING)
        self.assertEqual(check.verdict(500, [], False), check.ERROR)
        self.assertEqual(check.verdict(None, [], False), check.ERROR)

    def test_check_url_reads_the_page(self):
        html = b"<html><head><title>New</title><meta name='description' content='d'></head><body><h1>One</h1></body></html>"
        r = check.check_url("https://new.com/a", fetch=lambda u, **k: _resp(200, html, final="https://new.com/a", chain=[(301, "https://new.com/a")]))
        self.assertEqual((r["status"], r["title"], r["h1_count"], r["chain"]), (200, "New", 1, ["https://new.com/a"]))

        def boom(u, **k):
            raise OSError("down")

        self.assertEqual(check.check_url("https://new.com/a", fetch=boom)["status"], None)

    def test_markdown_report(self):
        report = {"new_base": "https://new.com", "rows": [
            {"old_url": "https://old.com/a", "new_url": "https://new.com/a", "verdict": check.OK, "status": 200, "chain": [],
             "final_url": "https://new.com/a", "title": "A", "old_title": "A", "findings": ["no description"], "error": None},
            {"old_url": "https://old.com/b", "new_url": "https://new.com/b", "verdict": check.MISSING, "status": 404, "chain": [],
             "final_url": None, "title": "", "old_title": "B", "findings": [], "error": None}],
            "counts": {check.OK: 1, check.MISSING: 1, check.CHAIN: 0, check.NOINDEX: 0, check.ERROR: 0},
            "sitemap": {"fetched": True, "entries": 1, "missing_from_sitemap": ["/a"], "new_in_sitemap": []},
            "home": {"status": 200, "jsonld_types": ["LocalBusiness"], "title": "New"}}
        md = check.markdown(report)
        self.assertTrue(md.splitlines()[6].startswith("| https://old.com/b | missing"))
        self.assertIn("missing from the sitemap: /a", md)
        self.assertIn("LocalBusiness", md)


class RefusalTests(unittest.TestCase):
    """Wrong input says what was wrong and what works, on stderr, never exit 0."""

    def run_in(self, root, *argv):
        out, err, cwd = StringIO(), StringIO(), os.getcwd()
        os.chdir(root)
        try:
            with redirect_stdout(out), redirect_stderr(err):
                code = cli.main(list(argv))
        finally:
            os.chdir(cwd)
        return code, out.getvalue(), err.getvalue()

    def test_docs_from_a_folder_no_crawl_wrote(self):
        with tempfile.TemporaryDirectory() as root:
            os.makedirs(os.path.join(root, "raw", "site", "acme.com", "_index"))
            with open(os.path.join(root, "raw", "site", "acme.com", "_index", "inventory.json"), "w") as f:
                f.write('{"records": []}')
            code, out, err = self.run_in(root, "docs", "--from", "raw/site/acme.co")
            self.assertEqual((code, out), (2, ""))
            self.assertIn("docs: no crawl at raw/site/acme.co", err)
            self.assertIn("raw/site/acme.com", err)                   # the folders that do exist
            self.assertIn("Try:", err)
            self.assertFalse(os.path.exists(os.path.join(root, "raw", "site", "_sites.json")))   # nothing registered

    def test_docs_names_only_folders_that_hold_a_crawl(self):
        with tempfile.TemporaryDirectory() as root:
            os.makedirs(os.path.join(root, "raw", "site", "x.com"))
            code, out, err = self.run_in(root, "docs", "--from", "raw/site/x.com")
            self.assertEqual((code, out), (2, ""))
            self.assertIn("Crawl folders: none", err)

    def test_docs_refuses_an_unreadable_inventory(self):
        with tempfile.TemporaryDirectory() as root:
            os.makedirs(os.path.join(root, "raw", "site", "acme.com", "_index"))
            with open(os.path.join(root, "raw", "site", "acme.com", "_index", "inventory.json"), "w") as f:
                f.write("{not json")
            code, out, err = self.run_in(root, "docs", "--from", "raw/site/acme.com")
            self.assertEqual((code, out), (2, ""))
            self.assertIn("docs: cannot read raw/site/acme.com/_index/inventory.json", err)
            self.assertIn("Try:", err)

    def test_a_refused_start_names_the_command_that_was_run(self):
        with tempfile.TemporaryDirectory() as root:
            code, out, err = self.run_in(root, "brand", "http://10.0.0.5/")
            self.assertEqual((code, out), (2, ""))
            self.assertIn("Try: tt-crawl brand https://theirsite.com", err)

    def test_check_names_the_inventory_it_could_not_find(self):
        with tempfile.TemporaryDirectory() as root:
            code, out, err = self.run_in(root, "check", "http://localhost:3000", "--inventory", "/nonexistent.json")
            self.assertEqual((code, out), (2, ""))
            self.assertIn("no inventory at /nonexistent.json", err)
            self.assertIn("Try:", err)

    def test_audit_refusals_are_on_stderr(self):
        with tempfile.TemporaryDirectory() as root:
            code, out, err = self.run_in(root, "audit", "http://10.0.0.5/")
            self.assertEqual((code, out), (2, ""))
            self.assertIn("neither a public", err)
            code, out, err = self.run_in(root, "audit", "http://localhost:3000", "--inventory", "/nonexistent.json")
            self.assertEqual((code, out), (2, ""))
            self.assertIn("no inventory at /nonexistent.json", err)

    def test_an_unexpected_failure_is_one_line_not_a_traceback(self):
        saved = site.run

        def fails(args):
            raise PermissionError(13, "Permission denied", "/read-only/acme.com")
        try:
            site.run = fails
            with tempfile.TemporaryDirectory() as root:
                code, out, err = self.run_in(root, "site", "https://acme.com/")
        finally:
            site.run = saved
        self.assertEqual(code, 1)
        self.assertTrue(err.startswith("tt-crawl site: [Errno 13] Permission denied: '/read-only/acme.com'"), err)
        self.assertIn("Try: tt-crawl site --help", err)
        self.assertNotIn("Traceback", err)

    def test_an_interrupt_exits_130_quietly(self):
        saved = site.run

        def interrupted(args):
            raise KeyboardInterrupt
        try:
            site.run = interrupted
            with tempfile.TemporaryDirectory() as root:
                self.assertEqual(self.run_in(root, "site", "https://acme.com/"), (130, "", ""))
        finally:
            site.run = saved


if __name__ == "__main__":
    unittest.main()
