"""Chrome's request guard and the choice of browser, the playbooks and
profiles, and importing a collection from a feed or a WordPress site. No
network, no browser."""
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ttcrawl import browser, cdp, chrome, cli, importer, net, playbooks, site  # noqa: E402

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

    def test_the_browser_choice(self):
        saved = (chrome.find_chrome, chrome.can_install_chrome, chrome.install_chrome, browser.find_obscura)
        try:
            chrome.find_chrome = lambda **kw: "/bin/chrome"
            browser.find_obscura = lambda **kw: "/bin/obscura"
            d, note = chrome.driver("chrome")
            self.assertEqual((d.browser_cls, d.engine, note), (chrome.Chrome, "chrome", None))
            self.assertEqual(chrome.driver("obscura")[0].browser_cls, cdp.Obscura)
            chrome.find_chrome = lambda **kw: None
            chrome.can_install_chrome = lambda: False
            d, note = chrome.driver("chrome")
            self.assertIs(d.browser_cls, cdp.Obscura)                                   # the fallback, said
            self.assertIn("obscura used instead", note)
            chrome.can_install_chrome = lambda: True

            def fails(log):
                raise RuntimeError("no network")
            chrome.install_chrome = fails
            d, note = chrome.driver("chrome")
            self.assertIs(d.browser_cls, cdp.Obscura)              # an install failure costs fidelity, not the crawl
            self.assertIn("no network", note)
            browser.find_obscura = lambda **kw: None
            d, note = chrome.driver("obscura")
            self.assertIsNone(d)
            self.assertIn("obscura is not installed", note)
        finally:
            chrome.find_chrome, chrome.can_install_chrome, chrome.install_chrome, browser.find_obscura = saved

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
        self.assertEqual(playbooks.names(), ["brand", "competitor", "import", "launch", "rebuild", "reference", "survey"])
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
        self.assertEqual((pages.browser, pages.static), ("chrome", False))       # chrome reads pages by default


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
        self.saved_driver = chrome.driver
        chrome.driver = lambda choice, **kw: (None, "no browser in tests")
        net.is_public_host = lambda host: True
        site.time.sleep = lambda s: None
        net.fetch_bytes = lambda url, cap, content_types=None, sleep=None: (b"GIF89a\x01\x00\x01\x00" + b"\x00" * 10, "image/gif")

    def tearDown(self):
        net.fetch_once, net.fetch_bytes, net.is_public_host, site.time.sleep = self.saved
        chrome.driver = self.saved_driver

    def cli(self, *argv, expect=0):
        out, err = StringIO(), StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            args = cli.build_parser().parse_args(list(argv))
            code = args.func(args)
        self.assertEqual(code, expect, err.getvalue())
        return (json.loads(out.getvalue().strip().splitlines()[-1]) if code == 0 else None), err.getvalue()


class SetupTests(unittest.TestCase):
    def test_a_failed_step_is_reported_and_the_rest_still_run(self):
        def broken(log):
            raise RuntimeError("no build for this machine\nmore detail")
        done, errors = chrome.setup(log=lambda m: None, steps=(
            ("tt-crawl", lambda log: "/usr/local/bin/tt-crawl"), ("chrome", broken),
            ("obscura", lambda log: "/usr/local/bin/obscura")))
        self.assertEqual(done, {"tt-crawl": "/usr/local/bin/tt-crawl", "chrome": None,
                                "obscura": "/usr/local/bin/obscura"})
        self.assertEqual(errors, {"chrome": "no build for this machine"})

    def test_the_launcher_is_left_alone_when_tt_crawl_is_on_the_path(self):
        self.assertEqual(chrome.install_launcher(which=lambda n: "/home/u/.local/bin/tt-crawl"),
                         "/home/u/.local/bin/tt-crawl")


class ReferenceProfileTests(unittest.TestCase):
    def test_reference_reads_a_few_pages_of_someone_elses_site_for_its_look(self):
        a = cli.build_parser().parse_args(["reference", "https://site-they-like.com"])
        self.assertEqual((a.profile, a.external, a.max_pages, a.images, a.styles, a.screenshots, a.per_template),
                         ("reference", True, 8, "none", True, True, 1))


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
            self.cli("survey", "https://acme.com/", "--out", out, "--static", "--delay", "0")
            with open(os.path.join(out, "_index", "templates.json")) as f:
                blog = next(t for t in json.load(f) if t["examples"][0].startswith("https://acme.com/blog/"))
            summary, err = self.cli("import", "--out", out, "--template", blog["template"], "--since", "2024-01-01",
                                    "--static", "--images", "content")   # a survey fetches none; this asks for them
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
            self.cli("survey", "https://acme.com/", "--out", out, "--static", "--delay", "0")
            _, err = self.cli("import", "--out", out, "--template", "product", "--static", expect=2)
            self.assertIn("no pages of template 'product'", err)

    def test_a_page_read_before_is_imported_again_not_called_its_own_duplicate(self):
        pages = dict(SITE, **{"https://acme.com/feed": FEED,
                              "https://acme.com/blog/one": "<html><body><main><h1>Post one</h1><p>%s The first post.</p></main></body></html>"
                              % LONG.replace("roofs", "gutters")})
        net.fetch_once = FakeFetch(pages)
        with tempfile.TemporaryDirectory() as out:
            self.cli("survey", "https://acme.com/", "--out", out, "--static", "--delay", "0")
            self.assertTrue(os.path.isfile(os.path.join(out, "pages", "blog--one.md")))
            with open(os.path.join(out, "_index", "templates.json")) as f:
                blog = next(t for t in json.load(f) if t["examples"][0].startswith("https://acme.com/blog/"))
            _, err = self.cli("import", "--out", out, "--template", blog["template"], "--since", "2024-01-01", "--static")
            self.assertIn("1 pages (rss 1)", err)
            with open(os.path.join(out, "pages", "blog--one.md")) as f:
                self.assertIn('fetcher: "rss"', f.read())


    def test_a_wordpress_site_imports_every_post_and_page_with_no_template(self):
        api = "https://acme.com/wp-json/wp/v2/"
        post = {"id": 1, "link": "https://acme.com/2025/06/storm-season/", "date": "2025-06-03T10:00:00", "author": 7,
                "categories": [3], "title": {"rendered": "Storm season"},
                "content": {"rendered": "<p>%s Storms.</p>" % LONG.replace("roofs", "storms")}}
        page = {"id": 2, "link": "https://acme.com/warranty/", "date": "2024-01-02T00:00:00", "author": 7, "categories": [],
                "title": {"rendered": "Warranty"}, "content": {"rendered": "<p>%s Ten years.</p>" % LONG.replace("roofs", "warranty")}}
        wp = {api + "posts?per_page=1": json.dumps([{"id": 1}]),
              api + "users?per_page=100&page=1&_embed=0": json.dumps([{"id": 7, "name": "Jane"}]),
              api + "categories?per_page=100&page=1&_embed=0": json.dumps([{"id": 3, "name": "News"}]),
              api + "posts?per_page=100&page=1&_embed=0": json.dumps([post]),
              api + "pages?per_page=100&page=1&_embed=0": json.dumps([page])}
        net.fetch_once = FakeFetch(dict(SITE, **wp))
        with tempfile.TemporaryDirectory() as root:
            out = os.path.join(root, "raw", "site", "acme.com")
            self.cli("brand", "https://acme.com/", "--out", out, "--static", "--delay", "0")
            with open(os.path.join(root, "raw", "site", "_sites.json")) as f:
                self.assertIn('"wp_imported": false', f.read())
            summary, err = self.cli("import", "--out", out, "--source", "wp", "--static")
            self.assertIn("2 pages (wp-rest 2)", err)
            with open(os.path.join(out, "pages", "2025--06--storm-season.md")) as f:
                text = f.read()
            self.assertIn('author: "Jane"', text)
            self.assertIn('categories: ["News"]', text)
            self.assertTrue(os.path.isfile(os.path.join(out, "pages", "warranty.md")))
            with open(os.path.join(root, "raw", "site", "_sites.json")) as f:
                reg = json.load(f)["latest"]
            self.assertEqual((reg["wp_imported"], reg["profile"]), (True, "brand"))   # the folder stays a brand folder
            with open(os.path.join(out, "_index", "run.json")) as f:
                run = json.load(f)
            self.assertEqual((run["command"], run["images"]["mode"]), ("import", "brand"))
            # one collection of a WordPress site is not the whole site imported
            with open(os.path.join(root, "raw", "site", "_sites.json"), "w") as f:
                json.dump({"entries": [dict(reg, wp_imported=False)], "latest": dict(reg, wp_imported=False)}, f)
            self.cli("import", "--out", out, "--template", "/{n}/{n}/*", "--static")
            with open(os.path.join(root, "raw", "site", "_sites.json")) as f:
                self.assertFalse(json.load(f)["latest"]["wp_imported"])
            # a source that is not WordPress imports one collection only
            _, err = self.cli("import", "--out", out, "--source", "rss", "--static", expect=2)
            self.assertIn("--source rss imports one collection: name it with --template", err)
            # a site with no WordPress API and no --template is told what to pass
            net.fetch_once = FakeFetch(SITE)
            _, err = self.cli("import", "--out", out, "--static", expect=2)
            self.assertIn("needs --template", err)


class StylesTests(Harness):
    def test_styles_no_page_rendered_are_said_to_be_skipped(self):
        net.fetch_once = FakeFetch(SITE)
        with tempfile.TemporaryDirectory() as out:                 # no browser here (chrome.driver stubbed)
            summary, _ = self.cli("site", "https://acme.com/", "--out", out, "--styles", "--delay", "0")
            self.assertEqual(summary["styles_skipped"], "no page rendered in the browser")


if __name__ == "__main__":
    unittest.main()
