# Home-Hunter

<img src="https://github.com/zaneriley/home-hunter/blob/main/logo.png?raw=true" alt="Home Hunter logo" width="400">

Công cụ tự động theo dõi tin đăng bất động sản trên **SUUMO** (Nhật Bản) và gửi thông báo qua **Telegram** khi có căn nhà mới phù hợp với điều kiện của bạn.

## Tính năng

- **Hỗ trợ cả 2 loại**: Thuê nhà (`chintai`) và Mua bán/Đất (`bukken`)
- **Nhiều link tìm kiếm**: Cấu hình bao nhiêu link SUUMO cũng được
- **Phân trang tự động**: Quét qua tất cả các trang kết quả
- **Lọc theo điều kiện**: Giá, diện tích, sơ đồ, tuổi nhà, tiền đặt cọc...
- **Thông báo Telegram**: Chỉ gửi khi có tin mới **phù hợp tiêu chí**
- **Xuất CSV**: Lưu lại toàn bộ kết quả mỗi lần quét
- **Tránh thông báo trùng**: Ghi nhớ tin đã thấy qua `seen_listings.json`

---

## Cài đặt nhanh

```bash
git clone https://github.com/zaneriley/home-hunter.git
cd home-hunter
python -m venv venv
source venv/bin/activate     # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

---

## Cấu hình

**Chỉ cần chỉnh 1 file duy nhất: `config.yaml`**

### 1. Thêm link tìm kiếm SUUMO

```yaml
searches:
  - name: "Toyonaka Rental"    # Tên tùy ý (dùng làm tên file CSV)
    type: rental                # rental = thuê nhà | sale = mua bán
    enabled: true
    url: "https://suumo.jp/jj/chintai/ichiran/FR301FC001/..."

  - name: "Tokyo Land"
    type: sale
    enabled: false              # false = tạm tắt, không cần xóa
    url: "https://suumo.jp/jj/bukken/ichiran/JJ012FC003/..."
```

### 2. Cài điều kiện lọc

```yaml
filters:
  min_size_m2: 30             # Diện tích tối thiểu 30m²
  max_building_age_years: 35  # Nhà không quá 35 năm tuổi

  rental:
    max_rent_man_yen: 8.0     # Giá thuê tối đa 8万円
    allowed_layouts:
      - "2LDK"
      - "2DK"
      - "3LDK"
```

### 3. Cài Telegram

1. Tạo bot: Chat với [@BotFather](https://t.me/BotFather) → `/newbot` → lấy **token**
2. Lấy chat_id: Vào `https://api.telegram.org/bot<TOKEN>/getUpdates` sau khi nhắn bot
3. Điền vào config:

```yaml
notifications:
  telegram:
    enabled: true
    bot_token: "1234567890:ABCDEFabcdef..."
    chat_id: "-1001234567890"
```

---

## Chạy

```bash
# Chạy 1 lần rồi thoát
python run.py --once

# Chạy vòng lặp (kiểm tra theo check_interval_seconds trong config)
python run.py

# Chỉ chạy 1 search cụ thể
python run.py --once --search "Toyonaka Rental"
```

### Chạy bằng Docker

```bash
# Cấu hình config.yaml xong, rồi:
docker-compose up --build
```

---

## Cấu trúc dự án

```
home-hunter/
├── config.yaml          <- Config duy nhất (URL, filter, Telegram, CSV)
├── run.py               <- Entry point chính
├── src/
│   ├── config.py        <- Đọc và validate config.yaml
│   ├── filter.py        <- Lọc listings theo điều kiện
│   ├── scraper/
│   │   ├── base.py      <- Lớp cơ sở (WebDriver + seen_listings)
│   │   ├── rental_hunter.py  <- Scraper cho thuê nhà (chintai)
│   │   └── sale_hunter.py    <- Scraper cho mua bán (bukken)
│   ├── notifier/
│   │   └── telegram.py  <- Gửi thông báo Telegram
│   └── exporter/
│       └── csv_exporter.py   <- Xuất ra CSV
└── results/
    ├── csv/             <- File CSV kết quả mỗi ngày
    └── seen_listings/   <- Lịch sử tin đã thấy (JSON)
```

---

## Output mẫu (CSV)

| name | listing_type | price_raw | layout | size_m2 | floor | building_age | transportation |
|:-----|:------------|:---------|:-------|:--------|:------|:-------------|:--------------|
| パリス北桜塚 | rental | 6.8万円 | 2DK | 45.34 | 3階 | - | 豊中駅 歩11分 |

## Telegram Notification

```
🏠 Home-Hunter — Toyonaka Rental
Found 3 new matching listing(s)

🏠 パリス北桜塚
💰 Giá thuê: 6.8万円 / Phí QL: 5000円
📐 DT: 45.34m2 | Sơ đồ: 2DK | Tầng: 3階
🔑 Đặt cọc: - | Tiền lễ: 15万円
🏗️ Tuổi nhà: N/A
📍 大阪府豊中市北桜塚２
🚉 豊中駅 歩11分
🔗 Xem chi tiết
```

---

## License

GNU General Public License v3.0 — xem [LICENSE](LICENSE).

---

---

## Ghi chú về logic quét dữ liệu (SUUMO)

Trong quá trình phát triển và kiểm thử, chúng tôi đã tối ưu hóa logic để trích xuất dữ liệu chính xác nhất từ cấu trúc phức tạp của SUUMO:

### 1. Phân trang tự động (Pagination)
Script không chỉ dừng lại ở trang đầu tiên mà sẽ tự động tìm nút **"Tiếp" (次へ)** ở cuối trang để chuyển sang trang kế tiếp.
- Quá trình này lặp lại cho đến khi không còn trang nào hoặc chạm giới hạn `max_pages_per_search` (cấu hình trong `config.yaml`).
- Điều này đảm bảo bạn không bỏ lỡ bất kỳ tin đăng nào nằm ở các trang sau.

### 2. Phân biệt Tòa nhà (Building) và Căn hộ (Room)
Đây là điểm quan trọng nhất để hiểu về số lượng kết quả:
- **Cấu trúc hiển thị**: SUUMO hiển thị kết quả theo dạng **"Cassette"** (mỗi tòa nhà là một khối). Bên trong mỗi khối tòa nhà là danh sách các **Căn hộ** đang trống.
- **Ví dụ thực tế**: Trong một lần thử nghiệm với link tìm kiếm hiển thị **"272件"**, script đã quét qua 4 trang kết quả và tìm thấy:
    - **66 tòa nhà** (Buildings).
    - **79 căn hộ** (Rooms) đang hiển thị sẵn.
- **Giải mã con số**: Con số "272" trên web là tổng số **Căn hộ** thỏa mãn điều kiện. Tuy nhiên, trên trang danh sách tổng hợp, SUUMO chỉ hiển thị các căn hộ tiêu biểu cho mỗi tòa nhà.

### 3. Tại sao số lượng Rooms quét được ít hơn số "件" trên Web?
- Mỗi tòa nhà trong danh sách thường chỉ hiển thị từ **1 đến 3 căn hộ** đại diện.
- Các căn hộ khác cùng tòa nhà thường bị ẩn sau nút "Xem tất cả các phòng".
- **Chiến lược của script**: Script lấy toàn bộ các phòng đang **hiển thị sẵn** trên tất cả các trang kết quả. Khi bạn kết hợp với bộ lọc và sắp xếp theo **"Mới nhất" (Newest - `po1=25`)**, các phòng mới đăng sẽ luôn xuất hiện ở những trang đầu, giúp bạn nhận thông báo Telegram kịp thời mà không cần phải quét sâu vào từng tòa nhà (giúp tăng tốc độ và tránh bị SUUMO chặn IP).

### 4. Chi tiết dữ liệu trích xuất
Hệ thống không chỉ lấy thông tin chung mà bóc tách chi tiết từng dòng căn hộ (`tr`) để có dữ liệu chính xác cho bộ lọc:
- **Thông tin tài chính**: Tiền thuê, Phí quản lý, Tiền cọc (Deposit), Tiền lễ (Key Money).
- **Thông tin căn hộ**: Diện tích (m²), Sơ đồ phòng (Layout), Tầng, Tuổi nhà.
- **Thông tin vị trí**: Địa chỉ chi tiết, thông tin di chuyển (số phút đi bộ đến ga).
---

## Cập nhật mới (Geocoding & Tối ưu RAM)

Hệ thống đã được nâng cấp mạnh mẽ để chạy ổn định hơn trên các máy chủ cấu hình thấp (như VPS 1GB RAM) và hỗ trợ tìm kiếm theo vị trí địa lý chính xác.

### 1. Tích hợp Geocoding & Lọc theo Bán kính
- **Tọa độ trung tâm**: Bạn có thể nhập tọa độ (`lat`, `lng`) của một địa điểm (ví dụ: Ga tàu, nơi làm việc) và đặt bán kính (km).
- **Tính khoảng cách**: Script tự động dịch địa chỉ nhà thành tọa độ và tính khoảng cách đường chim bay. Chỉ những căn nhà nằm trong bán kính cho phép mới được gửi thông báo.
- **Bản đồ trực tuyến**: Tin nhắn Telegram được bổ sung link **Google Maps** dẫn trực tiếp đến vị trí căn hộ.

### 2. Tối ưu hóa RAM "Nhịn đói" (Level 1)
- **Cấu hình `disable_images_css`**: Khi bật (`true`), Chrome Headless sẽ chặn tải toàn bộ Hình ảnh, CSS và Phông chữ.
- **Hiệu quả**: Giảm tiêu thụ RAM từ ~450MB xuống còn khoảng **200MB - 250MB** mỗi lần quét. Giúp hệ thống chạy cực mượt trên VPS 1GB mà không lo bị treo.

### 3. Bộ nhớ chung (Global Deduplication)
- **Tránh báo trùng chéo**: Trước đây, nếu 2 link tìm kiếm của bạn có kết quả trùng nhau, bạn sẽ nhận 2 tin nhắn. Giờ đây, hệ thống dùng một file "trí nhớ chung" (`global_seen_listings.json`). Một căn nhà đã báo ở link này sẽ **không bao giờ** bị báo lại ở link khác.

### 4. Cache tọa độ (Geocode Cache)
- Hệ thống lưu lại tọa độ của các địa chỉ đã dịch vào `results/geocode_cache.json`. Điều này giúp tiết kiệm băng thông, tránh bị khóa IP do gọi API địa lý quá nhiều và tăng tốc độ quét cho các lần sau.
