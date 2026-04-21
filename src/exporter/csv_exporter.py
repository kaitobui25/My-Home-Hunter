"""
src/exporter/csv_exporter.py
Writes listings to CSV files.
"""
from __future__ import annotations

import csv
import logging
import os
from datetime import datetime

from src.config import CsvExportConfig

logger = logging.getLogger(__name__)

# All columns written to CSV (order matters)
CSV_COLUMNS = [
    "name",
    "listing_type",
    "price_raw",
    "price_man_yen",
    "admin_fee_raw",
    "deposit_raw",
    "key_money_raw",
    "layout",
    "size_raw",
    "size_m2",
    "floor",
    "building_age",
    "address",
    "lat",
    "lng",
    "distance_km",
    "transportation",
    "url",
    "scraped_at",
]



class CSVExporter:
    def __init__(self, config: CsvExportConfig):
        self.cfg = config

    def write(self, listings: list[dict], search_name: str) -> str | None:
        """
        Write listings to a CSV file.
        Returns the path of the file written, or None if disabled/empty.
        """
        if not self.cfg.enabled:
            return None
        if not listings:
            logger.info("CSVExporter: no listings to write for '%s'.", search_name)
            return None

        os.makedirs(self.cfg.output_dir, exist_ok=True)
        filepath = self._build_filepath(search_name)
        file_exists = os.path.exists(filepath)

        mode = "a" if (self.cfg.append_mode and file_exists) else "w"

        with open(filepath, mode, newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS, extrasaction="ignore")

            # Write header only for new files
            if mode == "w" or not file_exists:
                writer.writeheader()

            for listing in listings:
                writer.writerow(listing)

        logger.info(
            "CSVExporter: wrote %d rows to %s (mode=%s).",
            len(listings), filepath, mode,
        )
        return filepath

    def _build_filepath(self, search_name: str) -> str:
        safe_name = "".join(c if c.isalnum() or c in "-_ " else "_" for c in search_name).strip()
        date_str = datetime.now().strftime("%Y-%m-%d")
        filename = self.cfg.filename.replace("{name}", safe_name).replace("{date}", date_str)
        return os.path.join(self.cfg.output_dir, filename)
