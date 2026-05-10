import json
import time
from playwright.sync_api import sync_playwright

def get_cookies():
    print("Mở trình duyệt để lấy Cookie vượt tường lửa...")
    with sync_playwright() as p:
        browser = p.chromium.launch(
            channel="chrome",
            headless=False,
            args=["--no-sandbox", "--disable-blink-features=AutomationControlled"]
        )
        context = browser.new_context(
            viewport={"width": 1920, "height": 1080},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            locale="ja-JP",
            timezone_id="Asia/Tokyo",
        )
        # Bỏ dấu vết automation
        context.add_init_script("Object.defineProperty(navigator, 'webdriver', { get: () => undefined });")
        
        page = context.new_page()
        url = "https://www.homes.co.jp/chintai/osaka/osaka_yodogawa-city/list/"
        print(f"Đang truy cập: {url}")
        
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=60000)
        except Exception as e:
            print(f"Lỗi khi tải trang: {e}")
            
        print("Đang chờ duyệt AWS WAF (thường mất 5-10 giây)...")
        print("LƯU Ý: Nếu trình duyệt yêu cầu xác nhận Captcha (click vào ô vuông), hãy click tay giúp tôi nhé!")
        
        success = False
        for i in range(15):  # Chờ tối đa 30 giây
            try:
                title = page.title()
                # Nếu tiêu đề có chữ "賃貸" (Chintai) nghĩa là đã vào trang chủ thành công
                if "賃貸" in title and "403" not in title:
                    success = True
                    break
            except Exception:
                pass # Đang reload
            time.sleep(2)
            
        if success:
            print("Tuyệt vời! Đã vượt qua tường lửa thành công.")
        else:
            print("Chưa thấy trang web thật, nhưng sẽ thử lưu Cookie hiện tại...")

        cookies = context.cookies()
        with open("homes_cookies.json", "w", encoding="utf-8") as f:
            json.dump(cookies, f, indent=2)
            
        print(f"Đã lưu {len(cookies)} cookies vào homes_cookies.json!")
        browser.close()

if __name__ == "__main__":
    get_cookies()
