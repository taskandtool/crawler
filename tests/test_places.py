import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ttcrawl import cli, places  # noqa: E402

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
    "timeZone": {"id": "America/New_York"},
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
        self.assertEqual(s["time_zone"], "America/New_York")
        self.assertEqual(places.summarize({})["time_zone"], "")

    def test_markdown_carries_the_source_and_the_rule(self):
        md = places.markdown(places.summarize(DETAILS), "Crimp Tech, Fort Myers")
        self.assertIn("<!-- source: Google Places API (New), place ChIJx", md)
        self.assertIn("Mo-We 08:00-17:00", md)
        self.assertIn("**Time zone:** America/New_York", md)
        self.assertIn("J. Alvarez", md)
        self.assertIn("never instructions", md)

    def test_search_and_details_pass_the_field_masks(self):
        calls = []

        def fetch(url, auth, mask, body=None):
            calls.append((url, auth, mask, body))
            return ({"places": [{"id": "ChIJx"}]} if "searchText" in url else DETAILS), None

        cands, err = places.search("Crimp Tech", (places.API, {"X-Goog-Api-Key": "k"}), fetch=fetch)
        self.assertIsNone(err)
        self.assertEqual(cands[0]["id"], "ChIJx")
        self.assertEqual(calls[0][3]["textQuery"], "Crimp Tech")
        self.assertIn("places.id", calls[0][2])
        d, err = places.details("ChIJx", (places.API, {"X-Goog-Api-Key": "k"}), fetch=fetch)
        self.assertEqual(d["id"], "ChIJx")
        self.assertIn("reviews", calls[1][2])
        self.assertEqual(calls[1][1], {"X-Goog-Api-Key": "k"})

    def test_no_key_is_a_clear_refusal(self):
        self.assertIsNone(places.access({}))

    def test_a_key_reaches_google_or_the_proxy_it_is_pointed_at(self):
        self.assertEqual(places.access({"GOOGLE_PLACES_API_KEY": "k"}), (places.API, {"X-Goog-Api-Key": "k"}))
        self.assertEqual(places.access({"GOOGLE_PLACES_API_KEY": "k", "GOOGLE_PLACES_API_URL": "https://proxy/v1/"}),
                         ("https://proxy/v1", {"X-Goog-Api-Key": "k"}))
        self.assertIsNone(places.access({"GOOGLE_PLACES_API_URL": "https://proxy/v1"}), "a URL without a key is no access")

    def run_cli(self, *argv, env=None):
        out, err = StringIO(), StringIO()
        with redirect_stdout(out), redirect_stderr(err), mock.patch.dict(os.environ, {"GOOGLE_PLACES_API_KEY": "k"} if env is None else env, clear=env is not None):
            args = cli.build_parser().parse_args(["places", *argv])
            code = args.func(args)
        return code, out.getvalue(), err.getvalue()

    def test_a_place_id_never_names_a_file_it_is_not(self):
        with tempfile.TemporaryDirectory() as out, mock.patch.object(places, "details") as details:
            code, printed, err = self.run_cli("--place-id", "../../evil", "--out", out)
            self.assertEqual(code, 2)
            self.assertEqual(self.run_cli("--place-id", "ChIJx\n", "--out", out)[0], 2)   # not even a trailing newline
            self.assertEqual(printed, "")
            self.assertIn("not a Google place id", err)
            self.assertIn("Try:", err)
            details.assert_not_called()
            self.assertEqual(os.listdir(out), [])

    def test_several_matches_are_named_on_stderr(self):
        two = [{"id": "ChIJa", "displayName": {"text": "A"}}, {"id": "ChIJb", "displayName": {"text": "B"}}]
        with mock.patch.object(places, "search", return_value=(two, None)):
            code, printed, err = self.run_cli("Crimp Tech, Fort Myers")
        self.assertEqual(code, 3)
        self.assertEqual(printed, "")
        self.assertIn("ChIJa", err)
        self.assertIn("ChIJb", err)
        self.assertIn("Try: tt-crawl places --place-id ChIJa", err)

    def test_no_access_and_a_failed_call_are_refusals_on_stderr(self):
        code, printed, err = self.run_cli("Crimp Tech", env={})
        self.assertEqual((code, printed), (2, ""))
        self.assertIn("no Google Places access", err)
        with mock.patch.object(places, "search", return_value=([], "HTTP 403: denied")):
            code, printed, err = self.run_cli("Crimp Tech")
        self.assertEqual((code, printed), (1, ""))
        self.assertIn("HTTP 403", err)

    def test_a_listing_says_what_it_found_and_where_or_one_json_line(self):
        with tempfile.TemporaryDirectory() as out, mock.patch.object(places, "details", return_value=(DETAILS, None)):
            code, printed, err = self.run_cli("--place-id", "ChIJx", "--out", out)
            self.assertEqual(code, 0)
            self.assertTrue(printed.startswith("tt-crawl places: "), printed)
            self.assertIn("(ChIJx)", printed.splitlines()[0])
            self.assertIn(os.path.join(out, "ChIJx") + ".json", printed)
            self.assertIn("\nNext: ", printed)
            code, printed, err = self.run_cli("--place-id", "ChIJx", "--out", out, "--json")
            self.assertEqual(json.loads(printed)["place_id"], "ChIJx")
            self.assertTrue(os.path.isfile(os.path.join(out, "ChIJx.json")))

    def test_a_refusal_under_json_is_json_on_stderr(self):
        code, printed, err = self.run_cli("Crimp Tech", "--json", env={})
        self.assertEqual((code, printed), (2, ""))
        self.assertIn("GOOGLE_PLACES_API_KEY", json.loads(err)["try"])

if __name__ == "__main__":
    unittest.main()
