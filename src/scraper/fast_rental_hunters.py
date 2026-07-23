"""Faster and safer Playwright-backed rental hunters.

This module intentionally keeps the existing parsers intact. It only adjusts
browser lifecycle and pacing so site-specific parsing remains the single source
of truth in the original hunter modules.
"""
from __future__ import annotations

import logging
import os

from src.scraper.homes_rental_hunter import HOMESRentalHunter
from src.scraper.nifty_rental_hunter import NiftyRentalHunter

logger = logging.getLogger(__name__)


def _fast_page_delay(current: float) -> float:
    """Cap per-page sleep while still allowing a safer override via env."""
    raw = os.environ.get("HOME_HUNTER_PAGE_DELAY_SECONDS", "0.35")
    try:
        configured = max(0.0, float(raw))
    except (TypeError, ValueError):
        configured = 0.35
    return min(max(0.0, float(current)), configured)


class SafePlaywrightCloseMixin:
    """Close every Playwright layer independently.

    The previous implementation wrapped page/context/browser/playwright cleanup
    in one try block. If the first close raised, the Node driver could be left
    alive and later write to a pipe that Python had already closed (EPIPE).
    """

    def close_driver(self) -> None:
        for attr, method_name in (
            ("page", "close"),
            ("context", "close"),
            ("browser", "close"),
            ("playwright", "stop"),
        ):
            obj = getattr(self, attr, None)
            if obj is None:
                continue
            try:
                getattr(obj, method_name)()
            except Exception as exc:  # cleanup must continue through all layers
                logger.debug("Playwright cleanup failed at %s.%s: %s", attr, method_name, exc)
            finally:
                try:
                    setattr(self, attr, None)
                except Exception:
                    pass


class FastNiftyRentalHunter(SafePlaywrightCloseMixin, NiftyRentalHunter):
    """Nifty parser with safe cleanup and shorter pacing."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.delay_between_pages = _fast_page_delay(self.delay_between_pages)
        try:
            self.page.set_default_timeout(min(self.page_load_timeout, 20_000))
        except Exception:
            pass


class FastHOMESRentalHunter(SafePlaywrightCloseMixin, HOMESRentalHunter):
    """HOME'S parser with safe cleanup and shorter pacing."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.delay_between_pages = _fast_page_delay(self.delay_between_pages)
        try:
            self.page.set_default_timeout(min(self.page_load_timeout, 20_000))
        except Exception:
            pass
