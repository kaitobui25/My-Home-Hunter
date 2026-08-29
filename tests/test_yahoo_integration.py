import unittest
from types import SimpleNamespace
from unittest.mock import patch

from src.local.yahoo_integration import _prepare_coordinates, run_yahoo_search


class DummyGeocoder:
    def __init__(self):
        self.calls = 0

    def get_coordinates(self, address):
        self.calls += 1
        return 34.7, 135.4

    def calculate_distance(self, lat1, lng1, lat2, lng2):
        return 1.0


class DummyFilter:
    def matches(self, listing):
        return True


class DummyTelegram:
    def __init__(self):
        self.cfg = SimpleNamespace(enabled=False, bot_token="", chat_id="")

    def send_batch(self, listings, search_name=""):
        raise AssertionError("Telegram should be disabled in this test")


class YahooIntegrationTests(unittest.TestCase):
    def test_prepare_coordinates_keeps_yahoo_coordinates(self):
        listing = {"address": "Osaka", "lat": 34.75, "lng": 135.48}
        geocoder = DummyGeocoder()
        loc_cfg = SimpleNamespace(enabled=False)

        _prepare_coordinates(listing, geocoder, loc_cfg)

        self.assertEqual(geocoder.calls, 0)
        self.assertEqual(listing["lat"], 34.75)
        self.assertEqual(listing["lng"], 135.48)

    def test_prepare_coordinates_falls_back_to_geocoder(self):
        listing = {"address": "Osaka", "lat": None, "lng": None}
        geocoder = DummyGeocoder()
        loc_cfg = SimpleNamespace(enabled=False)

        _prepare_coordinates(listing, geocoder, loc_cfg)

        self.assertEqual(geocoder.calls, 1)
        self.assertEqual(listing["lat"], 34.7)
        self.assertEqual(listing["lng"], 135.4)

    @patch("src.local.yahoo_integration.YahooRentalHunter")
    def test_run_yahoo_search_persists_listing_without_regeocoding(self, hunter_cls):
        hunter_cls.return_value.scrape.return_value = [
            {
                "url": "https://realestate.yahoo.co.jp/rent/detail/test-room/",
                "name": "Yahoo room",
                "address": "Osaka",
                "lat": 34.75,
                "lng": 135.48,
                "price_man_yen": 6.5,
                "size_m2": 35.0,
            }
        ]
        search = SimpleNamespace(name="Yahoo Test", type="rental")
        config = SimpleNamespace(
            general=SimpleNamespace(),
            filters=SimpleNamespace(
                location_filter=SimpleNamespace(enabled=False)
            ),
        )
        geocoder = DummyGeocoder()

        seen, canonical = run_yahoo_search(
            search=search,
            config=config,
            seen={},
            listing_filter=DummyFilter(),
            telegram=DummyTelegram(),
            geocoder=geocoder,
        )

        url = "https://realestate.yahoo.co.jp/rent/detail/test-room/"
        self.assertIn(url, canonical)
        self.assertIn(url, seen)
        self.assertEqual(seen[url]["search_name"], "Yahoo Test")
        self.assertEqual(seen[url]["lat"], 34.75)
        self.assertEqual(seen[url]["lng"], 135.48)
        self.assertEqual(geocoder.calls, 0)


if __name__ == "__main__":
    unittest.main()
