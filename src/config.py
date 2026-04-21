"""
src/config.py
Loads and validates config.yaml into dataclasses.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import List, Optional

import yaml

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class GeneralConfig:
    check_interval_seconds: int = 3600
    webdriver_path: str = ""
    log_level: str = "INFO"
    headless: bool = True
    disable_images_css: bool = True
    page_load_timeout: int = 20
    delay_between_pages: float = 1.0
    delay_between_searches: int = 30
    max_pages_per_search: int = 20


@dataclass
class SearchConfig:
    name: str
    type: str        # "rental" | "sale"
    url: str
    enabled: bool = True


@dataclass
class RentalFilterConfig:
    max_rent_man_yen: Optional[float] = None
    max_admin_fee_yen: Optional[float] = None
    max_deposit_man_yen: Optional[float] = None
    max_key_money_man_yen: Optional[float] = None
    allowed_layouts: List[str] = field(default_factory=list)
    min_floor: Optional[int] = None


@dataclass
class SaleFilterConfig:
    max_price_man_yen: Optional[float] = None
    max_price_per_tsubo: Optional[float] = None


@dataclass
class LocationFilterConfig:
    enabled: bool = False
    center_lat: float = 0.0
    center_lng: float = 0.0
    max_distance_km: float = 5.0


@dataclass
class FiltersConfig:
    min_size_m2: Optional[float] = None
    max_size_m2: Optional[float] = None
    max_building_age_years: Optional[int] = None
    location_filter: LocationFilterConfig = field(default_factory=LocationFilterConfig)
    rental: RentalFilterConfig = field(default_factory=RentalFilterConfig)
    sale: SaleFilterConfig = field(default_factory=SaleFilterConfig)


@dataclass
class TelegramConfig:
    enabled: bool = False
    bot_token: str = ""
    chat_id: str = ""
    max_per_run: int = 10


@dataclass
class DiscordConfig:
    enabled: bool = False
    webhook_url: str = ""


@dataclass
class NotificationsConfig:
    telegram: TelegramConfig = field(default_factory=TelegramConfig)
    discord: DiscordConfig = field(default_factory=DiscordConfig)


@dataclass
class CsvExportConfig:
    enabled: bool = True
    output_dir: str = "results/csv"
    filename: str = "{name}_{date}.csv"
    append_mode: bool = True


@dataclass
class ExportConfig:
    csv: CsvExportConfig = field(default_factory=CsvExportConfig)


@dataclass
class AppConfig:
    general: GeneralConfig
    searches: List[SearchConfig]
    filters: FiltersConfig
    notifications: NotificationsConfig
    export: ExportConfig


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------

def _get(d: dict, *keys, default=None):
    """Safe nested dict accessor."""
    for k in keys:
        if not isinstance(d, dict):
            return default
        d = d.get(k, default)
    return d


def load_config(path: str = "config.yaml") -> AppConfig:
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"Config file not found: {path}\n"
            "Please copy config.yaml and fill in your settings."
        )

    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    # General
    g = raw.get("general", {})
    general = GeneralConfig(
        check_interval_seconds=g.get("check_interval_seconds", 3600),
        webdriver_path=g.get("webdriver_path", ""),
        log_level=g.get("log_level", "INFO"),
        headless=g.get("headless", True),
        disable_images_css=g.get("disable_images_css", True),
        page_load_timeout=g.get("page_load_timeout", 20),
        delay_between_pages=g.get("delay_between_pages", 1.0),
        delay_between_searches=g.get("delay_between_searches", 30),
        max_pages_per_search=g.get("max_pages_per_search", 20),
    )

    # Searches
    searches = []
    for s in raw.get("searches", []):
        if not s.get("url"):
            logger.warning("Search '%s' has no URL, skipping.", s.get("name"))
            continue
        searches.append(SearchConfig(
            name=s.get("name", "Unnamed"),
            type=s.get("type", "rental").lower(),
            url=s["url"],
            enabled=s.get("enabled", True),
        ))

    # Filters
    fraw = raw.get("filters", {})
    rraw = fraw.get("rental", {})
    sraw = fraw.get("sale", {})
    lraw = fraw.get("location_filter", {})
    filters = FiltersConfig(
        min_size_m2=fraw.get("min_size_m2"),
        max_size_m2=fraw.get("max_size_m2"),
        max_building_age_years=fraw.get("max_building_age_years"),
        location_filter=LocationFilterConfig(
            enabled=lraw.get("enabled", False),
            center_lat=float(lraw.get("center_lat", 0.0)),
            center_lng=float(lraw.get("center_lng", 0.0)),
            max_distance_km=float(lraw.get("max_distance_km", 5.0)),
        ),
        rental=RentalFilterConfig(
            max_rent_man_yen=rraw.get("max_rent_man_yen"),
            max_admin_fee_yen=rraw.get("max_admin_fee_yen"),
            max_deposit_man_yen=rraw.get("max_deposit_man_yen"),
            max_key_money_man_yen=rraw.get("max_key_money_man_yen"),
            allowed_layouts=rraw.get("allowed_layouts") or [],
            min_floor=rraw.get("min_floor"),
        ),
        sale=SaleFilterConfig(
            max_price_man_yen=sraw.get("max_price_man_yen"),
            max_price_per_tsubo=sraw.get("max_price_per_tsubo"),
        ),
    )

    # Notifications
    nraw = raw.get("notifications", {})
    traw = nraw.get("telegram", {})
    draw = nraw.get("discord", {})
    notifications = NotificationsConfig(
        telegram=TelegramConfig(
            enabled=traw.get("enabled", False),
            bot_token=traw.get("bot_token", ""),
            chat_id=traw.get("chat_id", ""),
            max_per_run=traw.get("max_per_run", 10),
        ),
        discord=DiscordConfig(
            enabled=draw.get("enabled", False),
            webhook_url=draw.get("webhook_url", ""),
        ),
    )

    # Export
    eraw = raw.get("export", {})
    craw = eraw.get("csv", {})
    export = ExportConfig(
        csv=CsvExportConfig(
            enabled=craw.get("enabled", True),
            output_dir=craw.get("output_dir", "results/csv"),
            filename=craw.get("filename", "{name}_{date}.csv"),
            append_mode=craw.get("append_mode", True),
        )
    )

    config = AppConfig(
        general=general,
        searches=searches,
        filters=filters,
        notifications=notifications,
        export=export,
    )

    _setup_logging(general.log_level)
    logger.info("Config loaded: %d search(es) configured.", len(searches))
    return config


def _setup_logging(level_str: str):
    numeric = getattr(logging, level_str.upper(), logging.INFO)
    logging.basicConfig(
        level=numeric,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
