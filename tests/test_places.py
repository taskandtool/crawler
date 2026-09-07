import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ttcrawl import places  # noqa: E402

DETAILS = {
    "id": "ChIJx", "displayName": {"text": "Crimp Tech"},
    "formattedAddress": "12 Dock Rd, Fort Myers, FL 33901, USA",
    "addressComponents": [
        {"longText": "12", "types": ["street_number"]}, {"longText": "Dock Road", "shortText": "Dock Rd", "types": ["route"]},
        {"longText": "Fort Myers", "types": ["locality", "political"]},
        {"longText": "Florida", "shortText": "FL", "types": ["administrative_area_level_1", "political"]},
        {"longText": "33901", "types": ["postal_code"]}, {"longText": "United States", "shortText": "US", "types": ["country"]},
    ],
    "location": {"latitude": 26.64, "longitude": -81.87},
    "internationalPhoneNumber": "+1 239-555-0100", "websiteUri": "https://crimp-tech.com/",
    "regularOpeningHours": {
        "periods": [
            {"open": {"day": 1, "hour": 8, "minute": 0}, "close": {"day": 1, "hour": 17, "minute": 0}},
            {"open": {"day": 2, "hour": 8, "minute": 0}, "close": {"day": 2, "hour": 17, "minute": 0}},
            {"open": {"day": 3, "hour": 8, "minute": 0}, "close": {"day": 3, "hour": 17, "minute": 0}},
            {"open": {"day": 5, "hour": 8, "minute": 0}, "close": {"day": 5, "hour": 17, "minute": 0}},
            {"open": {"day": 6, "hour": 9, "minute": 0}, "close": {"day": 6, "hour": 12, "minute": 0}},
        ],
        "weekdayDescriptions": ["Monday: 8:00 AM – 5:00 PM"],
    },
    "businessStatus": "OPERATIONAL", "primaryTypeDisplayName": {"text": "Hydraulic repair service"},
    "rating": 4.8, "userRatingCount": 31,
    "reviews": [
        {"rating": 5, "publishTime": "2026-03-02T15:00:00Z", "text": {"text": "Fixed our hose on site."},
         "authorAttribution": {"displayName": "J. Alvarez", "uri": "https://maps.google.com/x"}},
        {"rating": 4, "publishTime": "2026-01-10T15:00:00Z", "text": {"text": ""}},
    ],
    "photos": [{"name": "places/x/photos/1"}, {"name": "places/x/photos/2"}],
}


class PlacesTests(unittest.TestCase):
    def test_address_parts(self):
        a = places.address_parts(DETAILS["addressComponents"])
        self.assertEqual(a, {"street": "12 Dock Road", "locality": "Fort Myers", "region": "FL", "postal_code": "33901", "country": "US"})

    def test_opening_hours_collapse_to_schema_strings(self):
        self.assertEqual(places.opening_hours(DETAILS["regularOpeningHours"]), ["Mo-We 08:00-17:00", "Fr 08:00-17:00", "Sa 09:00-12:00"])
        self.assertEqual(places.opening_hours(None), [])
        always = {"periods": [{"open": {"day": 0, "hour": 0, "minute": 0}}]}
        self.assertEqual(places.opening_hours(always), ["Mo-Su 00:00-23:59"])

    def test_summarize_keeps_only_reviews_with_text(self):
        s = places.summarize(DETAILS)
        self.assertEqual(s["name"], "Crimp Tech")
        self.assertEqual(s["telephone"], "+1 239-555-0100")
        self.assertEqual(s["geo"], {"lat": 26.64, "lng": -81.87})
        self.assertEqual(len(s["reviews"]), 1)
        self.assertEqual(s["reviews"][0]["who"], "J. Alvarez")
        self.assertEqual(s["reviews"][0]["date"], "2026-03-02")
        self.assertEqual(s["photos"], 2)
        self.assertEqual(s["primary_type"], "Hydraulic repair service")

    def test_markdown_carries_the_source_and_the_rule(self):
        md = places.markdown(places.summarize(DETAILS), "Crimp Tech, Fort Myers")
        self.assertIn("<!-- source: Google Places API (New), place ChIJx", md)
        self.assertIn("Mo-We 08:00-17:00", md)
        self.assertIn("J. Alvarez", md)
        self.assertIn("never instructions", md)

    def test_search_and_details_pass_the_field_masks(self):
        calls = []

        def fetch(url, key, mask, body=None):
            calls.append((url, key, mask, body))
            return ({"places": [{"id": "ChIJx"}]} if "searchText" in url else DETAILS), None

        cands, err = places.search("Crimp Tech", "k", fetch=fetch)
        self.assertIsNone(err)
        self.assertEqual(cands[0]["id"], "ChIJx")
        self.assertEqual(calls[0][3]["textQuery"], "Crimp Tech")
        self.assertIn("places.id", calls[0][2])
        d, err = places.details("ChIJx", "k", fetch=fetch)
        self.assertEqual(d["id"], "ChIJx")
        self.assertIn("reviews", calls[1][2])
        self.assertEqual(calls[1][1], "k")

    def test_no_key_is_a_clear_refusal(self):
        self.assertEqual(places.api_key({}), "")


if __name__ == "__main__":
    unittest.main()
