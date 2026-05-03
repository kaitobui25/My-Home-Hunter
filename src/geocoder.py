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
import urllib.request
import urllib.parse
from typing import Tuple, Optional

from geopy.distance import geodesic

logger = logging.getLogger(__name__)


class GeocoderService:
    def __init__(self, cache_file: str = "results/geocode_cache.json"):
        self.cache_file = cache_file
        self.cache = self._load_cache()
        # Delay to be nice to public APIs (GSI doesn't strictly limit, but 1s is safe)
        self.request_delay = 1.0 

    def get_coordinates(self, address: str) -> Tuple[Optional[float], Optional[float]]:
        """Return (lat, lng) for an address, using cache if available."""
        if not address:
            return None, None

        # Clean address a bit for better geocoding
        # Often SUUMO addresses have building names or floor numbers appended.
        clean_addr = address.split(" ")[0].split("\n")[0] # Take first part
        
        if clean_addr in self.cache:
            res = self.cache[clean_addr]
            if res is None:
                return None, None
            return res[0], res[1]

        logger.info(f"Geocoding new address via GSI: {clean_addr}")
        try:
            # Respect rate limit
            time.sleep(self.request_delay)
            
            # Use Japanese Government (GSI) msearch API - highly accurate for Japan, free, no keys needed.
            encoded_addr = urllib.parse.quote(clean_addr)
            url = f"https://msearch.gsi.go.jp/address-search/AddressSearch?q={encoded_addr}"
            
            req = urllib.request.Request(url, headers={'User-Agent': 'home-hunter-bot'})
            with urllib.request.urlopen(req, timeout=10) as response:
                data = json.loads(response.read().decode('utf-8'))
            
            if data and isinstance(data, list) and len(data) > 0:
                # GSI returns [lng, lat]
                coords = data[0]["geometry"]["coordinates"]
                result = (float(coords[1]), float(coords[0])) # Convert to (lat, lng)
                self.cache[clean_addr] = result
                self._save_cache()
                return result
            else:
                # Cache misses so we don't keep trying
                self.cache[clean_addr] = None
                self._save_cache()
                return None, None
                
        except Exception as e:
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
