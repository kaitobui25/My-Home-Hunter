import unittest

from src.config import (
    AppConfig,
    CsvExportConfig,
    ExportConfig,
    FiltersConfig,
    GeneralConfig,
    NotificationsConfig,
    SearchConfig,
    TelegramConfig,
)
from src.filter import ListingFilter
from src.local.run_local import _canonical_url, _mark_as_seen, run_refilter
from src.scraper.base import AbstractHunter


class DummyHunter(AbstractHunter):
    def scrape(self):
        return [{"url": "https://example.com/listing?bc=123", "name": "Test Listing"}]

    def _save_seen(self):
        raise AssertionError("Global seen should not be persisted during run()")


class DummyTelegram:
    def __init__(self, sent_count: int = 1):
        self.sent_count = sent_count

    def send_batch(self, listings, search_name=""):
        return self.sent_count


class DummyGeocoder:
    def get_coordinates(self, address: str):
        return 35.0, 139.0

    def calculate_distance(self, lat1, lng1, lat2, lng2):
        return 1.0


class SeenRetryTests(unittest.TestCase):
    def test_global_hunter_run_does_not_persist_seen_early(self):
        hunter = DummyHunter(search_name="dummy")
        all_listings, new_listings = hunter.run()
        self.assertEqual(len(all_listings), 1)
        self.assertEqual(len(new_listings), 1)
        self.assertEqual(new_listings[0]["url"], "https://example.com/listing?bc=123")

    def test_local_seen_preserves_coords(self):
        seen = {}
        listing = {
            "url": "https://example.com/listing",
            "lat": 35.0,
            "lng": 139.0,
            "distance_km": 0.5,
        }
        _mark_as_seen(seen, listing, tele_sent=False)
        self.assertIn("https://example.com/listing", seen)
        self.assertEqual(seen["https://example.com/listing"]["lat"], 35.0)
        self.assertEqual(seen["https://example.com/listing"]["lng"], 139.0)
        self.assertEqual(seen["https://example.com/listing"]["distance_km"], 0.5)
        self.assertFalse(seen["https://example.com/listing"]["tele_sent"])

    def test_refilter_marks_pending_unsent_listing_as_sent(self):
        config = AppConfig(
            general=GeneralConfig(),
            searches=[SearchConfig(name="test", type="rental", url="https://suumo.jp/test", enabled=True, site="suumo")],
            filters=FiltersConfig(),
            notifications=NotificationsConfig(
                telegram=TelegramConfig(enabled=True, bot_token="x", chat_id="y")
            ),
            export=ExportConfig(),
        )
        seen = {
            "https://suumo.jp/test/listing": {
                "url": "https://suumo.jp/test/listing",
                "name": "Pending",
                "address": "Tokyo",
                "tele_sent": False,
                "search_name": "test",
            }
        }
        listing_filter = ListingFilter(config.filters)
        telegram = DummyTelegram(sent_count=1)
        geocoder = DummyGeocoder()

        result = run_refilter(
            config=config,
            target_name=None,
            seen=seen,
            listing_filter=listing_filter,
            telegram=telegram,
            geocoder=geocoder,
        )

        entry = result["https://suumo.jp/test/listing"]
        self.assertTrue(entry["tele_sent"])
        self.assertIsNotNone(entry["tele_sent_at"])

    def test_canonical_url_strips_query(self):
        self.assertEqual(
            _canonical_url("https://example.com/path?bc=123#frag"),
            "https://example.com/path",
        )


if __name__ == "__main__":
    unittest.main()
