"""
src/filter.py
Applies config-driven conditions to listings.
All checks are optional (null/None = skip that check).
"""
from __future__ import annotations

import logging
from src.config import FiltersConfig

logger = logging.getLogger(__name__)


class ListingFilter:
    """
    Filters listings based on the [filters] section in config.yaml.
    A listing PASSES if it satisfies ALL enabled conditions.
    """

    def __init__(self, config: FiltersConfig):
        self.cfg = config

    def matches(self, listing: dict) -> bool:
        """Return True if the listing passes all filter conditions."""
        listing_type = listing.get("listing_type", "")

        # --- Common checks ---
        if not self._check_size(listing):
            return False
        if not self._check_building_age(listing):
            return False

        # --- Type-specific checks ---
        if listing_type == "rental":
            if not self._check_rental(listing):
                return False
        elif listing_type == "sale":
            if not self._check_sale(listing):
                return False

        return True

    # ------------------------------------------------------------------
    # Common
    # ------------------------------------------------------------------

    def _check_size(self, listing: dict) -> bool:
        size = listing.get("size_m2")
        if size is None:
            # If we can't parse size, pass the check (don't filter out)
            return True
        if self.cfg.min_size_m2 is not None and size < self.cfg.min_size_m2:
            logger.debug("FILTERED size %.1f < min %.1f [%s]", size, self.cfg.min_size_m2, listing.get("url"))
            return False
        if self.cfg.max_size_m2 is not None and size > self.cfg.max_size_m2:
            logger.debug("FILTERED size %.1f > max %.1f [%s]", size, self.cfg.max_size_m2, listing.get("url"))
            return False
        return True

    def _check_building_age(self, listing: dict) -> bool:
        if self.cfg.max_building_age_years is None:
            return True
        age = listing.get("building_age")
        if age is None:
            return True  # Unknown age → don't filter out
        if age > self.cfg.max_building_age_years:
            logger.debug("FILTERED age %d > max %d [%s]", age, self.cfg.max_building_age_years, listing.get("url"))
            return False
        return True

    # ------------------------------------------------------------------
    # Rental
    # ------------------------------------------------------------------

    def _check_rental(self, listing: dict) -> bool:
        r = self.cfg.rental

        # Rent
        if r.max_rent_man_yen is not None:
            rent = listing.get("price_man_yen")
            if rent is not None and rent > r.max_rent_man_yen:
                logger.debug("FILTERED rent %.1f > max %.1f [%s]", rent, r.max_rent_man_yen, listing.get("url"))
                return False

        # Admin fee
        if r.max_admin_fee_yen is not None:
            fee = listing.get("admin_fee_yen")
            if fee is not None and fee > r.max_admin_fee_yen:
                logger.debug("FILTERED admin fee %.0f > max %.0f [%s]", fee, r.max_admin_fee_yen, listing.get("url"))
                return False

        # Deposit
        if r.max_deposit_man_yen is not None:
            dep = listing.get("deposit_man_yen")
            if dep is not None and dep > r.max_deposit_man_yen:
                logger.debug("FILTERED deposit %.1f > max %.1f [%s]", dep, r.max_deposit_man_yen, listing.get("url"))
                return False

        # Key money
        if r.max_key_money_man_yen is not None:
            key = listing.get("key_money_man_yen")
            if key is not None and key > r.max_key_money_man_yen:
                logger.debug("FILTERED key money %.1f > max %.1f [%s]", key, r.max_key_money_man_yen, listing.get("url"))
                return False

        # Layout
        if r.allowed_layouts:
            layout = listing.get("layout", "")
            if not _layout_matches(layout, r.allowed_layouts):
                logger.debug("FILTERED layout '%s' not in %s [%s]", layout, r.allowed_layouts, listing.get("url"))
                return False

        # Floor
        if r.min_floor is not None:
            floor_num = listing.get("floor_num")
            if floor_num is not None and floor_num < r.min_floor:
                logger.debug("FILTERED floor %d < min %d [%s]", floor_num, r.min_floor, listing.get("url"))
                return False

        return True

    # ------------------------------------------------------------------
    # Sale
    # ------------------------------------------------------------------

    def _check_sale(self, listing: dict) -> bool:
        s = self.cfg.sale

        if s.max_price_man_yen is not None:
            price = listing.get("price_man_yen")
            if price is not None and price > s.max_price_man_yen:
                logger.debug("FILTERED price %.1f > max %.1f [%s]", price, s.max_price_man_yen, listing.get("url"))
                return False

        return True


def _layout_matches(layout: str, allowed: list[str]) -> bool:
    """
    Supports both exact and keyword matching.
    - Exact: "2LDK" matches only "2LDK"
    - Keyword: "LDK" matches "1LDK", "2LDK", "3LDK", etc.
    """
    if not layout:
        return False
    for pattern in allowed:
        if pattern == layout:          # exact match
            return True
        if len(pattern) <= 3 and pattern in layout:  # keyword match (e.g. "LDK")
            return True
    return False
