"""Content blocks, their markdown, templates, and the reading order, on
fixture HTML and URLs. No network, no browser."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ttcrawl import browser  # noqa: E402
from ttcrawl.blocks import fingerprint, to_markdown  # noqa: E402
from ttcrawl.html import parse_page  # noqa: E402
from ttcrawl.templates import Frontier, Sampler, Templates, is_collection, sitemap_label  # noqa: E402

URL = "https://acme.com/services"


def blocks_of(body, url=URL):
    return parse_page("<html><body>%s</body></html>" % body, url)["blocks"]


def md_of(body, url=URL, **kw):
    return to_markdown(blocks_of(body, url), **kw)


class BlockTests(unittest.TestCase):
    def test_verbatim_in_order_with_links_inline(self):
        md = md_of("<main><h1>Roofs</h1><p>We fix <a href='/flat'>flat roofs</a> in Leeds.</p>"
                   "<ul><li>Slate</li><li>Tile</li></ul><blockquote>Great work</blockquote>"
                   "<figure><img src='/crew.jpg' alt='The crew' width='1200'><figcaption>Our crew</figcaption></figure></main>")
        self.assertEqual(md, "# Roofs\n\nWe fix [flat roofs](https://acme.com/flat) in Leeds.\n\n- Slate\n- Tile\n\n"
                             "> Great work\n\n![The crew](https://acme.com/crew.jpg)\n\n*Our crew*\n")

    def test_page_builder_text_in_divs_is_kept(self):
        md = md_of("<div class='elementor-widget'><div>Call 0113 555 0100</div><span>Open 8 to 5</span></div>")
        self.assertIn("Call 0113 555 0100", md)
        self.assertIn("Open 8 to 5", md)

    def test_a_br_is_a_line_break(self):
        self.assertIn("12 Dock Rd  \nLeeds", md_of("<p>12 Dock Rd<br>Leeds</p>"))

    def test_nested_blocks_report_on_their_own(self):
        blocks = blocks_of("<ul><li><p>Inner</p> tail</li></ul>")
        self.assertEqual([(b["tag"], b["text"]) for b in blocks], [("li", "tail"), ("p", "Inner")])

    def test_site_chrome_flagged_and_left_out(self):
        body = "<header><nav><a href='/'>Home</a></nav></header><main><p>Body</p></main><footer><p>© Acme</p></footer>"
        self.assertEqual(md_of(body), "Body\n")
        self.assertIn("© Acme", md_of(body, keep_chrome=True))

    def test_an_articles_own_header_is_content(self):
        md = md_of("<main><article><header><h1>Storm season</h1><p>12 May 2026</p></header><p>Text</p></article></main>")
        self.assertIn("# Storm season", md)
        self.assertIn("12 May 2026", md)

    def test_hidden_and_plumbing_left_out(self):
        md = md_of("<a href='#content'>Skip to content</a><p hidden>Secret</p><span aria-hidden='true'>★</span>"
                   "<script>x()</script><form><button>Send</button></form><p>Shown</p>")
        self.assertEqual(md, "Shown\n")

    def test_browser_annotations_choose_the_image(self):
        blocks = blocks_of("<img src='/small.jpg' data-tt-src='https://cdn.acme.com/big.jpg' data-tt-w='1600' alt='Roof'>"
                           "<img src='/thumb.jpg' data-tt-w='120'><img src='/icons/phone.svg'>"
                           "<div data-tt-bg='https://acme.com/hero.jpg'><p>Hero</p></div>")
        imgs = [b for b in blocks if b["tag"] == "img"]
        self.assertEqual([i["src"] for i in imgs], ["https://cdn.acme.com/big.jpg", "https://acme.com/hero.jpg"])
        self.assertTrue(imgs[1]["background"])

    def test_a_card_link_gives_its_heading_the_target(self):
        md = md_of("<a href='/services/flat'><h3>Flat roofs</h3><p>From £900</p></a>")
        self.assertIn("### [Flat roofs](https://acme.com/services/flat)", md)

    def test_repeats_dropped_but_a_dated_line_kept(self):
        md = md_of("<p>Slide one says a long thing.</p><p>Slide one says a long thing.</p>"
                   "<h3>News A</h3><p>Published 12 May 2026</p><h3>News B</h3><p>Published 12 May 2026</p>")
        self.assertEqual(md.count("Slide one"), 1)
        self.assertEqual(md.count("Published 12 May 2026"), 2)

    def test_fingerprint_is_the_shape_of_the_top(self):
        post = "<main><h1>%s</h1><p>date</p><img src='/a.jpg'><p>%s</p></main>"
        a = fingerprint(blocks_of(post % ("One", "x " * 10)))
        b = fingerprint(blocks_of(post % ("Two", "y " * 400)))
        page = fingerprint(blocks_of("<main><h1>About</h1><ul><li>a</li></ul><p>z</p></main>"))
        self.assertEqual(a, b)
        self.assertNotEqual(a, page)

    def test_extract_runs_one_render(self):
        seen = {}

        class Proc:
            returncode, stdout = 0, b'{"html": "<html><body><p>hi</p></body></html>", "styles": {"fonts": []}}'

        def runner(cmd, **kw):
            seen["cmd"] = cmd
            return Proc()

        got = browser.extract("https://acme.com/", "/bin/obscura", styles=True, runner=runner)
        self.assertEqual(got["styles"], {"fonts": []})
        self.assertIn("--eval", seen["cmd"])
        self.assertIn("true ? JSON.parse", seen["cmd"][-1])
        self.assertIsNone(browser.extract("https://acme.com/", "/bin/o", runner=lambda c, **k: type("P", (), {"returncode": 1, "stdout": b""})()))


class TemplateTests(unittest.TestCase):
    def test_sitemap_label(self):
        self.assertEqual(sitemap_label("https://a.com/post-sitemap2.xml"), "post")
        self.assertEqual(sitemap_label("https://a.com/sitemap_products_1.xml.gz"), "products")
        self.assertEqual(sitemap_label("https://a.com/wp-sitemap-posts-post-1.xml"), "post")
        self.assertIsNone(sitemap_label("https://a.com/sitemap.xml"))
        self.assertIsNone(sitemap_label("https://a.com/page-sitemap.xml"))

    def test_of(self):
        t = Templates()
        for i in range(6):
            t.add("https://a.com/blog/post-%d" % i)
        t.add("https://a.com/2024/05/hello")
        t.add("https://a.com/my-flat-post", "https://a.com/post-sitemap.xml")
        t.add("https://a.com/about")
        t.add("https://a.com/blog/featured")
        t.mark_nav(["https://a.com/blog/featured"])
        self.assertEqual(t.of("https://a.com/"), "/")
        self.assertEqual(t.of("https://a.com/blog/post-3"), "/blog/*")
        self.assertEqual(t.of("https://a.com/2024/05/hello"), "/{n}/{n}/*")
        self.assertEqual(t.of("https://a.com/my-flat-post"), "post")
        self.assertEqual(t.of("https://a.com/about"), "/about")
        self.assertEqual(t.of("https://a.com/blog/featured"), "/blog/featured")   # the nav names it: a page
        for i in range(6):
            t.add("https://a.com/tag/t%d" % i)
        self.assertEqual(t.of("https://a.com/page/3"), "/page/{n}")
        self.assertEqual(t.of("https://a.com/tag/t2/page/2"), "/tag/*/page/{n}")   # one template for every tag's pages
        self.assertTrue(is_collection("/blog/*") and is_collection("post") and is_collection("/page/{n}"))
        self.assertFalse(is_collection("/about"))

    def test_frontier_order_and_promotion(self):
        f = Frontier()
        f.add("sitemap-1", Frontier.SITEMAP)
        f.add("found", Frontier.FOUND)
        f.add("footer", Frontier.FOOTER)
        f.add("about", Frontier.SITEMAP)
        f.add("about", Frontier.NAV)            # found again through the nav: moves up
        f.add("footer", Frontier.FOUND)         # a worse door never moves it down
        self.assertEqual([f.pop() for _ in range(4)], ["about", "footer", "sitemap-1", "found"])
        self.assertIsNone(f.pop())

    def test_sampler(self):
        s = Sampler(per_template=2, per_section=3)
        self.assertEqual([s.admit("https://a.com/blog/p%d" % i, "/blog/*") for i in range(3)], [True, True, False])
        self.assertEqual([s.admit("https://a.com/help/p%d" % i, "/help/p%d" % i) for i in range(4)],
                         [True, True, True, False])
        self.assertTrue(s.admit("https://a.com/help/contact", "/help/contact", nav=True))   # nav skips the section cap
        self.assertTrue(Sampler().admit("https://a.com/x", "/x/*"))                           # no caps: read all
        s.refund("https://a.com/blog/p0", "/blog/*")                                          # it redirected
        self.assertTrue(s.admit("https://a.com/blog/p9", "/blog/*"))


if __name__ == "__main__":
    unittest.main()
