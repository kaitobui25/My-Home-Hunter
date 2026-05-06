"""
src/scraper/school_vacancy_hunter.py
====================================
Scrapes Osaka City website for nursery school vacancies.
"""
from __future__ import annotations

import logging
import json
import os
import re
from datetime import datetime

from src.scraper.base import PlaywrightBase
from src.config import GeneralConfig, SchoolSearchConfig
from src.geocoder import GeocoderService

logger = logging.getLogger(__name__)

class SchoolVacancyHunter(PlaywrightBase):
    def __init__(self, search: SchoolSearchConfig, general: GeneralConfig, geocoder: GeocoderService):
        super().__init__(
            webdriver_path=general.webdriver_path,
            headless=general.headless,
            disable_images_css=general.disable_images_css
        )
        self.search = search
        self.url = search.url
        self.geocoder = geocoder
        self.target_class = search.target_class # e.g. "1-year"

    def scrape_vacancies(self) -> list[dict]:
        """
        Scrapes the Yodogawa-ku vacancy page.
        Returns a list of schools with vacancies for the target class.
        """
        results = []
        try:
            logger.info("[%s] Opening URL: %s", self.search.name, self.url)
            self.page.goto(self.url, wait_until="domcontentloaded")
            
            # Wait for the table to appear (if it exists directly on page)
            # Based on previous check, it's likely a table or we might need to handle PDF.
            # But the user link is the HTML page.
            
            # Let's look for tables
            tables = self.page.query_selector_all("table")
            if not tables:
                logger.warning("[%s] No tables found on page.", self.search.name)
                return []

            # We assume the main table has headers like '施設名', '0歳児', '1歳児' etc.
            # We'll search for the one that contains '施設名' and '1歳児'
            target_table = None
            for table in tables:
                text = table.inner_text()
                if "施設名" in text and "1歳児" in text:
                    target_table = table
                    break
            
            if not target_table:
                logger.warning("[%s] Could not find the vacancy table.", self.search.name)
                return []

            rows = target_table.query_selector_all("tr")
            headers = [th.inner_text().strip() for th in rows[0].query_selector_all("th, td")]
            
            # Find index of '施設名' and '1歳児' (or whatever target_class maps to)
            try:
                name_idx = headers.index("施設名")
                # Map target_class to header text
                class_header = "1歳児" if self.target_class == "1-year" else self.target_class
                class_idx = -1
                for i, h in enumerate(headers):
                    if class_header in h:
                        class_idx = i
                        break
                
                if class_idx == -1:
                    logger.warning("[%s] Could not find header for class: %s", self.search.name, class_header)
                    return []
                
                # Notes column (if any)
                notes_idx = -1
                if "備考" in headers:
                    notes_idx = headers.index("備考")

                for row in rows[1:]:
                    cols = row.query_selector_all("td")
                    if len(cols) <= max(name_idx, class_idx):
                        continue
                    
                    name = cols[name_idx].inner_text().strip()
                    val_str = cols[class_idx].inner_text().strip()
                    
                    # Convert vacancy value to int
                    # Handle '○', '×', numbers, or '3名まで'
                    vacancy_num = 0
                    if val_str.isdigit():
                        vacancy_num = int(val_str)
                    elif "○" in val_str or "〇" in val_str:
                        vacancy_num = 1 # At least one
                    elif re.search(r'(\d+)', val_str):
                        vacancy_num = int(re.search(r'(\d+)', val_str).group(1))
                    
                    if vacancy_num > 0:
                        notes = cols[notes_idx].inner_text().strip() if notes_idx != -1 else ""
                        results.append({
                            "school_name": name,
                            "vacancies": vacancy_num,
                            "notes": notes,
                            "scraped_at": datetime.now().isoformat()
                        })
            except Exception as e:
                logger.error("[%s] Error parsing table: %s", self.search.name, e)

        except Exception as e:
            logger.error("[%s] Scraping failed: %s", self.search.name, e)
        finally:
            self.close_driver()
        
        return results

    def update_json(self, results: list[dict], output_path: str, address_map_path: str):
        """Updates the JSON file with coordinates and addresses."""
        # Load address map
        address_map = {}
        if os.path.exists(address_map_path):
            with open(address_map_path, "r", encoding="utf-8") as f:
                address_map = json.load(f)
        
        # Helper to normalize names for matching
        def normalize(name: str) -> str:
            # Remove everything from the first parenthesis (any kind)
            name = re.split(r'\(|（', name)[0]
            # Remove all whitespace and special symbols
            name = re.sub(r'[\s\u3000\u00a0]+', '', name)
            return name

        norm_address_map = {normalize(k): v for k, v in address_map.items()}

        # Process results
        final_data = []
        for r in results:
            name = r["school_name"]
            norm_name = normalize(name)
            address = norm_address_map.get(norm_name, "")
            
            lat, lng = None, None
            if address:
                # Clean address as requested (remove building info if geocoding fails)
                lat, lng = self.geocoder.get_coordinates(address)
                if lat is None or lng is None:
                    # Try cleaning: remove space and subsequent parts
                    clean_address = address.split(" ")[0]
                    if clean_address != address:
                        logger.info("Retrying geocode with cleaned address: %s", clean_address)
                        lat, lng = self.geocoder.get_coordinates(clean_address)
            
            final_data.append({
                "school_name": name,
                "address": address,
                "lat": lat,
                "lng": lng,
                f"vacancies_{self.target_class.replace('-', '_')}": r["vacancies"],
                "notes": r["notes"],
                "scraped_at": r["scraped_at"]
            })

        # Save to JSON
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(final_data, f, ensure_ascii=False, indent=2)
        
        logger.info("[%s] Updated %d schools to %s", self.search.name, len(final_data), output_path)
