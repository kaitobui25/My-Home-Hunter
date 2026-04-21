"""
src/notifier/telegram.py
Sends listing notifications via Telegram Bot API.
"""
from __future__ import annotations

import logging
import time

import requests

from src.config import TelegramConfig

logger = logging.getLogger(__name__)

TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"


class TelegramNotifier:
    def __init__(self, config: TelegramConfig):
        self.cfg = config

    def send_batch(self, listings: list[dict], search_name: str = "") -> int:
        """
        Send notifications for a list of matched listings.
        Respects max_per_run limit.
        Returns the number of messages actually sent.
        """
        if not self.cfg.enabled:
            logger.info("Telegram: disabled, skipping %d notifications.", len(listings))
            return 0
        if not self.cfg.bot_token or not self.cfg.chat_id:
            logger.warning("Telegram: bot_token or chat_id not configured.")
            return 0
        if not listings:
            return 0

        to_send = listings[: self.cfg.max_per_run]
        skipped = len(listings) - len(to_send)

        if search_name and len(listings) > 0:
            header = (
                f"🏠 *Home-Hunter* — {search_name}\n"
                f"Found *{len(listings)}* new matching listing(s)"
            )
            if skipped:
                header += f" (showing first {len(to_send)})"
            self._send_text(header)
            time.sleep(0.5)

        sent = 0
        for listing in to_send:
            msg = self._format(listing)
            if self._send_text(msg):
                sent += 1
            time.sleep(0.3)  # Rate limit: Telegram allows ~30 msg/sec

        if skipped:
            self._send_text(
                f"... và còn *{skipped}* listings khác phù hợp. "
                f"Kiểm tra file CSV để xem đầy đủ."
            )

        logger.info("Telegram: sent %d/%d notifications.", sent, len(listings))
        return sent

    def send_text(self, text: str) -> bool:
        """Public helper for sending a plain text message."""
        return self._send_text(text)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _send_text(self, text: str) -> bool:
        url = TELEGRAM_API.format(token=self.cfg.bot_token)
        payload = {
            "chat_id": self.cfg.chat_id,
            "text": text,
            "parse_mode": "Markdown",
            "disable_web_page_preview": False,
        }
        try:
            resp = requests.post(url, json=payload, timeout=10)
            resp.raise_for_status()
            return True
        except requests.exceptions.HTTPError as e:
            logger.error("Telegram API error: %s — %s", e, resp.text)
        except requests.exceptions.RequestException as e:
            logger.error("Telegram request failed: %s", e)
        return False

    def _format(self, listing: dict) -> str:
        """Format a listing dict into a readable Telegram Markdown message."""
        listing_type = listing.get("listing_type", "")
        name = listing.get("name", "Unknown")
        url = listing.get("url", "")
        address = listing.get("address", "")
        transport = listing.get("transportation", "").replace("\n", " | ")

        map_link = ""
        lat = listing.get("lat")
        lng = listing.get("lng")
        if lat is not None and lng is not None:
            map_link = f" | 🗺️ [Bản đồ](https://www.google.com/maps?q={lat},{lng})"

        if listing_type == "rental":
            rent = listing.get("price_raw", "N/A")
            admin = listing.get("admin_fee_raw", "-")
            deposit = listing.get("deposit_raw", "-")
            key = listing.get("key_money_raw", "-")
            layout = listing.get("layout", "N/A")
            size = listing.get("size_raw", "N/A")
            floor = listing.get("floor", "N/A")
            age = listing.get("building_age_raw") or "N/A"

            return (
                f"🏠 *{name}*\n"
                f"💰 Giá thuê: *{rent}* / Phí QL: {admin}\n"
                f"📐 DT: {size} | Sơ đồ: *{layout}* | Tầng: {floor}\n"
                f"🔑 Đặt cọc: {deposit} | Tiền lễ: {key}\n"
                f"🏗️ Tuổi nhà: {age}\n"
                f"📍 {address}\n"
                f"🚉 {transport}\n"
                f"🔗 [Xem chi tiết]({url}){map_link}"
            )
        else:  # sale
            price = listing.get("price_raw", "N/A")
            size = listing.get("size_raw", "N/A")
            per_tsubo = listing.get("price_per_tsubo", "N/A")
            bcr = listing.get("building_coverage_ratio", "N/A")
            far = listing.get("floor_area_ratio", "N/A")

            return (
                f"🏡 *{name}* (Mua bán)\n"
                f"💰 Giá: *{price}* | Đơn giá/坪: {per_tsubo}\n"
                f"📐 DT: {size}\n"
                f"📊 Tỷ lệ XD: {bcr} | Hệ số sàn: {far}\n"
                f"📍 {address}\n"
                f"🚉 {transport}\n"
                f"🔗 [Xem chi tiết]({url}){map_link}"
            )

