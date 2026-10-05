"""The page parser, furniture, structured data, media, and inventory on
fixture HTML."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fixtures import BUSINESS_PAGE, NO_LANDMARKS_PAGE  # noqa: E402
from ttcrawl import inventory, structured  # noqa: E402
from ttcrawl.furniture import page_furniture, site_furniture  # noqa: E402
from ttcrawl.html import parse_page  # noqa: E402
from ttcrawl.media import Media, guess_kind  # noqa: E402

URL = "https://acme.com/"


class ParseTests(unittest.TestCase):
    def setUp(self):
        self.p = parse_page(BUSINESS_PAGE, URL)

    def test_head(self):
        p = self.p
        self.assertEqual(p["title"], "Acme Roofing | Leeds")
        self.assertEqual(p["meta_description"], "Roofs in Leeds since 1998")
        self.assertEqual(p["canonical"], "https://acme.com/")
        self.assertEqual(p["lang"], "en-GB")
        self.assertFalse(p["noindex"])
        self.assertEqual(p["hreflang"], [{"lang": "fr", "href": "https://acme.com/fr/"}])
        self.assertEqual(p["h1"], "Roofs that last")
        self.assertEqual(p["h1_count"], 1)
        self.assertEqual(p["meta"]["og:image"], "/img/og.jpg")

    def test_links_carry_their_landmark(self):
        by = {(l["href"], l["text"]): l for l in self.p["links"]}
        self.assertEqual(by[("https://acme.com/services", "Services")]["landmark"], "nav")
        self.assertEqual(by[("https://acme.com/services/flat-roofs", "Flat roofs")]["depth"], 2)
        self.assertEqual(by[("https://acme.com/about", "team")]["landmark"], None)
        self.assertEqual(by[("https://acme.com/careers", "Careers")]["group"], "Company")
        self.assertEqual(by[("https://acme.com/privacy", "Privacy policy")]["group"], "Legal")
        self.assertTrue(self.p["has_landmarks"])
        self.assertIn("Call us", self.p["landmark_lines"]["header"])

    def test_forms_images_iframes(self):
        f = self.p["forms"][0]
        self.assertEqual(f["action"], "https://acme.com/contact")
        self.assertEqual([x["name"] for x in f["fields"]], ["name", "email", "message", ""])
        self.assertTrue(f["fields"][0]["required"])
        self.assertEqual(self.p["iframes"], ["https://www.google.com/maps/embed?pb=1"])
        crew = next(i for i in self.p["images"] if "crew" in i["src"])
        self.assertEqual(crew["srcset"], ["https://acme.com/img/crew-800x600.jpg", "https://acme.com/img/crew-1600x1200.jpg"])
        self.assertEqual((crew["width"], crew["height"]), (800, 600))
        logo = next(i for i in self.p["images"] if "logo" in i["src"])
        self.assertEqual(logo["landmark"], "header")
        self.assertTrue(logo["in_link"])

    def test_microdata_and_jsonld_raw(self):
        self.assertEqual(self.p["microdata"][0]["props"], {"name": "Jane Doe", "jobTitle": "Owner"})
        self.assertIn("RoofingContractor", self.p["jsonld_raw"][0])

    def test_a_gallery_link_to_a_photograph_is_the_pages_picture(self):
        # a lightbox gallery: the full-size file is the link, the thumbnail is drawn by script
        p = parse_page("<html><body><main><a class='e-gallery-item' href='/uploads/2025/07/2.jpg?v=1'>"
                       "<div class='thumb'></div></a><a href='/about'>About</a></main></body></html>", URL)
        srcs = [i["src"] for i in p["images"]]
        self.assertIn("https://acme.com/uploads/2025/07/2.jpg?v=1", srcs)
        self.assertEqual(len(srcs), 1)

    def test_bad_html_never_raises(self):
        p = parse_page("<html><body><a href='/x'>unclosed <b>tags", URL)
        self.assertEqual(p["links"][0]["href"], "https://acme.com/x")
        self.assertEqual(p["links"][0]["text"], "unclosed tags")


class FurnitureTests(unittest.TestCase):
    def setUp(self):
        self.p = parse_page(BUSINESS_PAGE, URL)
        self.f = page_furniture(self.p, "acme.com")

    def test_nav_tree_cta_footer(self):
        nav = self.f["nav"]
        self.assertEqual([n["label"] for n in nav], ["Services", "About"])
        self.assertEqual([c["label"] for c in nav[0]["children"]], ["Flat roofs"])
        self.assertEqual(self.f["primary_cta"], {"label": "Call us", "href": "tel:+441135550100"})
        groups = {g["heading"]: [l["label"] for l in g["links"]] for g in self.f["footer_groups"]}
        self.assertEqual(groups, {"Company": ["About", "Careers"]})
        self.assertEqual([l["label"] for l in self.f["legal"]], ["Privacy policy", "Terms"])
        self.assertEqual([s["href"] for s in self.f["social"]], ["https://instagram.com/acme", "https://www.facebook.com/acme"])
        self.assertIn("2026 Acme Roofing", self.f["copyright"])
        self.assertEqual(self.f["logo"], {"src": "https://acme.com/img/logo.svg", "alt": "Acme Roofing"})
        self.assertEqual(self.f["badges"][0]["src"], "https://acme.com/img/bbb-badge.png")

    def test_site_view_and_landmark_lines(self):
        other = parse_page(BUSINESS_PAGE.replace("<li><a href=\"/about\">About</a></li>", ""), "https://acme.com/about")
        s = site_furniture([self.f, page_furniture(other, "acme.com")])
        self.assertTrue(s["has_landmarks"])
        self.assertEqual(s["from"], URL)
        self.assertEqual(s["pages_with_a_different_nav"], ["https://acme.com/about"])
        chrome = {b["text"] for b in self.p["blocks"] if b["chrome"]}
        content = {b["text"] for b in self.p["blocks"] if not b["chrome"]}
        self.assertTrue(any("Privacy policy" in t for t in chrome))
        self.assertTrue(any("Call us" in t for t in chrome))
        self.assertTrue(any(t.startswith("We fix roofs across Leeds") for t in content))

    def test_no_landmarks(self):
        p = parse_page(NO_LANDMARKS_PAGE, "https://old.com/")
        self.assertFalse(p["has_landmarks"])
        f = page_furniture(p, "old.com")
        self.assertEqual(f["nav"], [])
        self.assertFalse(site_furniture([f])["has_landmarks"])


class StructuredTests(unittest.TestCase):
    def setUp(self):
        self.p = parse_page(BUSINESS_PAGE, URL)
        self.s = structured.page_structured(self.p, BUSINESS_PAGE)

    def test_jsonld_og_tracking_embeds(self):
        self.assertEqual(self.s["jsonld_types"], ["RoofingContractor"])
        self.assertEqual(self.s["open_graph"]["og:title"], "Acme Roofing")
        self.assertEqual(self.s["tracking"]["ga4"], ["G-ABC123XYZ"])
        self.assertEqual(self.s["tracking"]["meta_pixel"], ["123456789012"])
        self.assertEqual(self.s["tracking"]["google_site_verification"], ["abc123verify"])
        self.assertEqual(self.s["embeds"][0]["kind"], "map")

    def test_jsonld_tolerates_graph_and_trailing_commas(self):
        p = parse_page('<script type="application/ld+json">{"@graph":[{"@type":"Organization","name":"A",},{"@type":"WebSite"}]}</script>', URL)
        items = structured.jsonld(p)
        self.assertEqual(structured.jsonld_types(items), ["Organization", "WebSite"])

    def test_merge_business(self):
        b = structured.merge_business([(URL, self.s["jsonld"]), ("https://acme.com/about", [{"@type": "Organization", "name": "Other", "sameAs": ["https://x.com/acme"]}])])
        self.assertEqual(b["name"], "Acme Roofing")
        self.assertEqual(b["telephone"], "+44 113 555 0100")
        self.assertEqual(b["sameAs"], ["https://instagram.com/acme", "https://x.com/acme"])
        self.assertEqual(b["sources"]["name"], [URL])
        self.assertIsNone(structured.merge_business([(URL, [{"@type": "WebSite"}])]))


class MediaTests(unittest.TestCase):
    def test_guess(self):
        self.assertEqual(guess_kind({"src": "https://a.com/img/logo.svg", "alt": ""}), "logo")
        self.assertEqual(guess_kind({"src": "https://a.com/icons/check.svg", "alt": ""}), "icon")
        self.assertEqual(guess_kind({"src": "https://images.unsplash.com/photo-1.jpg", "alt": "x"}), "stock")
        self.assertEqual(guess_kind({"src": "https://a.com/wp-content/themes/x/bg-pattern.png", "alt": ""}), "theme")
        self.assertEqual(guess_kind({"src": "https://a.com/uploads/crew.jpg", "alt": "crew"}), "photo")

    def test_other_peoples_logos_are_marks(self):
        # named for what it is
        self.assertEqual(guess_kind({"src": "https://a.com/uploads/nahad-member-badge.png", "alt": ""}), "mark")
        # a strip or carousel logo: short and wide, no alt
        self.assertEqual(guess_kind({"src": "https://a.com/uploads/47381.png", "alt": "", "width": 320, "height": 90}), "mark")
        # a small picture in the footer
        self.assertEqual(guess_kind({"src": "https://a.com/uploads/Unknown.png", "alt": "", "width": 99, "height": 43,
                                     "landmark": "footer"}), "mark")
        # a picture file the page shows at badge size
        self.assertEqual(guess_kind({"src": "https://a.com/uploads/Unknown-1.png", "alt": "", "width": 496, "height": 396,
                                     "shown": 55}), "mark")
        # a gallery thumbnail stays a photograph
        self.assertEqual(guess_kind({"src": "https://a.com/uploads/job-3.jpg", "alt": "", "width": 2000, "height": 1500,
                                     "shown": 200}), "photo")
        # a wide photograph stays a photograph
        self.assertEqual(guess_kind({"src": "https://a.com/uploads/panorama.jpg", "alt": "", "width": 2400, "height": 600}), "photo")

    def test_media_one_picture_across_its_sizes(self):
        p = parse_page(BUSINESS_PAGE, URL)
        media = Media()
        media.add_page(URL, p["blocks"], p["images"])
        media.add_page("https://acme.com/about", p["blocks"], p["images"])
        media.classify("https://acme.com/img/logo.svg")
        crew = media.items["acme.com/img/crew.jpg"]
        self.assertEqual([x["url"] for x in crew["pages"]], [URL, "https://acme.com/about"])
        self.assertEqual(crew["pages"][0]["heading"], "Roofs that last")
        self.assertEqual(Media.candidates(crew)[:2], ["https://acme.com/img/crew.jpg", "https://acme.com/img/crew-1600x1200.jpg"])
        self.assertEqual(crew["kind"], "photo")
        self.assertFalse(crew["chrome"])
        self.assertEqual(media.items["acme.com/img/logo.svg"]["kind"], "logo")
        chosen = {i["key"] for i in media.select("content")}
        self.assertIn("acme.com/img/crew.jpg", chosen)
        self.assertIn("acme.com/img/logo.svg", chosen)
        self.assertNotIn("acme.com/img/bbb-badge.png", chosen)            # the footer's badge is not content
        self.assertIn("acme.com/img/bbb-badge.png", {i["key"] for i in media.select("all")})
        self.assertEqual(media.select("none"), [])


class InventoryTests(unittest.TestCase):
    def test_inbound_counts_and_markdown(self):
        recs = {u: inventory.new_record(u) for u in (URL, "https://acme.com/about", "https://acme.com/services")}
        inventory.add_inbound(recs, URL, "https://acme.com/about", sitewide=True)
        inventory.add_inbound(recs, URL, "https://acme.com/about", sitewide=True)
        inventory.add_inbound(recs, "https://acme.com/services", "https://acme.com/about", sitewide=False)
        inventory.add_inbound(recs, URL, "https://acme.com/nope", sitewide=False)
        about = recs["https://acme.com/about"]
        self.assertEqual(about["inbound_sitewide"], {"count": 1, "from": [URL]})
        self.assertEqual(about["inbound_body"], {"count": 1, "from": ["https://acme.com/services"]})
        recs[URL]["tracking"] = {"ga4": ["G-1"]}
        recs[URL]["file"] = "index.md"
        md = inventory.markdown(sorted(recs.values(), key=lambda r: r["url"]), URL)
        self.assertIn("| https://acme.com/about |", md)
        self.assertIn("ga4: G-1", md)


if __name__ == "__main__":
    unittest.main()
