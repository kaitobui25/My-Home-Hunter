import unittest

from src.scraper.yahoo_rental_hunter import (
    _extract_page_context,
    _normalize_room,
    _parse_man_yen,
)


class YahooRentalHunterTests(unittest.TestCase):
    def test_extract_page_context(self):
        html = '<script>window.__SERVER_SIDE_CONTEXT__ = {common: {"x":1}, page: {"totalCount":456,"properties":[]}};</script>'
        page = _extract_page_context(html)
        self.assertEqual(page["totalCount"], 456)

    def test_parse_month_based_deposit(self):
        self.assertEqual(_parse_man_yen("2ヶ月", 6.75), 13.5)
        self.assertEqual(_parse_man_yen("なし", 6.75), 0.0)

    def test_normalize_room_uses_yahoo_coordinates(self):
        building = {
            "BuildingName": "signet庄内",
            "CoordinatesWgs": "34.74667,135.47801",
            "YearsOld": 5,
            "ExternalImageUrl": "https://example.com/building.jpg",
            "LocationView": {
                "PrefectureName": "大阪府",
                "GeoName": "豊中市",
                "OazaName": "庄内東町",
                "AzaName": "6丁目",
            },
            "Transports": [{"Label": "庄内駅/阪急宝塚本線 徒歩7分"}],
        }
        room = {
            "PropertyId": "abc123",
            "PriceLabel": "6.75万円",
            "MonthlyManagementCostLabel": "4,000円",
            "KeyMoneyLabel": "2ヶ月",
            "SecurityDepositLabel": "なし",
            "DetailRoomLayout": 4,
            "MonopolyAreaLabel": "31.72m<sup>2</sup>",
            "FloorNum": "3",
            "YearsOld": 5,
        }

        listing = _normalize_room(building, room)
        self.assertEqual(listing["layout"], "1LDK")
        self.assertEqual(listing["size_m2"], 31.72)
        self.assertEqual(listing["key_money_man_yen"], 13.5)
        self.assertEqual(listing["address"], "大阪府豊中市庄内東町6丁目")
        self.assertAlmostEqual(listing["lat"], 34.74667)
        self.assertAlmostEqual(listing["lng"], 135.47801)
        self.assertEqual(listing["url"], "https://realestate.yahoo.co.jp/rent/detail/abc123/")


if __name__ == "__main__":
    unittest.main()
