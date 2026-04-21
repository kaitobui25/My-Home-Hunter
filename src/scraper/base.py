"""
src/scraper/base.py
Base classes: WebDriverBase and AbstractHunter.
"""
from __future__ import annotations

import json
import logging
import os
from abc import ABC, abstractmethod

from playwright.sync_api import sync_playwright

logger = logging.getLogger(__name__)


class PlaywrightBase:
    """Manages Playwright browser lifecycle."""

    def __init__(self, webdriver_path: str = "", headless: bool = True, disable_images_css: bool = True):
        # webdriver_path is kept for config compatibility but ignored
        self.headless = headless
        self.disable_images_css = disable_images_css
        self._init_driver()

    def _init_driver(self):
        self.playwright = sync_playwright().start()
        self.browser = self.playwright.chromium.launch(headless=self.headless)
        self.context = self.browser.new_context(viewport={'width': 1920, 'height': 1080})
        self.page = self.context.new_page()

        if self.disable_images_css:
            self.page.route("**/*", self._block_resources)

    def _block_resources(self, route):
        if route.request.resource_type in ["image", "stylesheet", "font"]:
            route.abort()
        else:
            route.continue_()

    def restart_driver(self):
        self.close_driver()
        self._init_driver()

    def close_driver(self):
        try:
            if hasattr(self, 'context'): self.context.close()
            if hasattr(self, 'browser'): self.browser.close()
            if hasattr(self, 'playwright'): self.playwright.stop()
        except Exception as e:
            logger.debug("Error closing playwright: %s", e)


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
