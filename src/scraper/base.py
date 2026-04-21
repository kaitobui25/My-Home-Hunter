"""
src/scraper/base.py
Base classes: WebDriverBase and AbstractHunter.
"""
from __future__ import annotations

import json
import logging
import os
from abc import ABC, abstractmethod

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service as ChromeService

logger = logging.getLogger(__name__)


class WebDriverBase:
    """Manages Chrome WebDriver lifecycle."""

    def __init__(self, webdriver_path: str = "", headless: bool = True, disable_images_css: bool = True):
        self.webdriver_path = webdriver_path
        self.headless = headless
        self.disable_images_css = disable_images_css
        self.driver = self._init_driver()

    def _init_driver(self):
        opts = Options()
        if self.headless:
            opts.add_argument("--headless")
        opts.add_argument("--no-sandbox")
        opts.add_argument("--disable-dev-shm-usage")
        opts.add_argument("--disable-gpu")
        opts.add_argument("--window-size=1920x1080")
        opts.add_argument("--log-level=3")  # suppress chrome logs
        opts.add_experimental_option("excludeSwitches", ["enable-logging"])

        if self.disable_images_css:
            prefs = {
                "profile.managed_default_content_settings.images": 2,
                "profile.managed_default_content_settings.stylesheet": 2,
            }
            opts.add_experimental_option("prefs", prefs)

        if self.webdriver_path:
            service = ChromeService(executable_path=self.webdriver_path)
            return webdriver.Chrome(service=service, options=opts)
        # Selenium Manager handles driver download automatically
        return webdriver.Chrome(options=opts)

    def restart_driver(self):
        self.close_driver()
        self.driver = self._init_driver()

    def close_driver(self):
        try:
            self.driver.quit()
        except Exception:
            pass


class AbstractHunter(ABC):
    """
    Base scraper class.
    Handles seen-listings persistence; subclasses implement scraping logic.
    """

    def __init__(self, search_name: str, storage_root: str = "results/seen_listings"):
        self.search_name = search_name
        self.storage_root = storage_root
        self._seen: dict = {}        # url -> listing dict (persisted)
        self._new: list = []         # listings found this run that are new
        self._all: list = []         # all listings found this run
        self._load_seen()

    # ------------------------------------------------------------------
    # Abstract interface
    # ------------------------------------------------------------------

    @abstractmethod
    def scrape(self) -> list[dict]:
        """
        Perform full scrape of the target URL(s).
        Must return a list of listing dicts following the standard schema.
        """

    # ------------------------------------------------------------------
    # Public API called by run.py
    # ------------------------------------------------------------------

    def run(self) -> tuple[list[dict], list[dict]]:
        """
        Run the scraper.
        Returns (all_listings, new_listings).
        """
        self._all = self.scrape()
        self._new = [l for l in self._all if l["url"] not in self._seen]

        logger.info(
            "[%s] Scraped %d listings total, %d new.",
            self.search_name, len(self._all), len(self._new),
        )

        # Persist new listings immediately
        for listing in self._new:
            self._seen[listing["url"]] = listing
        self._save_seen()

        return self._all, self._new

    # ------------------------------------------------------------------
    # Persistence helpers
    # ------------------------------------------------------------------

    @property
    def _seen_file(self) -> str:
        os.makedirs(self.storage_root, exist_ok=True)
        return os.path.join(self.storage_root, "global_seen_listings.json")

    def _load_seen(self):
        try:
            with open(self._seen_file, "r", encoding="utf-8") as f:
                self._seen = json.load(f)
            logger.debug("[%s] Loaded %d seen listings.", self.search_name, len(self._seen))
        except FileNotFoundError:
            self._seen = {}
        except json.JSONDecodeError:
            logger.warning("[%s] seen_listings.json corrupted, resetting.", self.search_name)
            self._seen = {}

    def _save_seen(self):
        try:
            with open(self._seen_file, "w", encoding="utf-8") as f:
                json.dump(self._seen, f, ensure_ascii=False, indent=2)
            logger.debug("[%s] Saved %d seen listings.", self.search_name, len(self._seen))
        except IOError as e:
            logger.error("[%s] Failed to save seen listings: %s", self.search_name, e)
