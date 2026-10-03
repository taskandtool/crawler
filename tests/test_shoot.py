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
        return {"strips": ["01.png", "02.png"], "height": 3000, "truncated": False}

    def stop(self):
        self.stopped = True


class ShootTest(unittest.TestCase):
    def test_folder_names_come_from_the_path(self):
        self.assertEqual(shoot.name_for("http://localhost:3000/"), "home")
        self.assertEqual(shoot.name_for("http://localhost:3000/services/kitchens/"), "services-kitchens")

    def test_each_width_lands_in_its_own_folder_and_the_browser_stops(self):
        d = FakeDriver()
        results = shoot.shoot("http://localhost:3000/", [1280, 390], "uploads", driver=d)
        self.assertEqual([w for w, _ in results], [1280, 390])
        self.assertEqual([m["dir"] for _, m in results], ["uploads/home-1280", "uploads/home-390"])
        self.assertTrue(d.stopped)

    def test_the_summary_says_where_to_look_and_what_next(self):
        results = shoot.shoot("http://localhost:3000/", [1280], "uploads", driver=FakeDriver())
        text = shoot.report("http://localhost:3000/", results, False)
        self.assertIn("uploads/home-1280/01.png … 02.png", text)
        self.assertIn("read in order", text)
        self.assertIn("Next:", text)

    def test_a_failure_says_how_to_check_the_page_is_served(self):
        results = shoot.shoot("http://localhost:3000/", [1280], "uploads", driver=FakeDriver(fail=True))
        text = shoot.report("http://localhost:3000/", results, False)
        self.assertIn("failed", text)
        self.assertIn("curl", text)

    def test_only_the_named_local_host_gets_through_the_guard(self):
        guard = cdp.RequestGuard(allow_hosts=("localhost",))
        self.assertTrue(guard.allowed("http://localhost:3000/site.css"))
        self.assertFalse(guard.allowed("http://192.168.1.1/admin"))
        self.assertFalse(cdp.RequestGuard().allowed("http://localhost:3000/"))


if __name__ == "__main__":
    unittest.main()
