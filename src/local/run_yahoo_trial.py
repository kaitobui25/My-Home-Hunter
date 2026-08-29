"""Standalone Yahoo rental benchmark. Does not touch the normal local runner."""
from __future__ import annotations

import argparse
import json
import os

from src.config import GeneralConfig, SearchConfig
from src.scraper.yahoo_rental_hunter import YahooRentalHunter

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
DEFAULT_OUTPUT = os.path.join(PROJECT_ROOT, "results-local", "yahoo_trial.json")


def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark Yahoo rental scraping with direct HTTP")
    parser.add_argument("--url", required=True, help="Yahoo rental search URL")
    parser.add_argument("--max-pages", type=int, default=20)
    parser.add_argument("--delay", type=float, default=0.3, help="Seconds between search pages")
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    general = GeneralConfig(
        page_load_timeout=args.timeout,
        delay_between_pages=args.delay,
        max_pages_per_search=args.max_pages,
    )
    search = SearchConfig(
        name="Yahoo Trial",
        type="rental",
        url=args.url,
        site="yahoo",
    )

    hunter = YahooRentalHunter(search, general)
    listings = hunter.scrape()

    os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as file:
        json.dump(listings, file, ensure_ascii=False, indent=2)

    with_coords = sum(
        1 for listing in listings
        if listing.get("lat") is not None and listing.get("lng") is not None
    )

    print("\n===== YAHOO DIRECT HTTP TRIAL =====")
    print(f"Pages             : {hunter.stats['pages']}")
    print(f"Buildings         : {hunter.stats['buildings']}")
    print(f"Visible rooms     : {hunter.stats['visible_rooms']}")
    print(f"Hidden buildings  : {hunter.stats['hidden_buildings']}")
    print(f"Extra requests    : {hunter.stats['extra_requests']}")
    print(f"Unique rooms      : {hunter.stats['rooms']}")
    print(f"Rooms with coords : {with_coords}")
    print(f"Total time        : {hunter.stats['seconds']:.3f} sec")
    print(f"Saved             : {args.output}")


if __name__ == "__main__":
    main()
