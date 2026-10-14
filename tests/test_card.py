"""The business card: a crawl's facts in a listing's forms, each naming its file."""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ttcrawl import card  # noqa: E402


def folder(root, facts=None, markup=None, records=None, media=None, files=()):
    """A crawl folder holding just the files the card reads."""
    for d in ("_index", "structured", "images"):
        os.makedirs(os.path.join(root, d), exist_ok=True)
    for rel, data in (("_index/facts.json", facts or {}), ("structured/business.json", markup or {}),
                      ("_index/inventory.json", {"records": records or []}), ("_index/media.json", media or [])):
        with open(os.path.join(root, rel), "w") as f:
            json.dump(data, f)
    for name in files:
        open(os.path.join(root, "images", name), "wb").close()
    return root


def fact(value):
    return {"value": value, "sources": [{"url": "https://acme.com/", "where": "header/footer"}]}


class HoursTest(unittest.TestCase):
    def test_lines_as_schema_strings_runs_joined_in_day_order(self):
        lines = ["Monday: 7am – 3pm", "Tuesday: 7am – 3pm", "Wed 10:30am-4pm", "Thursday: 7am – 3pm",
                 "Saturday: 8am – 2pm", "Sunday: CLOSED"]
        self.assertEqual(card.schema_hours(lines), ["Mo-Tu 07:00-15:00", "We 10:30-16:00", "Th 07:00-15:00",
                                                    "Sa 08:00-14:00"])

    def test_noon_and_midnight(self):
        self.assertEqual(card.schema_hours(["Fri 12pm - 12am"]), ["Fr 12:00-00:00"])

    def test_a_range_of_days_is_left_to_read(self):
        self.assertEqual(card.schema_hours(["Mon-Fri 8am-5pm"]), [])


class AddressTest(unittest.TestCase):
    def test_the_markups_postal_address(self):
        a = {"streetAddress": "12 Dock Rd", "addressLocality": "Leeds", "addressRegion": "AL", "postalCode": "35094",
             "addressCountry": {"name": "US"}}
        self.assertEqual(card.address_from(a, "ignored"), ({"street": "12 Dock Rd", "locality": "Leeds", "region": "AL",
                                                            "postal_code": "35094", "country": "US"}, True))

    def test_a_us_line_is_split_and_any_other_kept_whole(self):
        self.assertEqual(card.address_from(None, "1952 Main Street, Sarasota, FL 34236, USA")[0],
                         {"street": "1952 Main Street", "locality": "Sarasota", "region": "FL", "postal_code": "34236",
                          "country": "US"})
        self.assertEqual(card.address_from(None, "4 High St, Leeds LS1 4AP"),
                         ({"street": "4 High St, Leeds LS1 4AP", "locality": "", "region": "", "postal_code": "",
                           "country": ""}, False))
        self.assertEqual(card.address_from(None, ""), (None, False))

    def test_a_time_zone_only_where_the_address_settles_it(self):
        self.assertEqual(card.time_zone({"region": "NY", "country": "US"}), "America/New_York")
        self.assertEqual(card.time_zone({"region": "FL", "country": "US"}), "", "Florida has two zones")
        self.assertEqual(card.time_zone({"region": "", "country": "United Kingdom"}), "Europe/London")
        self.assertEqual(card.time_zone(None), "")


class ProfilesTest(unittest.TestCase):
    def test_each_profile_once_in_its_https_form(self):
        self.assertEqual(card.profiles(["http://facebook.com/CedarFoxCoffee", "http://instagram.com/cedarfoxcoffee",
                                        "https://www.facebook.com/CedarFoxCoffee/", "https://www.instagram.com/cmill31"]),
                         ["https://www.facebook.com/CedarFoxCoffee/", "http://instagram.com/cedarfoxcoffee",
                          "https://www.instagram.com/cmill31"])


class BuildTest(unittest.TestCase):
    def test_the_card_from_a_crawl_folder(self):
        with tempfile.TemporaryDirectory() as root:
            folder(root,
                   facts={"phone": [fact("941-330-8494")], "email": [fact("hi@acme.com")],
                          "address": [fact("1952 Main Street, Sarasota, FL 34236")],
                          "hours": [fact("Monday: 7am – 3pm"), fact("Sunday: Closed")],
                          "social": [fact("https://instagram.com/acme")]},
                   markup={"sameAs": ["https://facebook.com/acme", "https://instagram.com/acme"]},
                   records=[{"url": "https://acme.com/", "title": "Acme Coffee | Sarasota", "meta_description": "Coffee."}],
                   media=[{"kind": "logo", "file": "small.png", "width": 200, "height": 80},
                          {"kind": "logo", "file": "big.png", "width": 1200, "height": 480},
                          {"kind": "photo", "file": "narrow.jpg", "width": 410, "height": 300},
                          {"kind": "photo", "file": "wide.jpg", "width": 1920, "height": 1080, "alts": ["The bar"],
                           "pages": [{"heading": "Visit us"}]},
                          {"kind": "photo", "file": "gone.jpg", "width": 2000, "height": 1000}],
                   files=("small.png", "big.png", "narrow.jpg", "wide.jpg"))
            c = card.write(root)
            with open(os.path.join(root, "_index", "business-card.json")) as f:
                self.assertEqual(json.load(f), c)
        self.assertEqual(c["name"], "Acme Coffee")
        self.assertEqual((c["telephone"], c["email"]), ("941-330-8494", "hi@acme.com"))
        self.assertEqual(c["address"]["locality"], "Sarasota")
        self.assertEqual(c["opening_hours"], ["Mo 07:00-15:00"])
        self.assertEqual(c["same_as"], ["https://instagram.com/acme", "https://facebook.com/acme"])
        self.assertEqual(c["description"], "Coffee.")
        self.assertEqual(c["logo"], {"file": "images/big.png", "width": 1200, "height": 480})
        # on disk, widest first
        self.assertEqual(c["photos"], [{"file": "images/wide.jpg", "width": 1920, "height": 1080, "alt": "The bar",
                                        "heading": "Visit us"},
                                       {"file": "images/narrow.jpg", "width": 410, "height": 300, "alt": "", "heading": ""}])
        self.assertEqual(c["sources"], {"name": "_index/inventory.json", "telephone": "_index/facts.json",
                                        "email": "_index/facts.json", "address": "_index/facts.json",
                                        "opening_hours": "_index/facts.json", "same_as": "_index/facts.json",
                                        "description": "_index/inventory.json", "logo": "_index/media.json",
                                        "photos": "_index/media.json"})

    def test_the_markup_wins_where_it_says_it(self):
        with tempfile.TemporaryDirectory() as root:
            folder(root,
                   facts={"hours": [fact("Monday: 9am – 5pm")], "phone": [fact("555-000-1111")]},
                   markup={"name": "Acme Ltd", "openingHours": ["Mo-Fr 08:00-17:00", "by appointment"],
                           "address": {"streetAddress": "1 Park Ave", "addressLocality": "New York",
                                       "addressRegion": "NY", "postalCode": "10016"},
                           "description": "Since 1990."},
                   records=[{"url": "https://acme.com/", "title": "Home"}])
            c = card.build(root)
        self.assertEqual(c["name"], "Acme Ltd")
        self.assertEqual(c["opening_hours"], ["Mo-Fr 08:00-17:00"])
        self.assertEqual(c["time_zone"], "America/New_York")
        self.assertEqual(c["telephone"], "555-000-1111", "the site's own words first")
        self.assertEqual({k: c["sources"][k] for k in ("name", "address", "opening_hours", "time_zone", "description")},
                         dict.fromkeys(("name", "address", "opening_hours", "time_zone", "description"),
                                       "structured/business.json"))
        self.assertEqual((c["logo"], c["photos"]), (None, []))

    def test_an_empty_folder_is_an_empty_card(self):
        with tempfile.TemporaryDirectory() as root:
            os.makedirs(os.path.join(root, "_index"))
            c = card.build(root)
        self.assertEqual((c["name"], c["address"], c["opening_hours"], c["sources"]), ("", None, [], {}))


if __name__ == "__main__":
    unittest.main()


class NameFromTitleTest(unittest.TestCase):
    def test_a_page_word_before_the_name_is_passed_over(self):
        self.assertEqual(card.name_from_title("Home | PGT Windows & Doors | Impact Windows", "www.pgtwindows.com"), "PGT Windows & Doors")
        self.assertEqual(card.name_from_title("Welcome - Harlow Bakery", "harlowbakery.com"), "Harlow Bakery")

    def test_the_part_that_shares_a_word_with_the_domain_wins(self):
        self.assertEqual(card.name_from_title("Fresh bread daily | Harlow Bakery", "harlowbakery.com"), "Harlow Bakery")

    def test_without_a_match_the_first_named_part_stands(self):
        self.assertEqual(card.name_from_title("Sunshine Biscotti", "www.sunshinebiscotti.com"), "Sunshine Biscotti")
        self.assertEqual(card.name_from_title("Acme | Tools", "example.com"), "Acme")
        self.assertEqual(card.name_from_title("Home", "example.com"), "")
