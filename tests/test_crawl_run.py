"""The crawl loop end to end on fake responses (no network, no browser):
throttled answers waited out, thin pages kept, a re-crawl refreshing pages
in place; and the full-page screenshot plumbing on fakes."""
import json
import os
import socket
import struct
import sys
import tempfile
import unittest
import zipfile
from contextlib import redirect_stderr, redirect_stdout
from io import BytesIO, StringIO
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ttcrawl import cdp, chrome, net, site  # noqa: E402

site.DELAY_S = 0                        # no pause between rounds of fake pages

LONG = " ".join(["Our roofers replace slate and tile roofs across the county, with a ten year guarantee."] * 6)
PAGES = {
    "https://acme.com/": f"<html><head><title>Acme</title></head><body><header><nav><a href='/'>Home</a> <a href='/services'>Services</a> <a href='/contact'>Contact</a></nav></header><main><h1>Acme Roofing</h1><p>{LONG}</p></main></body></html>",
    "https://acme.com/services": f"<html><head><title>Services</title></head><body><main><h1>Services</h1><p>{LONG.replace('slate', 'flat')} Gutters, chimneys and skylights too, and every job is photographed before and after for the owner.</p><p>Emergency call-outs are answered the same day.</p></main></body></html>",
    "https://acme.com/contact": "<html><head><title>Contact</title></head><body><main><h1>Contact us</h1><p>Call 0113 555 0100.</p><form action='/send'><input name='email'></form></main></body></html>",
}


SITEMAPS = {}


def response(url, status=200, body=b"", headers=None):
    return {"status": status, "final_url": url, "chain": [], "headers": headers or {}, "body": body, "truncated": False}


def args_for(out, command="site", urls=("https://acme.com/",), **kw):
    """The arguments the real command line builds, with test overrides."""
    from ttcrawl import cli
    argv = [command, *urls, "--out", out, "--static", "--json"]
    args = cli.build_parser().parse_args(argv)
    for k, v in kw.items():
        setattr(args, k, v)
    return args


def read(path):
    with open(path) as f:
        return f.read()


class CrawlHarness(unittest.TestCase):
    """site.run on the fake responses above, with retries' waits recorded."""

    def setUp(self):
        self.saved = (net.fetch_once, net.is_public_host, site.time.sleep, net.time.sleep)
        self.saved_driver = chrome.driver
        chrome.driver = lambda choice, **kw: (None, "no browser in tests")
        net.is_public_host = lambda host: True
        site.time.sleep = lambda s: None
        self.sleeps = []
        self.throttle_left = {}

        def fetch_once(url, cap=0, timeout=0, method="GET"):
            if self.throttle_left.get(url):
                self.throttle_left[url] -= 1
                return response(url, 429, headers={"retry-after": "0"})
            html = PAGES.get(url) or SITEMAPS.get(url)
            if html is None:
                return response(url, 404)
            return response(url, body=b"" if method == "HEAD" else html.encode(), headers={})

        net.fetch_once = fetch_once

    def tearDown(self):
        net.fetch_once, net.is_public_host, site.time.sleep, net.time.sleep = self.saved
        chrome.driver = self.saved_driver

    def crawl(self, out, command="site", urls=("https://acme.com/",), expect=0, **kw):
        # fetch() binds time.sleep as its default; route retries through a recorder
        real_fetch = net.fetch

        def fetch(url, cap=2_000_000, timeout=30, method="GET", sleep=None):
            return real_fetch(url, cap=cap, timeout=timeout, method=method, sleep=self.sleeps.append)

        net.fetch = fetch
        try:
            buf = StringIO()
            with redirect_stdout(buf), redirect_stderr(StringIO()):
                args = args_for(out, command, urls, **kw)
                code = args.func(args)
        finally:
            net.fetch = real_fetch
        self.assertEqual(code, expect)
        return json.loads(buf.getvalue().strip().splitlines()[-1]) if code == 0 else None


class CrawlRunTests(CrawlHarness):
    def test_thin_page_is_kept_and_marked(self):
        with tempfile.TemporaryDirectory() as out:
            summary = self.crawl(out)
            self.assertEqual(summary["pages"], 3)
            self.assertEqual(summary["thin"], 1)
            with open(os.path.join(out, "pages", "contact.md")) as f:
                text = f.read()
            self.assertIn("\nthin: true\n", text)
            self.assertIn("0113 555 0100", text)
            with open(os.path.join(out, "_index", "inventory.json")) as f:
                inv = json.load(f)
            row = next(r for r in inv["records"] if r["url"] == "https://acme.com/contact")
            self.assertTrue(row["thin"])
            self.assertEqual(row["file"], "pages/contact.md")

    def test_recrawl_refreshes_in_place(self):
        with tempfile.TemporaryDirectory() as out:
            self.crawl(out)
            first = sorted(os.listdir(os.path.join(out, "pages")))
            PAGES["https://acme.com/services"] = PAGES["https://acme.com/services"].replace("same day", "same hour")
            try:
                self.crawl(out)
            finally:
                PAGES["https://acme.com/services"] = PAGES["https://acme.com/services"].replace("same hour", "same day")
            self.assertEqual(sorted(os.listdir(os.path.join(out, "pages"))), first)
            with open(os.path.join(out, "pages", "services.md")) as f:
                self.assertIn("same hour", f.read())

    def test_recrawl_with_a_smaller_limit_keeps_earlier_files(self):
        with tempfile.TemporaryDirectory() as out:
            self.crawl(out)
            summary = self.crawl(out, max_pages=1)
            self.assertEqual(summary["pages"], 1)
            self.assertEqual(summary["earlier_kept"], 2)
            self.assertTrue(os.path.isfile(os.path.join(out, "pages", "contact.md")))
            # and a third run still knows their names
            self.crawl(out)
            self.assertFalse([f for f in os.listdir(os.path.join(out, "pages")) if f.endswith("-1.md")])

    def test_resume_carries_on_only_the_same_sites_crawl(self):
        with tempfile.TemporaryDirectory() as out:
            self.crawl(out)
            self.crawl(out, urls=("https://other.example/",), resume=True)
            with open(os.path.join(out, "_index", "inventory.json")) as f:
                urls = [r["url"] for r in json.load(f)["records"]]
            self.assertEqual(urls, ["https://other.example/"])          # nothing of acme.com seeded into it

    def test_a_browser_that_will_not_start_is_reported_not_claimed(self):
        class Broken:
            engine = "chrome"

            def __init__(self, binary):
                raise cdp.CDPError("chrome never opened its debugging port")

        chrome.driver = lambda choice, **kw: (cdp.Driver("/bin/chrome", browser=Broken), None)
        with tempfile.TemporaryDirectory() as out:
            summary = self.crawl(out, static=False)
            self.assertEqual(summary["pages"], 3)                       # read without it
            self.assertIsNone(summary["renderer"])
            self.assertIn("chrome would not start", summary["browser_note"])
            with open(os.path.join(out, "_index", "run.json")) as f:
                self.assertIsNone(json.load(f)["renderer"])

    def test_static_starts_no_browser_and_says_what_it_skipped(self):
        asked = []
        chrome.driver = lambda choice, **kw: asked.append(choice) or (None, None)
        with tempfile.TemporaryDirectory() as out:
            summary = self.crawl(out, command="brand")                 # brand asks for screenshots and styles
            self.assertEqual(asked, [])
            self.assertEqual(summary["screenshots"], 0)
            self.assertEqual(summary["browser_note"], "--static: no browser, so no screenshots or styles")

    def test_throttled_page_is_waited_out(self):
        self.throttle_left["https://acme.com/services"] = 2
        with tempfile.TemporaryDirectory() as out:
            summary = self.crawl(out)
            self.assertEqual(summary["pages"], 3)
            self.assertEqual(summary["throttled"], 2)
            self.assertEqual(self.sleeps, [s for s in self.sleeps if s >= 5])   # never under the floor
            self.assertEqual(len(self.sleeps), 2)

    def test_throttled_forever_is_a_skip_not_a_hang(self):
        self.throttle_left["https://acme.com/services"] = 99
        with tempfile.TemporaryDirectory() as out:
            summary = self.crawl(out)
            self.assertEqual(summary["pages"], 2)
            self.assertEqual(len(self.sleeps), net.MAX_RETRIES)
            with open(os.path.join(out, "_index", "manifest.json")) as f:
                manifest = json.load(f)
            self.assertIn({"url": "https://acme.com/services", "reason": "http_429"}, manifest["skipped"])


class SurveyTests(CrawlHarness):
    """A site with a blog of eight posts its sitemap lists as posts."""
    POSTS = ["https://acme.com/%s-tips" % w for w in ("roof", "gutter", "slate", "tile", "chimney", "skylight", "flat", "storm")]

    def setUp(self):
        super().setUp()
        self.saved_pages = dict(PAGES)
        for i, u in enumerate(self.POSTS):
            PAGES[u] = f"<html><body><main><h1>Post {i}</h1><p>12 May 2026</p><p>{LONG} Post number {i}.</p></main></body></html>"
        SITEMAPS["https://acme.com/sitemap.xml"] = ("<sitemapindex><sitemap><loc>https://acme.com/post-sitemap.xml</loc></sitemap>"
                                                    "<sitemap><loc>https://acme.com/page-sitemap.xml</loc></sitemap></sitemapindex>")
        SITEMAPS["https://acme.com/post-sitemap.xml"] = "<urlset>%s</urlset>" % "".join(
            "<url><loc>%s</loc></url>" % u for u in self.POSTS)
        SITEMAPS["https://acme.com/page-sitemap.xml"] = "<urlset><url><loc>https://acme.com/about-us</loc></url></urlset>"
        PAGES["https://acme.com/about-us"] = f"<html><body><main><h1>About</h1><p>{LONG.replace('roofers', 'people')}</p></main></body></html>"

    def tearDown(self):
        PAGES.clear()
        PAGES.update(self.saved_pages)
        SITEMAPS.clear()
        super().tearDown()

    def test_survey_reads_two_posts_and_lists_the_rest(self):
        with tempfile.TemporaryDirectory() as out:
            summary = self.crawl(out, command="survey")
            with open(os.path.join(out, "_index", "templates.json")) as f:
                post = next(t for t in json.load(f) if t["template"] == "post")
            self.assertEqual((post["count"], post["read"], post["not_read"]), (8, 2, 6))
            self.assertEqual(len(post["shapes"]), 1)
            self.assertIn("| post | 8 | 2 | 6 |", read(os.path.join(out, "_index", "templates.md")))
            with open(os.path.join(out, "_index", "run.json")) as f:
                run = json.load(f)
            self.assertEqual(run["not_fetched_by_template"], {"post": 6})
            self.assertEqual(run["profile"], "survey")
            self.assertTrue(os.path.isfile(os.path.join(out, "pages", "about-us.md")))   # a page is read, not sampled
            self.assertEqual(summary["collections"], 1)

    def test_the_nav_is_read_before_the_sitemap(self):
        with tempfile.TemporaryDirectory() as out:
            self.crawl(out, max_pages=3)
            with open(os.path.join(out, "_index", "manifest.json")) as f:
                order = [p["url"] for p in json.load(f)["pages"]]
            self.assertEqual(order, ["https://acme.com/", "https://acme.com/services", "https://acme.com/contact"])

    def test_add_reads_one_more_and_writes_it_all_again(self):
        with tempfile.TemporaryDirectory() as out:
            self.crawl(out, command="survey")
            unread = next(u for u in self.POSTS if not os.path.isfile(os.path.join(out, "pages", u.rsplit("/", 1)[1] + ".md")))
            summary = self.crawl(out, command="add", urls=(unread,))
            self.assertTrue(os.path.isfile(os.path.join(out, "pages", unread.rsplit("/", 1)[1] + ".md")))
            self.assertEqual((summary["new"], summary["changed"]), (1, 0))
            with open(os.path.join(out, "_index", "templates.json")) as f:
                post = next(t for t in json.load(f) if t["template"] == "post")
            self.assertEqual((post["read"], post["not_read"]), (3, 5))

    def test_add_keeps_the_folders_own_settings(self):
        with tempfile.TemporaryDirectory() as out:
            self.crawl(out, command="survey")                    # a survey fetches no pictures
            summary = self.crawl(out, command="add", urls=(self.POSTS[-1],))
            with open(os.path.join(out, "_index", "run.json")) as f:
                run = json.load(f)
            with open(os.path.join(out, "_index", "manifest.json")) as f:
                manifest = json.load(f)
            self.assertEqual((run["command"], run["profile"], run["images"]["mode"]), ("add", "survey", "none"))
            self.assertEqual((manifest["profile"], manifest["limit"], summary["limit"]), ("survey", 100, 100))
            self.crawl(out, command="add", urls=(self.POSTS[-2],), images="brand")   # unless asked otherwise
            with open(os.path.join(out, "_index", "run.json")) as f:
                self.assertEqual(json.load(f)["images"]["mode"], "brand")
            self.crawl(out, command="add", urls=(self.POSTS[-3],))
            with open(os.path.join(out, "_index", "run.json")) as f:
                self.assertEqual(json.load(f)["images"]["mode"], "none")   # the folder's own, still

    def test_add_refuses_a_folder_with_no_recorded_settings(self):
        with tempfile.TemporaryDirectory() as out:
            self.crawl(out, command="survey")
            path = os.path.join(out, "_cache", "crawl.json")
            with open(path) as f:
                state = json.load(f)
            del state["settings"]
            with open(path, "w") as f:
                json.dump(state, f)
            err = StringIO()
            with redirect_stderr(err), redirect_stdout(StringIO()):
                args = args_for(out, "add", (self.POSTS[-1],))
                self.assertEqual(args.func(args), 2)
            self.assertIn("has no recorded crawl settings", err.getvalue())
            self.assertIn("to crawl it again", err.getvalue())

    def test_add_needs_a_crawl_first(self):
        with tempfile.TemporaryDirectory() as out:
            self.assertIsNone(self.crawl(out, command="add", urls=("https://acme.com/about-us",), expect=2))

    def test_frontmatter_and_changes_between_runs(self):
        with tempfile.TemporaryDirectory() as out:
            self.crawl(out)
            text = read(os.path.join(out, "pages", "services.md"))
            self.assertTrue(text.startswith('---\nurl: "https://acme.com/services"\ntitle: "Services"\ntemplate: "/services"\n'))
            self.assertIn('fetcher: "static"', text)
            again = self.crawl(out)
            self.assertEqual((again["new"], again["changed"]), (0, 0))
            PAGES["https://acme.com/services"] = PAGES["https://acme.com/services"].replace("same day", "same hour")
            self.assertEqual(self.crawl(out)["changed"], 1)


class NameTests(unittest.TestCase):
    def test_page_name(self):
        from ttcrawl.text import page_name
        self.assertEqual(page_name("https://acme.com/"), "index")
        self.assertEqual(page_name("https://acme.com/services/flat-roofs"), "services--flat-roofs")
        self.assertNotEqual(page_name("https://acme.com/blog/post"), page_name("https://acme.com/blog-post"))
        self.assertNotEqual(page_name("https://acme.com/About"), page_name("https://acme.com/about"))
        self.assertNotEqual(page_name("https://acme.com/list?page=1"), page_name("https://acme.com/list?page=2"))
        long_a, long_b = "https://acme.com/" + "a" * 90, "https://acme.com/" + "a" * 91
        self.assertNotEqual(page_name(long_a), page_name(long_b))
        self.assertRegex(page_name("https://acme.com/About"), r"^about-[0-9a-f]{8}$")
        self.assertEqual(page_name("https://acme.com/x"), page_name("https://acme.com/x"))   # stable


class CollisionTests(CrawlHarness):
    """Two URLs that slugify alike each keep their own page, JSON and name."""

    def setUp(self):
        super().setUp()
        self.saved_pages = dict(PAGES)
        PAGES["https://acme.com/"] = PAGES["https://acme.com/"].replace(
            "<a href='/contact'>Contact</a>", "<a href='/blog/post'>A</a> <a href='/blog-post'>B</a>")
        PAGES["https://acme.com/blog/post"] = f"<html><body><main><h1>Post one</h1><p>{LONG} Nested.</p></main></body></html>"
        PAGES["https://acme.com/blog-post"] = f"<html><body><main><h1>Post two</h1><p>{LONG.replace('roofs', 'gutters')} Flat.</p></main></body></html>"

    def tearDown(self):
        PAGES.clear()
        PAGES.update(self.saved_pages)
        super().tearDown()

    def test_each_url_its_own_files(self):
        with tempfile.TemporaryDirectory() as out:
            self.crawl(out)
            self.assertIn("Post one", read(os.path.join(out, "pages", "blog--post.md")))
            self.assertIn("Post two", read(os.path.join(out, "pages", "blog-post.md")))
            structured = os.listdir(os.path.join(out, "structured"))
            self.assertIn("blog--post.json", structured)
            self.assertIn("blog-post.json", structured)

    def test_an_earlier_name_is_kept_and_never_taken(self):
        with tempfile.TemporaryDirectory() as out:
            # an earlier run wrote /blog/post as blog-post.md
            os.makedirs(os.path.join(out, "_index"))
            with open(os.path.join(out, "_index", "manifest.json"), "w") as f:
                json.dump({"pages": [{"url": "https://acme.com/blog/post", "file": "pages/blog-post.md"}]}, f)
            self.crawl(out)
            self.assertIn("Post one", read(os.path.join(out, "pages", "blog-post.md")))
            others = [f for f in os.listdir(os.path.join(out, "pages")) if f.startswith("blog-post-")]
            self.assertEqual(len(others), 1)                 # /blog-post hashed, not overwriting
            self.assertIn("Post two", read(os.path.join(out, "pages", others[0])))


class BackoffTests(unittest.TestCase):
    def test_retry_after(self):
        self.assertEqual(net.retry_after("7"), 7.0)
        self.assertIsNone(net.retry_after(None))
        self.assertIsNone(net.retry_after("soon"))
        self.assertEqual(net.retry_after("Wed, 21 Oct 2015 07:28:10 GMT", now=1445412480.0), 10.0)
        self.assertEqual(net.retry_after("Wed, 21 Oct 2015 07:28:00 GMT", now=1445412490.0), 0.0)

    def test_only_a_503_with_retry_after_is_throttling(self):
        self.assertTrue(net.throttled(429, {}))
        self.assertTrue(net.throttled(503, {"retry-after": "30"}))
        self.assertFalse(net.throttled(503, {}))           # a site that is down: check and audit report it
        self.assertFalse(net.throttled(500, {"retry-after": "30"}))

    def test_bare_503_is_answered_at_once(self):
        saved = net.fetch_once
        net.fetch_once = lambda url, **kw: response(url, 503)
        try:
            waits = []
            self.assertEqual(net.fetch("https://acme.com/", sleep=waits.append)["status"], 503)
            self.assertEqual(waits, [])
        finally:
            net.fetch_once = saved

    def test_backoff_floor_and_cap(self):
        self.assertEqual([net.backoff(n) for n in (1, 2, 3, 4)], [5, 10, 20, 40])
        self.assertEqual(net.backoff(1, "0"), 5)        # a Retry-After of 0 is not obeyed literally
        self.assertEqual(net.backoff(1, "30"), 30)
        self.assertEqual(net.backoff(1, "9999"), net.BACKOFF_CAP_S)


class _FakeSock:
    def __init__(self, data=b""):
        self.data, self.sent = data, b""

    def recv(self, n):
        out, self.data = self.data[:n], self.data[n:]
        return out

    def sendall(self, b):
        self.sent += b


def _ws(data=b""):
    ws = cdp.WebSocket.__new__(cdp.WebSocket)
    ws.sock, ws.buf = _FakeSock(data), b""
    return ws


class CDPTests(unittest.TestCase):
    def test_strip_plan(self):
        self.assertEqual(cdp.strip_plan(1000, strip=1600), ([(0, 1000)], False))
        self.assertEqual(cdp.strip_plan(3300, strip=1600), ([(0, 1600), (1600, 1600), (3200, 100)], False))
        plan, cut = cdp.strip_plan(10_000, strip=1600, max_strips=3)
        self.assertEqual(len(plan), 3)
        self.assertTrue(cut)

    def test_frames_long_fragmented_and_ping(self):
        big = b"x" * 70_000
        data = (bytes([0x89, 0x00])                                     # a ping, skipped
                + bytes([0x01, 0x7F]) + struct.pack(">Q", len(big)) + big   # text, not final, 64-bit length
                + bytes([0x80, 0x03]) + b"end")                          # continuation, final
        self.assertEqual(_ws(data).recv(), "x" * 70_000 + "end")

    def test_a_frame_arriving_in_pieces_is_not_lost(self):
        frame = bytes([0x81, 0x05]) + b"hello"
        ws = _ws(frame[:3])

        def recv(n, sock=ws.sock):
            if not sock.data:
                raise socket.timeout()
            out, sock.data = sock.data[:n], sock.data[n:]
            return out

        ws.sock.recv = recv
        with self.assertRaises(socket.timeout):
            ws.recv()                                       # timed out mid-frame
        ws.sock.data = frame[3:]
        self.assertEqual(ws.recv(), "hello")                # the stream picks up where it was

    def test_send_is_masked(self):
        ws = _ws()
        ws.send("hi")
        head, mask, payload = ws.sock.sent[:2], ws.sock.sent[2:6], ws.sock.sent[6:]
        self.assertEqual(head, bytes([0x81, 0x82]))
        self.assertEqual(bytes(b ^ mask[i % 4] for i, b in enumerate(payload)), b"hi")

    def test_close_frame_raises(self):
        with self.assertRaises(cdp.CDPError):
            _ws(bytes([0x88, 0x00])).recv()

    def test_the_driver_restarts_and_never_raises(self):
        started = []

        class FakeBrowser:
            engine = "fake"

            def __init__(self, binary):
                started.append(binary)

            def stop(self):
                pass

        calls = []

        def capture(browser, url, out_dir):
            calls.append(url)
            if url.endswith("/dies"):
                raise cdp.CDPError("websocket closed")
            return {"strips": ["01.png"]}

        def renderer(browser, url, styles):
            if url.endswith("/dies"):
                raise cdp.CDPError("websocket closed")
            return {"html": "<p>%s</p>" % url, "styles": {"fonts": []} if styles else None}

        d = cdp.Driver("/bin/obscura", browser=FakeBrowser, capture=capture, renderer=renderer)
        d.USES_PER_BROWSER = 2
        self.assertEqual(d.shoot("https://a.com/1", "x"), {"strips": ["01.png"]})
        self.assertEqual(d.render("https://a.com/2", styles=True)["styles"], {"fonts": []})   # one browser for both
        d.shoot("https://a.com/3", "x")                     # a fresh browser after two uses
        self.assertEqual(len(started), 2)
        self.assertEqual(d.shoot("https://a.com/dies", "x"), {"error": "websocket closed"})
        self.assertEqual(calls.count("https://a.com/dies"), 2)   # retried once on a fresh browser
        self.assertIsNone(d.render("https://a.com/dies"))
        d.stop()

    def test_a_browser_that_will_not_start_is_tried_once(self):
        tries = []

        class Broken:
            engine = "fake"

            def __init__(self, binary):
                tries.append(binary)
                raise cdp.CDPError("chrome never opened its debugging port")

        d = cdp.Driver("/bin/chrome", browser=Broken, renderer=lambda b, u, s: {"html": "x"})
        self.assertIsNone(d.render("https://a.com/1"))
        self.assertEqual(d.shoot("https://a.com/2", "x"), {"error": "fake would not start (chrome never opened its debugging port)"})
        self.assertEqual(len(tries), 1)

    def test_a_browser_that_will_not_start_hands_over_to_the_fallback(self):
        class Broken:
            engine = "chrome"

            def __init__(self, binary):
                raise cdp.CDPError("no port")

        class Works:
            engine = "obscura"

            def __init__(self, binary):
                pass

            def stop(self):
                pass

        d = cdp.Driver("/bin/chrome", browser=Broken, fallback=("/bin/obscura", Works),
                       renderer=lambda b, u, s: {"html": "<p>x</p>"})
        self.assertEqual(d.render("https://a.com/"), {"html": "<p>x</p>"})
        self.assertEqual(d.engine, "obscura")
        self.assertIn("chrome would not start (no port); obscura used instead", d.note)

    def test_a_page_error_does_not_restart_the_browser(self):
        started, calls = [], []

        class FakeBrowser:
            engine = "fake"

            def __init__(self, binary):
                started.append(binary)

            def stop(self):
                pass

        def renderer(browser, url, styles):
            calls.append(url)
            raise cdp.PageError("%s: net::ERR_ACCESS_DENIED" % url)

        d = cdp.Driver("/bin/x", browser=FakeBrowser, renderer=renderer)
        self.assertIsNone(d.render("https://a.com/private-redirect"))
        self.assertEqual((len(calls), len(started)), (1, 1))

    def fake_session(self, navigate, extracted):
        test = self

        class FakeSession:
            closed = []

            def __init__(self, ws_url, timeout=60, on_event=None):
                pass

            def call(self, method, params=None, session=True, timeout=60):
                return navigate if method == "Page.navigate" else {}

            def wait_event(self, name, timeout=30):
                return {}

            def evaluate(self, expression, await_promise=False, timeout=60):
                return 7 if expression == cdp.SETTLE_JS else extracted

            def close(self):
                FakeSession.closed.append(True)
        test.addCleanup(mock.patch.stopall)
        mock.patch.object(cdp, "Session", FakeSession).start()
        mock.patch.object(cdp.time, "sleep", lambda s: None).start()
        return FakeSession

    def test_render_reads_the_page_and_closes_its_session(self):
        browser = type("B", (), {"ws_url": "ws://x", "needs_guard": False})()
        fake = self.fake_session({"frameId": "1"}, '{"html": "<p>hi</p>", "styles": null}')
        self.assertEqual(cdp.render(browser, "https://a.com/"), {"html": "<p>hi</p>", "styles": None})
        self.assertEqual(fake.closed, [True])

    def test_render_refused_or_empty_is_a_page_error(self):
        browser = type("B", (), {"ws_url": "ws://x", "needs_guard": False})()
        fake = self.fake_session({"errorText": "net::ERR_ACCESS_DENIED"}, None)
        with self.assertRaises(cdp.PageError):
            cdp.render(browser, "https://a.com/")
        mock.patch.stopall()
        fake2 = self.fake_session({"frameId": "1"}, '{"html": "  "}')
        with self.assertRaises(cdp.PageError):
            cdp.render(browser, "https://a.com/")
        self.assertEqual((fake.closed, fake2.closed), ([True], [True]))

    def test_a_session_that_cannot_attach_closes_what_it_opened(self):
        sent, closed = [], []

        class FakeWS:
            def __init__(self, url, timeout=60):
                self.sock = mock.Mock()
                self.replies = []

            def send(self, text):
                msg = json.loads(text)
                sent.append(msg["method"])
                result = {"Target.createTarget": {"result": {"targetId": "t1"}},
                          "Target.attachToTarget": {"error": {"message": "no such target"}}}.get(msg["method"], {"result": {}})
                self.replies.append(json.dumps(dict(result, id=msg["id"])))

            def recv(self):
                return self.replies.pop(0)

            def close(self):
                closed.append(True)

        with mock.patch.object(cdp, "WebSocket", FakeWS):
            with self.assertRaises(cdp.PageError):
                cdp.Session("ws://x")
        self.assertEqual(sent, ["Target.createTarget", "Target.attachToTarget", "Target.closeTarget"])
        self.assertEqual(closed, [True])

    def test_chrome_interrupted_while_starting_leaves_no_process(self):
        proc = mock.Mock()
        proc.poll.return_value = None

        def interrupted(port, tries):
            raise KeyboardInterrupt

        with mock.patch.object(cdp, "wait_for_endpoint", interrupted):
            with self.assertRaises(KeyboardInterrupt):
                chrome.Chrome("/bin/chrome", popen=lambda *a, **k: proc)
        proc.terminate.assert_called_once()

    def test_a_chrome_that_cannot_run_is_never_linked(self):
        data = BytesIO()
        with zipfile.ZipFile(data, "w") as z:
            z.writestr("chrome-headless-shell-linux64/chrome-headless-shell", "x")
        cft = json.dumps({"channels": {"Stable": {"version": "1", "downloads": {"chrome-headless-shell": [
            {"platform": "linux64", "url": "https://x/c.zip"}]}}}}).encode()
        with tempfile.TemporaryDirectory() as home:
            with mock.patch.multiple(chrome, can_install_chrome=lambda: True, SHARE=home,
                                     LOCAL_BIN=os.path.join(home, "bin"),
                                     _download=lambda url, timeout=600: cft if url == chrome.CFT_JSON else data.getvalue(),
                                     missing_libraries=lambda binary: ["libnss3.so"],
                                     apt_install=lambda choices, log=None: ["libnss3"]):
                with self.assertRaises(RuntimeError):
                    chrome.install_chrome(log=lambda m: None)
            self.assertFalse(os.path.lexists(os.path.join(home, "bin", "chrome-headless-shell")))

    def test_free_port_is_loopback(self):
        port = cdp.free_port()
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", port))


if __name__ == "__main__":
    unittest.main()
