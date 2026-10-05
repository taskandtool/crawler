"""Pictures, business facts, reviews, and the folder: identity across sizes,
the largest fetched once, facts with their sources and never from a review,
a crawl that resumes, and the registry beside the site folders. No network,
no browser."""
import json
import os
import struct
import sys
import tempfile
import unittest
import zlib
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ttcrawl import chrome, cli, dom, net, paths, site  # noqa: E402
from ttcrawl.facts import Facts, action_score, jsonld_reviews, phones, reviews  # noqa: E402
from ttcrawl.html import parse_page  # noqa: E402
from ttcrawl.media import Media, dimensions, file_name, variant_of  # noqa: E402


def png(w, h):
    raw = b"".join(b"\x00" + b"\x00\x00\x00" * w for _ in range(h))
    chunk = lambda t, d: struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d))
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)) + \
        chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")


class MediaTests(unittest.TestCase):
    def test_variant_of(self):
        self.assertEqual(variant_of("https://a.com/wp-content/uploads/2024/05/roof-1024x683.jpg"),
                         ("a.com/wp-content/uploads/2024/05/roof.jpg", "https://a.com/wp-content/uploads/2024/05/roof.jpg"))
        self.assertEqual(variant_of("https://a.com/uploads/roof-scaled.jpg")[1], "https://a.com/uploads/roof.jpg")
        self.assertEqual(variant_of("https://cdn.shopify.com/s/files/1/boot_800x.jpg?v=12")[1],
                         "https://cdn.shopify.com/s/files/1/boot.jpg?v=12")
        self.assertEqual(variant_of("https://static.wixstatic.com/media/abc.jpg/v1/fill/w_400,h_300/abc.jpg")[1],
                         "https://static.wixstatic.com/media/abc.jpg")
        self.assertEqual(variant_of("https://res.cloudinary.com/x/image/upload/c_fill,w_400/v123/team.jpg")[1],
                         "https://res.cloudinary.com/x/image/upload/team.jpg")
        self.assertEqual(variant_of("https://a.com/_next/image?url=%2Fimg%2Fhero.png&w=1080&q=75")[1],
                         "https://a.com/img/hero.png")
        self.assertEqual(variant_of("https://img.a.com/p.jpg?w=800&id=7")[0], variant_of("https://img.a.com/p.jpg?id=7&w=1600")[0])

    def test_dimensions(self):
        self.assertEqual(dimensions(png(3, 2)), (3, 2))
        self.assertEqual(dimensions(b"GIF89a" + struct.pack("<HH", 40, 30) + b"\x00" * 10), (40, 30))
        jpeg = b"\xff\xd8" + b"\xff\xe0" + struct.pack(">H", 4) + b"\x00\x00" + b"\xff\xc0" + struct.pack(">HBHH", 11, 8, 480, 640) + b"\x00" * 8
        self.assertEqual(dimensions(jpeg), (640, 480))
        webp = b"RIFF" + b"\x00" * 4 + b"WEBPVP8X" + b"\x00" * 8 + (799).to_bytes(3, "little") + (599).to_bytes(3, "little")
        self.assertEqual(dimensions(webp), (800, 600))
        self.assertIsNone(dimensions(b"not an image"))

    def test_file_name_readable_and_stable(self):
        a = file_name("a.com/img/crew%20on%20roof.jpg", "https://a.com/img/crew%20on%20roof.jpg", "jpg")
        self.assertRegex(a, r"^crew-on-roof-[0-9a-f]{8}\.jpg$")
        self.assertEqual(a, file_name("a.com/img/crew%20on%20roof.jpg", "https://a.com/img/crew%20on%20roof.jpg", "jpg"))
        self.assertNotEqual(file_name("a.com/x/image.jpg", "https://a.com/x/image.jpg", "jpg"),
                            file_name("a.com/y/image.jpg", "https://a.com/y/image.jpg", "jpg"))

    def test_brand_takes_the_logo_and_the_most_used_photos(self):
        m = Media()
        for i in range(70):
            blocks = [{"tag": "img", "src": "https://a.com/p%d.jpg" % i, "alt": "photo %d" % i, "chrome": False}]
            for page in range(1 + (i % 3)):
                m.add_page("https://a.com/page%d" % page, blocks, [])
        m.add_page("https://a.com/", [], [{"src": "https://a.com/logo.svg", "alt": "Acme", "landmark": "header"}])
        m.add_page("https://a.com/", [], [{"src": "https://acme.com/uploads/ifa-member-logo.jpg", "alt": "IFA", "landmark": "footer"},
                                           {"src": "https://a.com/acme-logo-white.png", "alt": "", "landmark": "footer"}])
        m.classify("https://a.com/logo.svg", "acme")
        self.assertEqual(m.items["acme.com/uploads/ifa-member-logo.jpg"]["kind"], "mark")   # a partner's, on the site's host
        self.assertEqual(m.items["a.com/acme-logo-white.png"]["kind"], "logo")      # the site's own, another version
        m.items["a.com/acme-logo-white.png"]["kind"] = "theme"
        chosen = m.select("brand")
        self.assertEqual(chosen[0]["key"], "a.com/logo.svg")
        # the logo, the partners' logos (proof for a homepage), then 60 photographs
        self.assertEqual(chosen[1]["key"], "acme.com/uploads/ifa-member-logo.jpg")
        self.assertEqual(len(chosen), 1 + 1 + 60)
        self.assertEqual(len(chosen[2]["pages"]), 3)                  # the photos more pages show come first


class FactsTests(unittest.TestCase):
    REVIEWS = """<main><div class="testimonials">
      <div class="review-card"><p>They fixed our roof in a day and cleaned up after.</p>
        <span class="author-name">Jane Doe</span><span>May 3, 2025</span><img src="/g.png" alt="Google">
        <i class="star"></i><i class="star"></i><i class="star"></i><i class="star"></i><i class="star"></i></div>
      <div class="review-card"><p>Call me on 555-123-4567 any time, great crew.</p><cite>Bob</cite></div>
    </div></main>"""

    def test_reviews_as_records(self):
        found = reviews(dom.build(self.REVIEWS), "https://a.com/")
        self.assertEqual(found[0], {"quote": "They fixed our roof in a day and cleaned up after.", "name": "Jane Doe",
                                    "date": "May 3, 2025", "platform": "Google", "stars": 5, "url": "https://a.com/"})
        self.assertEqual(found[1]["name"], "Bob")

    def test_a_page_builders_testimonial(self):
        html = """<main><div class="elementor-testimonial"><div class="elementor-testimonial__content">
          <div class="elementor-testimonial__text">Their emergency service saved our production line.</div></div>
          <div class="elementor-testimonial__footer"><cite class="elementor-testimonial__cite">
          <span class="elementor-testimonial__name">– Mike R.</span><span class="elementor-testimonial__title">Plant Manager</span>
          </cite></div></div></main>"""
        found = reviews(dom.build(html), "https://a.com/")
        self.assertEqual([(r["name"], r["quote"]) for r in found],
                         [("Mike R.", "Their emergency service saved our production line.")])

    def test_a_menu_is_not_a_review(self):
        html = "<header><nav><ul><li><span class='name'>Windows</span><p>All our windows and doors here</p></li></ul></nav></header>"
        self.assertEqual(reviews(dom.build(html), "https://a.com/"), [])

    def test_facts_with_sources_never_from_a_review_or_a_post(self):
        f = Facts()
        home = parse_page("<body><header><a href='tel:+15551234567'>Call</a></header><main><p>Visit us at 12 Main St, Leeds, AL 35094.</p>"
                          "<p>Open Mon-Fri 8am-5pm</p>" + self.REVIEWS + "</main><footer><p>info@acme.com</p></footer></body>",
                          "https://acme.com/")
        quotes = {r["quote"] for r in reviews(dom.build(self.REVIEWS), "https://acme.com/")}
        f.from_page("https://acme.com/", "/", home["blocks"], home["links"], quotes)
        post = parse_page("<main><p>Our supplier is on 555-999-0000.</p></main>", "https://acme.com/blog/supplier")
        f.from_page("https://acme.com/blog/supplier", "/blog/*", post["blocks"], post["links"], set())
        out = f.to_json()
        self.assertEqual([p["value"] for p in out["phone"]], ["+15551234567"])     # not the review's, not the post's
        self.assertEqual(out["phone"][0]["sources"], [{"url": "https://acme.com/", "where": "link"}])
        self.assertEqual([e["value"] for e in out["email"]], ["info@acme.com"])
        self.assertEqual(out["email"][0]["sources"][0]["where"], "header/footer")
        self.assertIn("12 Main St, Leeds, AL 35094", [a["value"] for a in out["address"]])
        self.assertEqual([h["value"] for h in out["hours"]], ["Mon-Fri 8am-5pm"])

    def test_phones_and_actions(self):
        self.assertEqual(phones("Call (941) 473-1566 or 941.473.1566 or 1-941-473-1566"), ["(941) 473-1566"])
        self.assertEqual(action_score("https://calendly.com/acme/visit", "Visit"), 2)
        self.assertEqual(action_score("https://acme.com/quote", "Get a quote"), 1)
        self.assertEqual(action_score("https://instagram.com/acme", "Book now"), 0)
        self.assertEqual(action_score("tel:555", "Book"), 0)

    def test_jsonld_reviews_and_ratings(self):
        items = [{"@type": "LocalBusiness", "aggregateRating": {"@type": "AggregateRating", "ratingValue": "4.9", "reviewCount": "87"},
                  "review": [{"@type": "Review", "author": {"@type": "Person", "name": "Ann"}, "reviewBody": "Superb.",
                              "reviewRating": {"ratingValue": 5}}]}]
        found, ratings = jsonld_reviews(items, "https://a.com/")
        self.assertEqual((found[0]["name"], found[0]["quote"], found[0]["stars"]), ("Ann", "Superb.", 5))
        self.assertEqual((ratings[0]["value"], ratings[0]["count"]), ("4.9", "87"))


class CrawlFolderTests(unittest.TestCase):
    """site.run on a fake site with pictures, into the new layout."""
    SITE = {
        "https://acme.com/": "<html><head><title>Acme</title><meta property='og:image' content='/og.jpg'></head><body>"
                             "<header><a href='/'><img src='/logo.png' alt='Acme logo'></a><nav><a href='/about'>About</a>"
                             "<a href='/contact'>Contact</a><a href='/team'>Team</a></nav></header>"
                             "<main><h1>Roofs</h1><p>%s</p><img src='/crew-800x600.jpg' srcset='/crew-800x600.jpg 800w, /crew-1600x1200.jpg 1600w' alt='Crew'>"
                             "<p>Our crew at work.</p></main><footer><p>© Acme</p></footer></body></html>" % ("We fix roofs. " * 30),
        "https://acme.com/about": "<html><body><main><h1>About</h1><p>%s</p><img src='/crew-1024x768.jpg' alt='Crew again'></main></body></html>" % ("Family firm since 1998. " * 30),
        "https://acme.com/contact": "<html><body><main><h1>Contact</h1><p>Call 555-123-4567.</p></main></body></html>",
        "https://acme.com/team": "<html><body><main><h1>Team</h1><p>%s</p></main></body></html>" % ("Twelve roofers and a dog. " * 30),
    }

    def setUp(self):
        self.saved = (net.fetch_once, net.fetch_bytes, net.is_public_host, site.time.sleep)
        self.saved_driver = chrome.driver
        chrome.driver = lambda choice, **kw: (None, "no browser in tests")
        net.is_public_host = lambda host: True
        site.time.sleep = lambda s: None
        self.fetched_pages, self.fetched_images = [], []

        def fetch_once(url, cap=0, timeout=0, method="GET"):
            html = self.SITE.get(url)
            if method == "GET":
                self.fetched_pages.append(url)
            if html is None:
                return {"status": 404, "final_url": url, "chain": [], "headers": {}, "body": b"", "truncated": False}
            return {"status": 200, "final_url": url, "chain": [], "headers": {},
                    "body": b"" if method == "HEAD" else html.encode(), "truncated": False}

        def fetch_bytes(url, cap, content_types=None, sleep=None):
            self.fetched_images.append(url)
            if url == "https://acme.com/crew.jpg":
                raise net.urllib.error.HTTPError(url, 404, "no", {}, None)   # no original: the widest size is next
            return png(1600 if "1600" in url else 10, 1200 if "1600" in url else 10), "image/png"

        net.fetch_once, net.fetch_bytes = fetch_once, fetch_bytes

    def tearDown(self):
        net.fetch_once, net.fetch_bytes, net.is_public_host, site.time.sleep = self.saved
        chrome.driver = self.saved_driver

    def run_cli(self, *argv, expect=0):
        buf, err = StringIO(), StringIO()
        with redirect_stdout(buf), redirect_stderr(err):
            args = cli.build_parser().parse_args(list(argv))
            code = args.func(args)
        self.assertEqual(code, expect, err.getvalue())
        return json.loads(buf.getvalue().strip().splitlines()[-1]) if code == 0 and buf.getvalue().strip() else err.getvalue()

    def crawl(self, out, *extra, expect=0):
        return self.run_cli("site", "https://acme.com/", "--out", out, "--static",
                            "--delay", "0", *extra, expect=expect)

    def test_layout_pictures_and_facts(self):
        with tempfile.TemporaryDirectory() as out:
            summary = self.crawl(out)
            self.assertEqual(sorted(os.listdir(os.path.join(out, "pages"))), ["about.md", "contact.md", "index.md", "team.md"])
            for name in ("inventory.json", "templates.md", "run.json", "manifest.json", "media.json", "facts.json",
                         "reviews.md", "furniture.json"):
                self.assertTrue(os.path.isfile(os.path.join(out, "_index", name)), name)
            self.assertTrue(os.path.isfile(os.path.join(out, "structured", "index.json")))
            # the crew photo, shown at two sizes on two pages: fetched once, at its largest, linked from both
            with open(os.path.join(out, "_index", "media.json")) as f:
                crew = next(m for m in json.load(f) if m["key"] == "acme.com/crew.jpg")
            self.assertRegex(crew["file"], r"^crew-[0-9a-f]{8}\.png$")
            self.assertEqual((crew["width"], crew["height"]), (1600, 1200))
            self.assertEqual(crew["fetched_from"], "https://acme.com/crew-1600x1200.jpg")
            self.assertEqual([p["url"] for p in crew["pages"]], ["https://acme.com/", "https://acme.com/about"])
            self.assertEqual(crew["pages"][0]["heading"], "Roofs")
            self.assertEqual(crew["pages"][0]["beside"], "Our crew at work.")
            for page in ("index.md", "about.md"):
                with open(os.path.join(out, "pages", page)) as f:
                    self.assertIn("](../images/%s)" % crew["file"], f.read())
            self.assertEqual(self.fetched_images.count("https://acme.com/crew-1600x1200.jpg"), 1)
            self.assertIn("logo-", " ".join(os.listdir(os.path.join(out, "images"))))
            with open(os.path.join(out, "_index", "facts.json")) as f:
                facts = json.load(f)
            self.assertEqual([p["value"] for p in facts["phone"]], ["555-123-4567"])
            self.assertEqual(summary["images"], len(os.listdir(os.path.join(out, "images"))))
            # a second run fetches no picture again
            self.fetched_images.clear()
            self.crawl(out)
            self.assertEqual(self.fetched_images, [])

    def test_a_narrower_run_keeps_the_pictures_an_earlier_one_fetched(self):
        with tempfile.TemporaryDirectory() as out:
            self.crawl(out)
            self.fetched_images.clear()
            self.crawl(out, "--images", "none")
            self.assertEqual(self.fetched_images, [])
            with open(os.path.join(out, "pages", "about.md")) as f:
                self.assertIn("](../images/crew-", f.read())

    def test_images_none_fetches_nothing_but_lists_everything(self):
        with tempfile.TemporaryDirectory() as out:
            summary = self.crawl(out, "--images", "none")
            self.assertEqual(self.fetched_images, [])
            self.assertEqual(summary["images"], 0)
            self.assertGreaterEqual(summary["images_seen"], 3)

    def test_resume_after_an_interruption(self):
        saved_every = site.STATE_EVERY
        site.STATE_EVERY = 1
        real = net.fetch_once

        def dies_on_team(url, **kw):
            if url.endswith("/team"):
                raise KeyboardInterrupt
            return real(url, **kw)
        try:
            with tempfile.TemporaryDirectory() as out:
                net.fetch_once = dies_on_team
                with self.assertRaises(KeyboardInterrupt), redirect_stderr(StringIO()), redirect_stdout(StringIO()):
                    args = cli.build_parser().parse_args(["site", "https://acme.com/", "--out", out, "--static",
                                                          "--delay", "0"])
                    args.func(args)
                read_before = [u for u in self.fetched_pages]
                net.fetch_once = real
                self.fetched_pages.clear()
                summary = self.crawl(out, "--resume")
                self.assertEqual(summary["pages"], 4)
                self.assertNotIn("https://acme.com/", self.fetched_pages)        # not read twice
                self.assertIn("https://acme.com/", read_before)
        finally:
            site.STATE_EVERY = saved_every
            net.fetch_once = real

    def test_the_sites_registry_at_a_fixed_path(self):
        with tempfile.TemporaryDirectory() as root:
            out = os.path.join(root, "raw", "site", "acme.com")
            self.crawl(out, "--images", "none")
            with open(os.path.join(root, "raw", "site", "_sites.json")) as f:
                reg = json.load(f)
            self.assertEqual(reg["latest"]["folder"], out)
            self.assertEqual((reg["latest"]["host"], reg["latest"]["pages"]), ("acme.com", 4))
        with tempfile.TemporaryDirectory() as out:                      # a folder of its own: nothing beside it
            self.crawl(out, "--images", "none")
            self.assertFalse(os.path.exists(os.path.join(os.path.dirname(out), "_sites.json")))

    def test_default_folder_is_the_host(self):
        self.assertEqual(paths.site_dir("https://www.Acme.com/x"), os.path.join("raw", "site", "acme.com"))
        self.assertEqual(paths.site_dir("https://rival.com/", external=True), os.path.join("raw", "external", "rival.com"))
        self.assertEqual(paths.audit_dir("https://acme.com/"), os.path.join("raw", "audit", "acme.com"))


class ChromeTreeTests(unittest.TestCase):
    def test_an_articles_header_is_not_the_sites(self):
        root = dom.build("<header><p id='a'>site</p></header><main><article><header><p id='b'>story</p></header></article></main>")
        by = {n.attrs.get("id"): n for n in root.iter() if n.attrs.get("id")}
        self.assertTrue(by["a"].in_chrome())
        self.assertFalse(by["b"].in_chrome())


if __name__ == "__main__":
    unittest.main()
