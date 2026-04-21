"""
src/geocoder.py
Handles geocoding addresses to coordinates and calculating distances.
Includes simple file-based caching to avoid hitting rate limits.
"""
from __future__ import annotations

import json
import logging
import os
import time
from typing import Tuple, Optional

from geopy.geocoders import Nominatim
from geopy.distance import geodesic
from geopy.exc import GeocoderTimedOut, GeocoderServiceError

logger = logging.getLogger(__name__)


class GeocoderService:
    def __init__(self, cache_file: str = "results/geocode_cache.json"):
        self.cache_file = cache_file
        self.cache = self._load_cache()
        # Use a descriptive user_agent as required by Nominatim
        self.geolocator = Nominatim(user_agent="home-hunter-suumo-scraper")
        self.request_delay = 1.1 # Nominatim requires 1 req/sec limit

    def get_coordinates(self, address: str) -> Tuple[Optional[float], Optional[float]]:
        """Return (lat, lng) for an address, using cache if available."""
        if not address:
            return None, None

        # Clean address a bit for better geocoding (Nominatim can be strict)
        # Often SUUMO addresses have building names or floor numbers appended.
        # We try to use the raw address first.
        clean_addr = address.split(" ")[0].split("\n")[0] # Take first part
        
        if clean_addr in self.cache:
            res = self.cache[clean_addr]
            if res is None:
                return None, None
            return res[0], res[1]

        logger.info(f"Geocoding new address: {clean_addr}")
        try:
            # Respect rate limit
            time.sleep(self.request_delay)
            location = self.geolocator.geocode(clean_addr, timeout=10)
            
            if location:
                result = (location.latitude, location.longitude)
                self.cache[clean_addr] = result
                self._save_cache()
                return result
            else:
                # Cache misses so we don't keep trying
                self.cache[clean_addr] = None
                self._save_cache()
                return None, None
                
        except (GeocoderTimedOut, GeocoderServiceError) as e:
            logger.error(f"Geocoding error for {clean_addr}: {e}")
            return None, None

    def calculate_distance(self, lat1: float, lng1: float, lat2: float, lng2: float) -> Optional[float]:
        """Calculate distance in kilometers between two points."""
        if None in (lat1, lng1, lat2, lng2):
            return None
        return geodesic((lat1, lng1), (lat2, lng2)).kilometers

    def _load_cache(self) -> dict:
        if os.path.exists(self.cache_file):
            try:
                with open(self.cache_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                logger.warning(f"Failed to load geocode cache: {e}")
        return {}

    def _save_cache(self):
        os.makedirs(os.path.dirname(self.cache_file), exist_ok=True)
        try:
            with open(self.cache_file, "w", encoding="utf-8") as f:
                json.dump(self.cache, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"Failed to save geocode cache: {e}")
