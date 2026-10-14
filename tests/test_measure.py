"""`tt-crawl measure`: what a browser measures on a page, summed into one line
per problem an agent can act on. The measuring itself is the browser's; here
the browser is a fake that returns what it would have measured."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ttcrawl import cdp, measure  # noqa: E402


class FakeDriver:
    def __init__(self, got):
        self.got, self.stopped, self.widths = got, False, []

    def _use(self, fn):
        # measure_page's width is the lambda's default argument
        self.widths.append(fn.__defaults__[0])
        return self.got

    def stop(self):
        self.stopped = True


class MeasureTest(unittest.TestCase):
    def test_every_page_is_measured_at_every_width_and_the_browser_stops(self):
        d = FakeDriver({"overflow": None, "edges": [], "small": [], "tight": []})
        out = measure.measure(["http://localhost:3000/", "http://localhost:3000/about"], [390, 1280], driver=d, status=lambda u: 200)
        self.assertEqual([(r["url"].split("3000")[1], r["width"]) for r in out], [("/", 390), ("/", 1280), ("/about", 390), ("/about", 1280)])
        self.assertEqual(d.widths, [390, 1280, 390, 1280])
        self.assertTrue(d.stopped)
        self.assertEqual(measure.problems(out[:2]), [])

    def test_an_error_page_is_not_measured(self):
        out = measure.measure(["http://localhost:3000/nope"], [390], driver=FakeDriver({}), status=lambda u: 404)
        self.assertEqual(out, [{"url": "http://localhost:3000/nope", "width": 390, "error": "http://localhost:3000/nope answered 404"}])

    def test_the_same_problem_at_several_widths_is_one_line(self):
        small = [{"px": 12.8, "element": "span.price", "text": "From $16.99"}, {"px": 12, "element": "span.price", "text": "From $18.99"}]
        tight = [{"gap": 0, "side": "left", "panel": "section.club-band", "text": "Every month deserves a treat."}]
        results = [{"width": 390, "small": small, "tight": [], "edges": [], "overflow": None},
                   {"width": 1280, "small": small, "tight": tight, "edges": [], "overflow": None}]
        self.assertEqual(measure.problems(results), [
            '390px, 1280px: text at 12px in <span.price> "From $16.99" (+1 more); 14px is the smallest a reader needs',
            '1280px: "Every month deserves a treat." is 0px from the left edge of <section.club-band>, a panel with its own ground; give it at least 16px of padding',
        ])

    def test_each_problem_names_the_rule_that_keeps_it_on_purpose(self):
        got = measure.problems([{"width": 390, "small": [{"px": 12, "element": "p", "text": "Hours"}], "tight": [], "edges": [], "overflow": None}], rules=True)
        self.assertEqual([p["rule"] for p in got], ["small-text"])

    def test_bands_that_start_on_different_edges_are_named(self):
        bands = [{"x": 32, "band": "header"}, {"x": 104, "band": "section.food"}, {"x": 33, "band": "footer"},
                 {"x": 104, "band": "section#story"}]
        self.assertEqual(cdp.edge_groups(bands), [{"x": 32, "bands": ["header", "footer"]},
                                                  {"x": 104, "bands": ["section.food", "section#story"]}])
        self.assertEqual(cdp.edge_groups([{"x": 32, "band": "header"}, {"x": 33, "band": "footer"}]), [])
        lines = measure.problems([{"width": 1600, "edges": cdp.edge_groups(bands), "overflow": {"px": 46, "element": "div.gallery"}}])
        self.assertIn("1600px: scrolls sideways by 46px, first past the edge: div.gallery", lines)
        self.assertIn("1600px: left edges differ: 32px header, footer; 104px section.food, section#story; put every band's content on one left edge", lines)

    def test_the_browser_checks_carry_their_limits(self):
        self.assertIn("const min = 14", cdp.SMALL_TEXT_JS % 14)
        self.assertIn('data-lint-allow~="small-text"', cdp.SMALL_TEXT_JS)
        self.assertIn('data-lint-allow~="tight-panel"', cdp.TIGHT_PANEL_JS)


if __name__ == "__main__":
    unittest.main()
