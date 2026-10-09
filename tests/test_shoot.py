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
        return {"strips": ["01.png", "02.png"], "page": "page.png", "overview": "overview.png", "height": 3000, "truncated": False}

    def stop(self):
        self.stopped = True


class SizeTest(unittest.TestCase):
    def test_strips_are_as_tall_as_a_model_reads_unscaled(self):
        self.assertEqual(cdp.strip_height(1280), 2576)
        self.assertEqual(cdp.strip_height(1440), 2576)
        self.assertEqual(cdp.strip_height(390), 2576)
        # wider than 1456: fewer rows, so the patches stay within 4784
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
        _what, lines, nxt = shoot.report("http://localhost:3000/", results, False)
        text = "\n".join(lines + ["Next: " + nxt])
        self.assertIn("uploads/home-1280/overview.png, then 01.png … 02.png", text)
        self.assertIn("page.png is the whole page for people", text)
        self.assertIn("Next: look at each overview", text)

    def test_a_page_wider_than_the_screen_is_named(self):
        meta = {"dir": "uploads/home-390", "strips": ["01.png"], "height": 800, "overview": None,
                "overflow": {"px": 46, "element": "div.gallery"}}
        _what, lines, nxt = shoot.report("http://localhost:3000/", [(390, meta)], False)
        self.assertIn("390px: scrolls sideways by 46px, first past the edge: div.gallery", lines)
        self.assertIn("sticks out past the edge", nxt)

    def test_bands_that_start_on_different_edges_are_named(self):
        bands = [{"x": 32, "band": "header"}, {"x": 104, "band": "section.food"}, {"x": 33, "band": "footer"},
                 {"x": 104, "band": "section#story"}]
        self.assertEqual(cdp.edge_groups(bands), [{"x": 32, "bands": ["header", "footer"]},
                                                  {"x": 104, "bands": ["section.food", "section#story"]}])
        self.assertEqual(cdp.edge_groups([{"x": 32, "band": "header"}, {"x": 33, "band": "footer"}]), [])
        meta = {"dir": "uploads/home-1280", "strips": ["01.png"], "height": 800, "overview": None,
                "edges": cdp.edge_groups(bands)}
        _what, lines, nxt = shoot.report("http://localhost:3000/", [(1280, meta)], False)
        self.assertIn("1280px: left edges differ: 32px header, footer; 104px section.food, section#story", lines)
        self.assertIn("one left edge", nxt)

    def test_a_failure_says_how_to_check_the_page_is_served(self):
        results = shoot.shoot("http://localhost:3000/", [1280], "uploads", driver=FakeDriver(fail=True), status=lambda u: 200)
        _what, lines, nxt = shoot.report("http://localhost:3000/", results, False)
        text = "\n".join(lines + [nxt])
        self.assertIn("failed", text)
        self.assertIn("curl", text)

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
