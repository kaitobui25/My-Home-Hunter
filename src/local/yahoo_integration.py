"""Yahoo-specific adapter for the progressive local runner.

Keeps Yahoo scraping separate while reusing the shared filter, geocoder,
seen-store and Telegram components used by the other local sources.
"""
from __future__ import annotations

import logging

from src.config import AppConfig, SearchConfig
from src.filter import ListingFilter
from src.geocoder import GeocoderService
from src.notifier.telegram import TelegramNotifier
from src.scraper.yahoo_rental_hunter import YahooRentalHunter
from src.local import run_local as legacy

logger = logging.getLogger("local.yahoo")


def _telegram_active(telegram: TelegramNotifier) -> bool:
    cfg = telegram.cfg
    return bool(cfg.enabled and cfg.bot_token and cfg.chat_id)


def _prepare_coordinates(listing: dict, geocoder: GeocoderService, loc_cfg) -> None:
    """Use Yahoo coordinates first; geocode only when they are missing."""
    lat = listing.get("lat")
    lng = listing.get("lng")

    if lat is None or lng is None:
        address = listing.get("address", "")
        if address:
            lat, lng = geocoder.get_coordinates(address)
        else:
            lat, lng = None, None
        listing["lat"] = lat
        listing["lng"] = lng

    if lat is not None and lng is not None and loc_cfg.enabled:
        listing["distance_km"] = geocoder.calculate_distance(
            lat,
            lng,
            loc_cfg.center_lat,
            loc_cfg.center_lng,
        )
    else:
        listing["distance_km"] = None


def _is_spatial_duplicate(listing: dict, fingerprints: list[dict], geocoder: GeocoderService) -> bool:
    lat = listing.get("lat")
    lng = listing.get("lng")
    price = listing.get("price_man_yen")
    size = listing.get("size_m2")

    if lat is None or lng is None or price is None or size is None:
        return False

    for fp in fingerprints:
        if fp["price"] != price or fp["size"] != size:
            continue
        if geocoder.calculate_distance(lat, lng, fp["lat"], fp["lng"]) <= 0.2:
            return True
    return False


def run_yahoo_search(
    search: SearchConfig,
    config: AppConfig,
    seen: dict,
    listing_filter: ListingFilter,
    telegram: TelegramNotifier,
    geocoder: GeocoderService,
    on_seen_updated=None,
) -> tuple[dict, set[str]]:
    """Scrape one Yahoo rental search and run the normal local processing steps."""
    if search.type != "rental":
        raise ValueError(f"Yahoo only supports 'rental' type, got: '{search.type}'.")

    try:
        listings = YahooRentalHunter(search=search, general=config.general).scrape()
    except Exception:
        logger.exception("[%s] Yahoo scraping failed", search.name)
        return seen, set()

    canonical_scraped = {
        legacy._canonical_url(listing["url"])
        for listing in listings
        if listing.get("url")
    }
    telegram_active = _telegram_active(telegram)

    candidates: dict[str, dict] = {}
    if telegram_active:
        for entry in seen.values():
            if (
                not entry.get("tele_sent")
                and entry.get("url")
                and entry.get("search_name") == search.name
            ):
                candidates[legacy._canonical_url(entry["url"])] = dict(entry)

    for listing in listings:
        url = listing.get("url")
        if not url:
            continue
        canonical = legacy._canonical_url(url)
        if canonical not in seen:
            candidates[canonical] = listing

    logger.info(
        "[%s] Yahoo result: %d scraped | %d candidates",
        search.name,
        len(listings),
        len(candidates),
    )

    if not candidates:
        return seen, canonical_scraped

    candidate_urls = set(candidates)
    fingerprints = []
    for entry in seen.values():
        url = entry.get("url")
        if url and legacy._canonical_url(url) in candidate_urls:
            continue
        lat = entry.get("lat")
        lng = entry.get("lng")
        if lat is None or lng is None:
            continue
        fingerprints.append(
            {
                "lat": lat,
                "lng": lng,
                "price": entry.get("price_man_yen"),
                "size": entry.get("size_m2"),
            }
        )

    loc_cfg = config.filters.location_filter
    matched: list[dict] = []
    legacy._emit_phase(
        "filtering_listings",
        search_name=search.name,
        message="Processing Yahoo listings",
        total_listings=len(candidates),
    )

    for index, listing in enumerate(candidates.values(), start=1):
        _prepare_coordinates(listing, geocoder, loc_cfg)
        listing["search_name"] = search.name
        legacy._mark_as_seen(seen, listing, tele_sent=False)

        if not listing_filter.matches(listing):
            continue
        if loc_cfg.enabled:
            distance = listing.get("distance_km")
            if distance is None or distance > loc_cfg.max_distance_km:
                continue
        if _is_spatial_duplicate(listing, fingerprints, geocoder):
            continue

        lat = listing.get("lat")
        lng = listing.get("lng")
        price = listing.get("price_man_yen")
        size = listing.get("size_m2")
        if lat is not None and lng is not None and price is not None and size is not None:
            fingerprints.append({"lat": lat, "lng": lng, "price": price, "size": size})
        matched.append(listing)

        if callable(on_seen_updated) and index % 100 == 0:
            on_seen_updated(seen)

    if callable(on_seen_updated):
        on_seen_updated(seen)

    if matched and telegram_active:
        legacy._emit_phase(
            "notifying_telegram",
            search_name=search.name,
            message=f"Sending {len(matched)} Yahoo listings to Telegram",
        )
        sent_count = telegram.send_batch(matched, search_name=f"[LOCAL] {search.name}")
        for listing in matched[:sent_count]:
            legacy._mark_as_seen(seen, listing, tele_sent=True)
        if callable(on_seen_updated):
            on_seen_updated(seen)
        legacy._emit_phase(
            "telegram_done",
            search_name=search.name,
            message=f"Sent {sent_count}/{len(matched)} Yahoo listings",
            matched=len(matched),
            sent=sent_count,
        )
    else:
        legacy._emit_phase(
            "telegram_done",
            search_name=search.name,
            message="Telegram disabled or no matching Yahoo listings",
            matched=len(matched),
            sent=0,
        )

    logger.info(
        "[%s] Yahoo processed: %d candidates | %d matched",
        search.name,
        len(candidates),
        len(matched),
    )
    return seen, canonical_scraped
