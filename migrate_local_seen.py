"""
migrate_local_seen.py
=====================
Script migration MỘT LẦN: chuyển dữ liệu từ:
  src/local/seen_listings.json  (format cũ, không git-tracked)
sang:
  results-local/local_seen_listings.json  (format mới, git-tracked)

Format cũ: { "url": {} | {...listing_data} }
Format mới: { "url": { ...listing_data, tele_sent: bool, tele_sent_at: str|null, first_seen_at: str } }

Cách chạy (từ thư mục gốc My-Home-Hunter):
  python migrate_local_seen.py

Script này an toàn để chạy nhiều lần (idempotent):
  - Nếu results-local/local_seen_listings.json đã tồn tại, sẽ MERGE (không ghi đè).
  - Nếu src/local/seen_listings.json không tồn tại, script báo không cần migrate.
"""
from __future__ import annotations

import json
import os
from datetime import datetime

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
OLD_FILE = os.path.join(PROJECT_ROOT, "src", "local", "seen_listings.json")
NEW_DIR  = os.path.join(PROJECT_ROOT, "results-local")
NEW_FILE = os.path.join(NEW_DIR, "local_seen_listings.json")

FALLBACK_TIME = "2026-01-01T00:00:00+00:00"  # Timestamp giả cho các entry cũ không có metadata


def load_json(path: str) -> dict:
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(data: dict, path: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def convert_old_entry(url: str, value: dict | None) -> dict:
    """Chuyển entry cũ sang format mới."""
    base = value if isinstance(value, dict) and value else {}
    
    # Các entry cũ có data đầy đủ (đã scraped và gửi tele) → tele_sent=True
    # Các entry cũ chỉ có {} rỗng → không rõ, giả sử đã seen nhưng chưa gửi
    has_data = bool(base)
    
    scraped_at = base.get("scraped_at", FALLBACK_TIME)
    
    return {
        **base,
        "url": url,
        "tele_sent": has_data,          # có data = đã gửi; {} rỗng = chưa rõ
        "tele_sent_at": scraped_at if has_data else None,
        "first_seen_at": scraped_at,
        "_migrated": True,              # đánh dấu để trace lại nếu cần
    }


def main():
    print("=" * 60)
    print("  LOCAL SEEN LISTINGS — MIGRATION TOOL")
    print("=" * 60)

    # Kiểm tra file nguồn
    if not os.path.exists(OLD_FILE):
        print(f"\n✅ Không cần migrate: File cũ không tồn tại.")
        print(f"   ({OLD_FILE})")
        return

    old_data = load_json(OLD_FILE)
    print(f"\n📂 File cũ: {OLD_FILE}")
    print(f"   → {len(old_data)} entries")

    # Load file mới nếu đã tồn tại (để merge)
    new_data = load_json(NEW_FILE)
    already_in_new = len(new_data)
    print(f"\n📂 File mới: {NEW_FILE}")
    print(f"   → {already_in_new} entries hiện có (sẽ merge)")

    # Migrate
    migrated = 0
    skipped = 0
    for url, value in old_data.items():
        if url in new_data:
            skipped += 1
            continue  # Đã có trong file mới, không ghi đè
        new_data[url] = convert_old_entry(url, value)
        migrated += 1

    # Lưu
    save_json(new_data, NEW_FILE)

    print(f"\n✅ Migration hoàn tất!")
    print(f"   + {migrated} entries mới đã migrate")
    print(f"   = {skipped} entries đã bỏ qua (đã tồn tại)")
    print(f"   → Tổng cộng: {len(new_data)} entries trong file mới")
    print(f"\n📌 File mới đã lưu tại: {NEW_FILE}")
    print()
    print("⚠️  BƯỚC TIẾP THEO:")
    print("   1. Kiểm tra results-local/local_seen_listings.json")
    print("   2. git add results-local/local_seen_listings.json")
    print("   3. git commit -m 'feat: migrate local seen listings to results-local'")
    print("   4. git push  →  PC2 git pull sẽ có đầy đủ history")
    print("=" * 60)


if __name__ == "__main__":
    main()
