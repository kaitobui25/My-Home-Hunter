"""Direct HTTP scraper for Yahoo! Japan real-estate rental search pages."""
from __future__ import annotations

import json
import logging
import re
import time
from datetime import datetime, timezone
from html import unescape
from urllib.parse import urljoin

import requests

from src.config import GeneralConfig, SearchConfig
from src.scraper.base import AbstractHunter

logger = logging.getLogger(__name__)

YAHOO_BASE = "https://realestate.yahoo.co.jp"
_LAYOUTS = {
    1: "1R",
    2: "1K",
    3: "1DK",
    4: "1LDK",
    5: "2K",
    6: "2DK",
    7: "2LDK",
    8: "3K",
    9: "3DK",
    10: "3LDK",
    11: "4K",
    12: "4DK",
    13: "4LDK",
    14: "5K以上",
}


def _extract_page_context(html_text: str) -> dict:
    """Extract the strict-JSON `page` object from Yahoo's JS context."""
    marker = "window.__SERVER_SIDE_CONTEXT__"
    marker_pos = html_text.find(marker)
    if marker_pos < 0:
        raise ValueError("Yahoo server-side context not found")

    match = re.search(r",\s*page\s*:\s*", html_text[marker_pos:])
    if not match:
        raise ValueError("Yahoo page context not found")

    start = marker_pos + match.end()
    if start >= len(html_text) or html_text[start] != "{":
        start = html_text.find("{", start)
    if start < 0:
        raise ValueError("Yahoo page JSON start not found")

    depth = 0
    in_string = False
    escaped = False
    for pos in range(start, len(html_text)):
        char = html_text[pos]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue

        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return json.loads(html_text[start : pos + 1])

    raise ValueError("Yahoo page JSON end not found")


def _parse_man_yen(text: str | None, rent_man_yen: float | None = None) -> float | None:
    value = unescape(text or "").strip()
    if not value:
        return None
    if value in {"なし", "無", "不要", "-"}:
        return 0.0

    match = re.search(r"([\d.]+)\s*万円", value)
    if match:
        return float(match.group(1))

    match = re.search(r"([\d,]+)\s*円", value)
    if match:
        return float(match.group(1).replace(",", "")) / 10000.0

    match = re.search(r"([\d.]+)\s*(?:ヶ|か)?月", value)
    if match and rent_man_yen is not None:
        return float(match.group(1)) * rent_man_yen

    return None


def _parse_yen(text: str | None) -> float | None:
    value = unescape(text or "").strip()
    if not value:
        return None
    if value in {"なし", "無", "不要", "-"}:
        return 0.0
    match = re.search(r"([\d,]+)\s*円", value)
    return float(match.group(1).replace(",", "")) if match else None


def _parse_m2(text: str | None) -> float | None:
    value = unescape(text or "")
    match = re.search(r"([\d.]+)\s*m", value, re.IGNORECASE)
    return float(match.group(1)) if match else None


def _parse_floor(text: str | None) -> int | None:
    match = re.search(r"-?\d+", str(text or ""))
    return int(match.group(0)) if match else None


def _parse_coordinates(value: str | None) -> tuple[float | None, float | None]:
    if not value or "," not in value:
        return None, None
    try:
        lat_raw, lng_raw = value.split(",", 1)
        return float(lat_raw), float(lng_raw)
    except (TypeError, ValueError):
        return None, None


def _address(building: dict) -> str:
    location = building.get("LocationView") or {}
    parts = [
        location.get("PrefectureName"),
        location.get("GeoName"),
        location.get("OazaName"),
        location.get("AzaName"),
    ]
    address = "".join(str(part) for part in parts if part)
    return address or str(location.get("AddressName") or "")


def _normalize_room(building: dict, room: dict) -> dict:
    rent_raw = str(room.get("PriceLabel") or "")
    rent_man_yen = _parse_man_yen(rent_raw)
    admin_raw = str(room.get("MonthlyManagementCostLabel") or "")
    deposit_raw = str(room.get("SecurityDepositLabel") or room.get("DepositLabel") or "")
    key_money_raw = str(room.get("KeyMoneyLabel") or "")
    size_raw = unescape(str(room.get("MonopolyAreaLabel") or ""))
    floor_raw = str(room.get("FloorNum") or "")
    property_id = str(room.get("PropertyId") or "")
    lat, lng = _parse_coordinates(building.get("CoordinatesWgs"))
    transports = [
        str(item.get("Label"))
        for item in (building.get("Transports") or [])
        if item.get("Label")
    ]

    return {
        "name": str(building.get("BuildingName") or ""),
        "listing_type": "rental",
        "price_raw": rent_raw,
        "price_man_yen": rent_man_yen,
        "admin_fee_raw": admin_raw,
        "admin_fee_yen": _parse_yen(admin_raw),
        "deposit_raw": deposit_raw,
        "deposit_man_yen": _parse_man_yen(deposit_raw, rent_man_yen),
        "key_money_raw": key_money_raw,
        "key_money_man_yen": _parse_man_yen(key_money_raw, rent_man_yen),
        "layout": _LAYOUTS.get(room.get("DetailRoomLayout"), ""),
        "size_m2": _parse_m2(size_raw),
        "size_raw": size_raw,
        "floor": floor_raw,
        "floor_num": _parse_floor(floor_raw),
        "building_age": room.get("YearsOld", building.get("YearsOld")),
        "building_age_raw": (
            f"築{room.get('YearsOld', building.get('YearsOld'))}年"
            if room.get("YearsOld", building.get("YearsOld")) is not None
            else ""
        ),
        "address": _address(building),
        "transportation": " | ".join(transports),
        "url": f"{YAHOO_BASE}/rent/detail/{property_id}/" if property_id else "",
        "image_url": building.get("ExternalImageUrl"),
        "lat": lat,
        "lng": lng,
        "scraped_at": datetime.now(timezone.utc).isoformat(),
    }


class YahooRentalHunter(AbstractHunter):
    """Scrape Yahoo rental search pages with requests only, no browser."""

    def __init__(self, search: SearchConfig, general: GeneralConfig):
        super().__init__(search_name=search.name)
        self.start_url = search.url
        self.max_pages = max(1, int(general.max_pages_per_search))
        self.timeout = max(1, int(general.page_load_timeout))
        self.delay_between_pages = max(0.0, float(general.delay_between_pages))
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
                "Accept-Language": "ja-JP,ja;q=0.9,en;q=0.8",
            }
        )
        self.stats = {
            "pages": 0,
            "buildings": 0,
            "visible_rooms": 0,
            "hidden_buildings": 0,
            "extra_requests": 0,
            "rooms": 0,
        }

    def _get_page(self, url: str) -> dict:
        response = self.session.get(url, timeout=self.timeout)
        response.raise_for_status()
        response.encoding = response.apparent_encoding or response.encoding
        return _extract_page_context(response.text)

    def _fetch_all_rooms_for_structure(self, structure_id: str) -> list[dict]:
        self.stats["extra_requests"] += 1
        page = self._get_page(f"{YAHOO_BASE}/rent/search/s/{structure_id}/")
        for building in page.get("properties") or []:
            if str(building.get("StructureId") or "") == structure_id:
                return list(building.get("GroupProperties") or [])
        logger.warning("[%s] Yahoo hidden rooms not found for %s", self.search_name, structure_id)
        return []

    def _rooms_from_building(self, building: dict) -> list[dict]:
        rooms = list(building.get("GroupProperties") or [])
        self.stats["visible_rooms"] += len(rooms)
        expected = int(building.get("PropertyCount") or len(rooms))
        structure_id = str(building.get("StructureId") or "")

        if structure_id and expected > len(rooms):
            self.stats["hidden_buildings"] += 1
            full_rooms = self._fetch_all_rooms_for_structure(structure_id)
            if full_rooms:
                by_id = {
                    str(room.get("PropertyId") or ""): room
                    for room in rooms
                    if room.get("PropertyId")
                }
                for room in full_rooms:
                    property_id = str(room.get("PropertyId") or "")
                    if property_id:
                        by_id[property_id] = room
                rooms = list(by_id.values())

        return [_normalize_room(building, room) for room in rooms]

    def scrape(self) -> list[dict]:
        started = time.perf_counter()
        all_listings: list[dict] = []
        seen_property_ids: set[str] = set()
        current_url = self.start_url

        for page_number in range(1, self.max_pages + 1):
            logger.info("[%s] Yahoo page %d: %s", self.search_name, page_number, current_url)
            page = self._get_page(current_url)
            buildings = list(page.get("properties") or [])
            self.stats["pages"] += 1
            self.stats["buildings"] += len(buildings)

            for building in buildings:
                for listing in self._rooms_from_building(building):
                    property_id = listing["url"].rstrip("/").rsplit("/", 1)[-1]
                    if not property_id or property_id in seen_property_ids:
                        continue
                    seen_property_ids.add(property_id)
                    all_listings.append(listing)

            pagination = page.get("paginationContext") or {}
            next_url = pagination.get("nextPageUrl")
            current_page = int(pagination.get("currentPage") or page_number)
            last_page = int(pagination.get("lastPage") or current_page)
            logger.info(
                "[%s] Yahoo page %d: %d buildings, %d rooms total so far",
                self.search_name,
                page_number,
                len(buildings),
                len(all_listings),
            )

            if not next_url or current_page >= last_page or page_number >= self.max_pages:
                break
            current_url = urljoin(YAHOO_BASE, next_url)
            if self.delay_between_pages:
                time.sleep(self.delay_between_pages)

        self.stats["rooms"] = len(all_listings)
        self.stats["seconds"] = round(time.perf_counter() - started, 3)
        return all_listings
