"""Chrome's request guard and the choice of screenshot engine, the playbooks
and profiles, importing a collection from a feed, and Firecrawl as a fetcher
(against a stand-in server speaking its API). No network, no browser."""
import http.server
import json
import os
import sys
import tempfile
import threading
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ttcrawl import cdp, chrome, cli, firecrawl, importer, net, playbooks, site  # noqa: E402

LONG = " ".join(["Our roofers replace slate and tile roofs across the county, with a ten year guarantee."] * 6)
SITE = {
    "https://acme.com/": "<html><head><title>Acme</title></head><body><header><nav><a href='/about'>About</a></nav></header>"
                         "<main><h1>Acme</h1><p>%s</p><a href='/blog/one'>One</a> <a href='/blog/two'>Two</a></main></body></html>" % LONG,
    "https://acme.com/about": "<html><body><main><h1>About</h1><p>%s Since 1998.</p></main></body></html>" % LONG,
}
FEED = """<?xml version="1.0"?><rss version="2.0" xmlns:dc="http://purl.org/dc/elements/1.1/"
  xmlns:content="http://purl.org/rss/1.0/modules/content/"><channel>
  <item><title>Post one</title><link>https://acme.com/blog/one</link><pubDate>Tue, 03 Jun 2025 10:00:00 +0000</pubDate>
    <dc:creator>Jane</dc:creator><category>Roofs</category>
    <content:encoded><![CDATA[<p>%s The first post.</p><img src="https://acme.com/crew.jpg" alt="Crew">]]></content:encoded></item>
  <item><title>Post two</title><link>https://acme.com/blog/two</link><pubDate>Mon, 02 Jan 2023 10:00:00 +0000</pubDate>
    <content:encoded><![CDATA[<p>%s The second post, older.</p>]]></content:encoded></item>
</channel></rss>""" % (LONG.replace("roofs", "gutters"), LONG.replace("roofs", "chimneys"))


class GuardTests(unittest.TestCase):
    def test_every_request_is_judged(self):
        sent = []

        class FakeSession:
            def send(self, method, params):
                sent.append((method, params))

        # the resolver's verdicts (net.is_public_host refuses loopback and private ranges)
        g = cdp.RequestGuard(is_public=lambda host: host not in ("intranet.acme.com", "127.0.0.1"))
        for rid, url in enumerate(["https://acme.com/", "http://127.0.0.1:9000/", "https://intranet.acme.com/x",
                                   "data:image/png;base64,AA", "file:///etc/passwd", "http://localhost/"]):
            self.assertTrue(g(FakeSession(), {"method": "Fetch.requestPaused",
                                              "params": {"requestId": str(rid), "request": {"url": url}}}))
        self.assertEqual([m for m, _ in sent], ["Fetch.continueRequest", "Fetch.failRequest", "Fetch.failRequest",
                                                "Fetch.continueRequest", "Fetch.failRequest", "Fetch.failRequest"])
        self.assertFalse(g(FakeSession(), {"method": "Page.loadEventFired", "params": {}}))   # other events pass by

    def test_chrome_is_guarded_and_obscura_guards_itself(self):
        self.assertTrue(chrome.Chrome.needs_guard)
        self.assertFalse(cdp.Obscura.needs_guard)

    def test_the_engine_choice(self):
        saved = (chrome.find_chrome, chrome.can_install_chrome, chrome.install_chrome)
        try:
            chrome.find_chrome = lambda **kw: "/bin/chrome"
            s = chrome.shooter("auto", "/bin/obscura")
            self.assertIs(s.browser_cls, chrome.Chrome)
            chrome.find_chrome = lambda **kw: None
            chrome.can_install_chrome = lambda: False
            self.assertIs(chrome.shooter("auto", "/bin/obscura").browser_cls, cdp.Obscura)   # the fallback
            self.assertIsNone(chrome.shooter("chrome", "/bin/obscura"))
            self.assertIsNone(chrome.shooter("auto", None))
            chrome.can_install_chrome = lambda: True

            def fails(log):
                raise RuntimeError("no network")
            chrome.install_chrome = fails
            with redirect_stderr(StringIO()):
                self.assertIs(chrome.shooter("auto", "/bin/obscura").browser_cls, cdp.Obscura)  # an install failure costs fidelity, not the crawl
        finally:
            chrome.find_chrome, chrome.can_install_chrome, chrome.install_chrome = saved

    def test_missing_libraries_become_packages(self):
        class Proc:
            stdout = "\tlibnss3.so => not found\n\tlibasound.so.2 => not found\n\tlibc.so.6 => /lib/libc.so.6\n\tlibweird.so.9 => not found\n"
        libs = chrome.missing_libraries("/x", run=lambda *a, **k: Proc())
        self.assertEqual(libs, ["libasound.so.2", "libnss3.so", "libweird.so.9"])
        choices, unknown = chrome.packages_for(libs)
        self.assertEqual(choices, [["libasound2t64", "libasound2"], ["libnss3"]])
        self.assertEqual(unknown, ["libweird.so.9"])


class PlaybookTests(unittest.TestCase):
    def test_list_and_print(self):
        self.assertEqual(playbooks.names(), sorted(["audit", "brand", "competitor", "import", "launch", "rebuild",
                                                    "reference", "survey"]))
        for n in playbooks.names():
            text = playbooks.read(n)
            self.assertTrue(text.startswith("# %s:" % n), n)
            self.assertIn("tt-crawl ", text, n)
            self.assertTrue(playbooks.summary(n), n)

    def test_every_command_a_playbook_names_exists(self):
        commands = set(cli.build_parser()._subparsers._group_actions[0].choices)
        for n in playbooks.names():
            for cmd in __import__("re").findall(r"^tt-crawl ([a-z-]+)", playbooks.read(n), __import__("re").M):
                self.assertIn(cmd, commands, "%s names tt-crawl %s" % (n, cmd))

    def test_profiles_carry_their_defaults(self):
        p = cli.build_parser()
        brand = p.parse_args(["brand", "https://a.com/"])
        self.assertEqual((brand.images, brand.styles, brand.screenshots, brand.per_template, brand.profile), ("brand", True, True, 2, "brand"))
        pages = p.parse_args(["pages", "https://a.com/"])
        self.assertEqual((pages.images, pages.max_pages, pages.per_template), ("content", 1000, None))
        ref = p.parse_args(["reference", "https://a.com/"])
        self.assertEqual((ref.external, ref.images, ref.max_pages), (True, "none", 8))


class FakeFetch:
    """net.fetch_once over a dict of URL -> body (bytes or str)."""

    def __init__(self, pages):
        self.pages, self.asked = pages, []

    def __call__(self, url, cap=0, timeout=0, method="GET"):
        self.asked.append(url)
        body = self.pages.get(url)
        if body is None:
            return {"status": 404, "final_url": url, "chain": [], "headers": {}, "body": b"", "truncated": False}
        body = body.encode() if isinstance(body, str) else body
        return {"status": 200, "final_url": url, "chain": [], "headers": {}, "body": b"" if method == "HEAD" else body,
                "truncated": False}


class Harness(unittest.TestCase):
    def setUp(self):
        self.saved = (net.fetch_once, net.fetch_bytes, net.is_public_host, site.time.sleep)
        net.is_public_host = lambda host: True
        site.time.sleep = lambda s: None
        net.fetch_bytes = lambda url, cap, content_types=None, sleep=None: (b"GIF89a\x01\x00\x01\x00" + b"\x00" * 10, "image/gif")

    def tearDown(self):
        net.fetch_once, net.fetch_bytes, net.is_public_host, site.time.sleep = self.saved

    def cli(self, *argv, expect=0):
        out, err = StringIO(), StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            args = cli.build_parser().parse_args(list(argv))
            code = args.func(args)
        self.assertEqual(code, expect, err.getvalue())
        return (json.loads(out.getvalue().strip().splitlines()[-1]) if code == 0 else None), err.getvalue()


class ImportTests(Harness):
    def test_parse_rss_and_atom(self):
        items = importer.parse_feed(FEED, "https://acme.com/feed")
        one = items["https://acme.com/blog/one"]
        self.assertEqual((one["title"], one["author"], one["categories"]), ("Post one", "Jane", ["Roofs"]))
        self.assertIn("<img", one["html"])
        atom = importer.parse_feed('<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>A</title>'
                                   '<link rel="alternate" href="/a"/><published>2025-01-02T00:00:00Z</published>'
                                   '<author><name>Bo</name></author><category term="News"/><content>&lt;p&gt;x&lt;/p&gt;</content>'
                                   '</entry></feed>', "https://acme.com/atom.xml")
        self.assertEqual(atom["https://acme.com/a"]["author"], "Bo")
        self.assertEqual(importer._iso("Tue, 03 Jun 2025 10:00:00 +0000"), "2025-06-03")

    def test_shopify_products(self):
        products = {"products": [{"handle": "boot", "title": "Boot", "vendor": "Acme", "product_type": "Shoes",
                                  "tags": "leather, brown", "published_at": "2025-03-01T00:00:00Z", "body_html": "<p>A boot.</p>",
                                  "variants": [{"price": "120.00"}, {"price": "99.00"}],
                                  "images": [{"src": "https://cdn.shopify.com/boot.jpg", "alt": None}]}]}
        fetch = FakeFetch({"https://acme.com/products.json?limit=250&page=1": json.dumps(products)})
        items = importer.from_shopify("https://acme.com/", fetch=fetch)
        boot = items["https://acme.com/products/boot"]
        self.assertEqual(boot["front"], {"vendor": "Acme", "product_type": "Shoes", "tags": ["leather", "brown"], "price": "99.0-120.0"})
        self.assertIn('<img src="https://cdn.shopify.com/boot.jpg" alt="Boot">', boot["html"])

    def test_import_a_collection_from_the_feed(self):
        pages = dict(SITE, **{"https://acme.com/feed": FEED})
        net.fetch_once = FakeFetch(pages)
        with tempfile.TemporaryDirectory() as out:
            self.cli("survey", "https://acme.com/", "--out", out, "--static", "--ignore-robots", "--no-sitemap", "--delay", "0",
                     "--per-template", "0")
            with open(os.path.join(out, "_index", "templates.json")) as f:
                blog = next(t for t in json.load(f) if t["examples"][0].startswith("https://acme.com/blog/"))
            summary, err = self.cli("import", "--out", out, "--template", blog["template"], "--since", "2024-01-01",
                                    "--static", "--delay", "0")
            self.assertIn("1 pages (rss 1)", err)                       # the 2023 post stays out
            with open(os.path.join(out, "pages", "blog--one.md")) as f:
                text = f.read()
            self.assertIn('date: "2025-06-03"', text)
            self.assertIn('author: "Jane"', text)
            self.assertIn('categories: ["Roofs"]', text)
            self.assertIn('fetcher: "rss"', text)
            self.assertIn("](../images/crew-", text)                   # its picture, fetched like any other
            self.assertFalse(os.path.exists(os.path.join(out, "pages", "blog--two.md")))
            with open(os.path.join(out, "_index", "run.json")) as f:
                self.assertEqual(json.load(f)["import"]["by_source"], {"rss": 1})

    def test_import_names_the_collections_when_the_template_is_wrong(self):
        net.fetch_once = FakeFetch(SITE)
        with tempfile.TemporaryDirectory() as out:
            self.cli("survey", "https://acme.com/", "--out", out, "--static", "--ignore-robots", "--no-sitemap", "--delay", "0")
            _, err = self.cli("import", "--out", out, "--template", "product", "--static", expect=2)
            self.assertIn("no pages of template 'product'", err)

    def test_a_page_read_before_is_imported_again_not_called_its_own_duplicate(self):
        pages = dict(SITE, **{"https://acme.com/feed": FEED,
                              "https://acme.com/blog/one": "<html><body><main><h1>Post one</h1><p>%s The first post.</p></main></body></html>"
                              % LONG.replace("roofs", "gutters")})
        net.fetch_once = FakeFetch(pages)
        with tempfile.TemporaryDirectory() as out:
            self.cli("survey", "https://acme.com/", "--out", out, "--static", "--ignore-robots", "--no-sitemap", "--delay", "0")
            self.assertTrue(os.path.isfile(os.path.join(out, "pages", "blog--one.md")))
            with open(os.path.join(out, "_index", "templates.json")) as f:
                blog = next(t for t in json.load(f) if t["examples"][0].startswith("https://acme.com/blog/"))
            _, err = self.cli("import", "--out", out, "--template", blog["template"], "--since", "2024-01-01", "--static", "--delay", "0")
            self.assertIn("1 pages (rss 1)", err)
            with open(os.path.join(out, "pages", "blog--one.md")) as f:
                self.assertIn('fetcher: "rss"', f.read())


class FakeFirecrawl(http.server.BaseHTTPRequestHandler):
    status = 200
    seen = []

    def log_message(self, *a):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        FakeFirecrawl.seen.append((self.path, self.headers.get("Authorization"), body))
        if FakeFirecrawl.status != 200:
            self.send_response(FakeFirecrawl.status)
            self.end_headers()
            return
        if self.path == "/v2/map":
            out = {"success": True, "links": [{"url": "https://acme.com/about"}, {"url": "https://other.com/x"}]}
        else:
            html = SITE.get(body["url"])
            out = {"success": True, "data": {"rawHtml": html or "", "metadata": {"statusCode": 200 if html else 404,
                                                                                   "url": body["url"]}}}
        data = json.dumps(out).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


class FirecrawlTests(Harness):
    def setUp(self):
        super().setUp()
        self.server = http.server.HTTPServer(("127.0.0.1", 0), FakeFirecrawl)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.env = {k: os.environ.get(k) for k in ("FIRECRAWL_API_KEY", "FIRECRAWL_API_URL")}
        os.environ["FIRECRAWL_API_KEY"] = "fc-test"
        os.environ["FIRECRAWL_API_URL"] = "http://127.0.0.1:%d" % self.server.server_port
        FakeFirecrawl.status, FakeFirecrawl.seen = 200, []
        net.fetch_once = FakeFetch({})               # nothing is fetched here: Firecrawl does it

    def tearDown(self):
        self.server.shutdown()
        for k, v in self.env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        super().tearDown()

    def test_pages_come_through_firecrawl(self):
        with tempfile.TemporaryDirectory() as out:
            summary, _ = self.cli("site", "https://acme.com/", "--out", out, "--fetcher", "firecrawl", "--ignore-robots",
                                  "--no-sitemap", "--delay", "0", "--images", "none")
            self.assertEqual(summary["pages"], 2)
            self.assertEqual([p for p, _, _ in FakeFirecrawl.seen][:1], ["/v2/map"])
            self.assertEqual(FakeFirecrawl.seen[0][1], "Bearer fc-test")
            self.assertEqual(net.fetch_once.asked, [])                # not one request of our own to the site
            with open(os.path.join(out, "pages", "about.md")) as f:
                self.assertIn('fetcher: "firecrawl"', f.read())
            with open(os.path.join(out, "_index", "run.json")) as f:
                # the home page and about kept; the two blog links it found answered 404
                self.assertEqual(json.load(f)["firecrawl_calls"], {"scrape": 4, "map": 1})

    def test_no_credits_stops_the_crawl(self):
        FakeFirecrawl.status = 402
        with tempfile.TemporaryDirectory() as out:
            _, err = self.cli("site", "https://acme.com/", "--out", out, "--fetcher", "firecrawl", "--ignore-robots",
                              "--no-sitemap", "--delay", "0", expect=3)
            self.assertIn("out of credits", err)

    def test_no_key_says_how_to_get_one(self):
        os.environ.pop("FIRECRAWL_API_KEY")
        with tempfile.TemporaryDirectory() as out:
            _, err = self.cli("site", "https://acme.com/", "--out", out, "--fetcher", "firecrawl", expect=3)
            self.assertIn('request_connection("firecrawl"', err)

    def test_throttling_is_waited_out(self):
        waits = []
        client = firecrawl.Client("fc-test", os.environ["FIRECRAWL_API_URL"], sleep=waits.append)
        FakeFirecrawl.status = 429
        with self.assertRaises(firecrawl.FirecrawlError) as e:
            client.scrape("https://acme.com/")
        self.assertFalse(e.exception.fatal)
        self.assertEqual(len(waits), net.MAX_RETRIES)


if __name__ == "__main__":
    unittest.main()
