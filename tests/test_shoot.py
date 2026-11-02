"""`tt-crawl shoot`: names, the summary an agent reads, and the one private
host it may open (this machine's own dev server). The capture itself is the
crawl's screenshot code, run here on a fake browser."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ttcrawl import cdp, shoot  # noqa: E402


class FakeDriver:
    def __init__(self, fail=False):
        self.fail, self.calls, self.stopped = fail, [], False

    def _use(self, fn):
        if self.fail:
            raise cdp.CDPError("net::ERR_CONNECTION_REFUSED")
        self.calls.append(fn)
        return {"strips": ["01.webp", "02.webp"], "page": "page.jpg", "overview": "overview.webp", "height": 3000, "truncated": False}

    def stop(self):
        self.stopped = True


class SizeTest(unittest.TestCase):
    def test_strips_are_as_tall_as_a_model_reads_unscaled(self):
        self.assertEqual(cdp.strip_height(1280), 2044)
        self.assertEqual(cdp.strip_height(1440), 2044)
        self.assertEqual(cdp.strip_height(390), 2044)
        # wider than 1820: fewer rows, so the patches stay within 4784
        h = cdp.strip_height(1920)
        self.assertLessEqual(-(-1920 // 28) * (h // 28), cdp.MODEL_TOKENS)

    def test_the_overview_fits_the_whole_page_into_one_readable_image(self):
        s = cdp.overview_scale(1280, 6400)
        self.assertLessEqual(6400 * s, cdp.MODEL_EDGE)
        self.assertLessEqual(-(-int(1280 * s) // 28) * -(-int(6400 * s) // 28), cdp.MODEL_TOKENS)
        self.assertEqual(cdp.overview_scale(800, 600), 1.0)


class ShootTest(unittest.TestCase):
    def test_folder_names_come_from_the_path(self):
        self.assertEqual(shoot.name_for("http://localhost:3000/"), "home")
        self.assertEqual(shoot.name_for("http://localhost:3000/services/kitchens/"), "services-kitchens")

    def test_each_width_lands_in_its_own_folder_and_the_browser_stops(self):
        d = FakeDriver()
        results = shoot.shoot("http://localhost:3000/", [1280, 390], "uploads", driver=d, status=lambda u: 200)
        self.assertEqual([w for w, _ in results], [1280, 390])
        self.assertEqual([m["dir"] for _, m in results], ["uploads/home-1280", "uploads/home-390"])
        self.assertTrue(d.stopped)

    def test_the_summary_says_where_to_look_and_what_next(self):
        results = shoot.shoot("http://localhost:3000/", [1280], "uploads", driver=FakeDriver(), status=lambda u: 200)
        _what, lines, nxt = shoot.report([("http://localhost:3000/", results)], False)
        text = "\n".join(lines + ["Next: " + nxt])
        self.assertIn("uploads/home-1280/overview.webp, then 01.webp … 02.webp", text)
        self.assertIn("page.jpg is the whole page to share", text)
        self.assertIn("Next: look at each overview", text)

    def test_a_failure_says_how_to_check_the_page_is_served(self):
        results = shoot.shoot("http://localhost:3000/", [1280], "uploads", driver=FakeDriver(fail=True), status=lambda u: 200)
        _what, lines, nxt = shoot.report([("http://localhost:3000/", results)], False)
        text = "\n".join(lines + [nxt])
        self.assertIn("failed", text)
        self.assertIn("curl", text)

    def test_dev_not_answering_points_at_its_service(self):
        results = shoot.shoot("http://localhost:3000/", [1280], "uploads", driver=FakeDriver(), status=lambda u: None)
        self.assertIn("taskandtool.py logs", shoot.report([("http://localhost:3000/", results)], False)[2])

    def test_a_path_is_dev_and_anything_else_is_refused(self):
        self.assertEqual(shoot.page_url("/about"), "http://localhost:3000/about")
        self.assertEqual(shoot.page_url("https://example.com/x"), "https://example.com/x")
        self.assertIsNone(shoot.page_url("about"))

    def test_several_pages_are_named_with_their_widths_beneath(self):
        d = lambda: FakeDriver()
        shots = [(u, shoot.shoot(u, [1280], "uploads", driver=d(), status=lambda _: 200))
                 for u in ("http://localhost:3000/", "http://localhost:3000/about")]
        what, lines, _ = shoot.report(shots, False)
        self.assertEqual(what, "2 pages (whole page)")
        self.assertEqual(lines[0], "http://localhost:3000/")
        self.assertTrue(lines[1].startswith("  1280px: uploads/home-1280/"))
        self.assertEqual(lines[2], "http://localhost:3000/about")

    def test_the_sitemap_lists_its_pages_at_the_site_given(self):
        xml = (b"<urlset><url><loc>https://acme.com/</loc></url><url><loc> https://acme.com/about </loc></url>"
               b"<url><loc>https://acme.com/about</loc></url></urlset>")
        got = shoot.sitemap_pages("http://localhost:3000/", fetch=lambda u, timeout: {"status": 200, "body": xml})
        self.assertEqual(got, (["http://localhost:3000/", "http://localhost:3000/about"], None))
        self.assertEqual(shoot.sitemap_pages("http://localhost:3000/", fetch=lambda u, timeout: {"status": 404, "body": b""}),
                         (None, "http://localhost:3000/sitemap.xml answered 404"))
        self.assertIn("lists no pages", shoot.sitemap_pages("http://localhost:3000/", fetch=lambda u, timeout: {"status": 200, "body": b"<urlset/>"})[1])

    def test_an_error_page_is_not_shot(self):
        d = FakeDriver()
        results = shoot.shoot("http://localhost:3000/nope", [1280], "uploads", driver=d, status=lambda u: 404)
        self.assertEqual(results[0][1]["error"], "http://localhost:3000/nope answered 404")
        self.assertEqual(d.calls, [])
        results = shoot.shoot("http://localhost:3000/", [1280], "uploads", driver=d, status=lambda u: None)
        self.assertIn("nothing answered", results[0][1]["error"])

    def test_only_the_named_local_host_gets_through_the_guard(self):
        guard = cdp.RequestGuard(allow_hosts=("localhost",))
        self.assertTrue(guard.allowed("http://localhost:3000/site.css"))
        self.assertFalse(guard.allowed("http://192.168.1.1/admin"))
        self.assertFalse(cdp.RequestGuard().allowed("http://localhost:3000/"))


if __name__ == "__main__":
    unittest.main()


class PageStatusTest(unittest.TestCase):
    """A page that sends the visitor elsewhere is not shot as if it were the page."""

    def fetching(self, answer):
        real = shoot.net.fetch_once

        def fake(url, **_kw):
            if isinstance(answer, Exception):
                raise answer
            return answer

        shoot.net.fetch_once = fake
        self.addCleanup(setattr, shoot.net, "fetch_once", real)

    def test_another_site_is_a_reason_not_a_status(self):
        self.fetching({"status": 200, "final_url": "https://sign.example.net/access?return_to=x"})
        why = shoot.page_status("https://a.example.com/admin")
        self.assertEqual(why, "https://a.example.com/admin sends visitors to another site, "
                              "https://sign.example.net/access (often a sign-in)")
        results = shoot.shoot("https://a.example.com/admin", [1280], "uploads", driver=FakeDriver(),
                              status=lambda u: why)
        self.assertEqual(results[0][1]["error"], why)
        self.assertIn("to see where it sends visitors", shoot.report([("https://a.example.com/admin", results)], False)[2])

    def test_www_and_the_bare_domain_are_one_site(self):
        self.fetching({"status": 200, "final_url": "https://example.com/about"})
        self.assertEqual(shoot.page_status("https://www.example.com/about"), 200)

    def test_a_redirect_this_machine_may_not_follow_says_where_it_went(self):
        import urllib.error
        self.fetching(urllib.error.URLError("redirect to non-public host blocked: http://localhost:4000/access?x=1"))
        self.assertEqual(shoot.page_status("https://a.example.com/admin"),
                         "https://a.example.com/admin sends visitors to http://localhost:4000/access, "
                         "which this machine may not open (often a sign-in)")

    def test_nothing_answering_is_none(self):
        self.fetching(OSError("connection refused"))
        self.assertIsNone(shoot.page_status("https://a.example.com/"))
