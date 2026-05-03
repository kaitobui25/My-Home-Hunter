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

## Ghi chú tích hợp myhome.nifty.com (Nifty Integration Notes)

Quá trình phân tích và bóc tách dữ liệu từ myhome.nifty.com đòi hỏi các kỹ thuật tinh chỉnh đặc biệt, do Nifty có cấu trúc DOM phức tạp và cơ chế bảo mật (WAF) khắt khe hơn SUUMO. Dưới đây là tài liệu chi tiết:

### 1. Kiến trúc & Tích hợp (Architecture)
- **Kế thừa Class:** `NiftyRentalHunter` kế thừa từ `AbstractHunter` (để tái sử dụng logic check trùng lặp `seen_listings`) và `PlaywrightBase` (để quản lý instance browser và stealth mode).
- **Auto-Detection (`src/config.py`):** Lớp `SearchConfig` tự động gán `site = "nifty"` nếu domain của URL chứa `myhome.nifty.com`. Nhờ đó, người dùng có thể pass thẳng URL từ trình duyệt vào file `config.yaml` mà không cần cấu hình type của site thủ công.
- **Data Mapping Standardization:** Mọi dữ liệu crawl được (Giá, diện tích, tiền cọc, tuổi nhà...) đều trải qua các hàm regex parser (`_parse_man_yen`, `_parse_m2`, `_parse_floor_num`) để chuyển về kiểu dữ liệu chuẩn (như `float`, `int`). Nhờ đó, pipeline Geocoding và Telegram Filter hoạt động trơn tru 100% không cần code riêng cho Nifty.

### 2. Phân tích DOM & CSS Selectors Chi tiết
Nifty thiết kế DOM dạng bảng lồng ghép phức tạp:
- **Container Tòa nhà:** `li.result-bukken-list > div.card`.
- **Thông tin Tòa nhà:** 
  - Địa chỉ: Quét mảng thẻ `p.text.is-line-height-sm.is-sm` và dùng điều kiện logic kiểm tra sự tồn tại của từ khóa Tỉnh/Thành (VD: `府, 県, 都, 道, 市`) để lấy đúng chuỗi địa chỉ.
  - Di chuyển (Transport): Lấy toàn bộ thẻ `li[data-transport-access]` và map thành một chuỗi duy nhất cách nhau bởi `|`.
  - Tuổi nhà: Nằm trong cụm `.bukken-info-items dl`. Tìm thẻ `dt='築年数'`, sau đó lấy giá trị ở `dd` (VD: "40年"). *Edge case: Nifty ghi "40年" thay vì "築40年" như SUUMO, parser phải adapt theo format này.*
- **Dòng thông tin Phòng (Room Rows):** Nằm trong mảng `tbody.click-area`. Nifty nhóm các phòng của cùng 1 tòa nhà vào các thẻ `tbody` riêng biệt.
  - Giá thuê/Phí QL: Ở cột `td.bukken-info-rent` chứa 2 thẻ `p`. `p[0]` chứa giá thuê (có chữ `万円`), `p[1]` chứa phí quản lý (chữ `円`).
  - Tầng/Diện tích: Nằm rải rác ở các `td[data-link-wrap-item]`. Hàm quét text dựa trên keyword ("階", "LDK", "㎡") để định vị chính xác dữ liệu do số lượng cột thay đổi.
  - Tiền cọc/Lễ: Tìm các block `dl` có `dt` là "敷" (Cọc) và "礼" (Lễ). *Edge cases xử lý: Convert "不要" / "なし" thành 0.0; tự động chia tiền Yên ra Man nếu cần thiết.*
  - Link chi tiết: Thẻ `a[href*='/detail_']` bên trong `tbody`.

### 3. Logic Phân trang (Pagination)
- **URL-based Routing:** Nifty không dùng Infinite Scroll mà sử dụng cơ chế phân trang truyền thống trên URL (VD trang 2: `.../toyonakashi_ct/2/?r1=...`).
- **Thuật toán Next Page:** Thay vì click JS dễ gây lỗi timeout, tool tìm thẻ `a` trong các container `.pager a, .pagination a`. Nó đối chiếu xem nội dung text có chứa `page_num + 1` hoặc thuộc tính `href` có chứa URL pattern `/{page_num + 1}/?` hay không. Nếu có, nó bóc tách absolute URL để gán cho lượt chạy (scroll loop) tiếp theo.

### 4. Vượt rào cản WAF / Anti-Bot (Playwright Stealth)
Nifty sử dụng bot protection của Akamai / AWS WAF chặn rất gắt các session tự động hóa (hiển thị màn hình lỗi `ただいま込み合っております` chặn request ngay lần đầu). Giải pháp stealth bao gồm:
1. **Khởi chạy Chromium với Flag:** Truyền `--disable-blink-features=AutomationControlled` để tắt biến cờ automation ở cấp độ Engine browser.
2. **Xóa dấu vết Webdriver:** Inject đoạn JS tĩnh vào context để gỡ thuộc tính giả lập ở mọi tab: `Object.defineProperty(navigator, 'webdriver', { get: () => undefined });`.
3. **Fake User-Agent & Headers:** Giả lập Profile trình duyệt như máy thực: `Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36`. Set Locale `ja-JP` và Timezone `Asia/Tokyo`.
4. **Tối ưu tài nguyên mạng:** Sử dụng Interception routing của Playwright (`page.route("**/*")`) để block load ảnh (`.jpg`, `.png`), font chữ và style CSS. Điều này làm trang load cực nhẹ, chống Time-out và giảm tần suất tải file tĩnh, tránh gây sự chú ý với firewall của Nifty.

### 5. Kết quả Testing & Báo cáo giới hạn (Akamai WAF)
- **Tình trạng chặn IP Datacenter:** Akamai WAF của Nifty chặn quyết liệt các dải IP của VPS/Cloud. Ngay cả khi đã áp dụng toàn bộ kỹ thuật Stealth, kết nối từ VPS vẫn sẽ bị Tarpit (treo kết nối cho đến timeout).
- **Thất bại của phương pháp Hybrid (Cookie Injection):** Việc copy toàn bộ Cookie (đặc biệt là `ak_bmsc`, `bm_sv` của Akamai) từ máy tính cá nhân thật (Local) và bơm vào VPS **không hoạt động** với Nifty. Nguyên nhân cốt lõi là Akamai mã hóa Cookie này và ràng buộc chặt chẽ nó với **Địa chỉ IP gốc** cùng **Chữ ký TLS (JA3 Fingerprint)** của trình duyệt lúc khởi tạo. Khi mang lên VPS, IP và TLS thay đổi, Akamai sẽ phát hiện sự bất thường, lập tức reset DOM và đẩy về trang Captcha, gây ra lỗi Timeout.
- **Khuyến nghị & Giải pháp:** Không thể dùng các proxy API (như LumiProxy Scraper API) vì chính các API này cũng được bảo vệ bởi Cloudflare và chặn IP tự động. Để hệ thống có thể phân trang và lấy data ổn định trên VPS, bắt buộc phải tích hợp **Proxy Dân Cư (Residential Proxy)** vào thẳng Playwright. Hệ thống cảnh báo qua Telegram hiện tại được giữ lại để thông báo lập tức nếu WAF chặn request, hỗ trợ việc giám sát và cài đặt Proxy sau này.

---

## License

GNU General Public License v3.0 — xem [LICENSE](LICENSE).

---

---

## Local Runner — Chạy trên máy PC thật (Vượt WAF & Đồng bộ đa thiết bị)

Do hệ thống chống bot như Akamai WAF chặn quyết liệt địa chỉ IP Datacenter (VPS) của các trang như Nifty, giải pháp được đưa ra là cung cấp một `Local Runner` độc lập. Bạn có thể chạy script này ngay trên **máy tính cá nhân** (với IP dân cư) để vượt qua WAF một cách tự nhiên. File thực thi là `src/local/run_local.py`, hỗ trợ chạy **tất cả** các trang web (SUUMO, Nifty...) có `enabled: true` trong cấu hình.

### Cách chạy

```bash
# Chạy mọi search đang được bật (enabled: true)
python -m src.local.run_local

# Chạy ẩn cửa sổ browser
python -m src.local.run_local --headless

# Chỉ chạy một search cụ thể
python -m src.local.run_local --search "Toyonaka Rental"

# [MỚI] Reset toàn bộ cờ Telegram để gửi lại tin nhắn cho mọi listing đã lưu
python -m src.local.run_local --reset-tele

# [MỚI] Lọc lại dữ liệu cũ với config hiện tại mà không mở browser (dùng khi vừa đổi khoảng cách, giá...)
python -m src.local.run_local --refilter

# [MỚI] Reset và gửi lại Telegram ngay lập tức
python -m src.local.run_local --reset-tele --refilter
```

### Kiến trúc & Điểm nổi bật

- **Tự động nhận diện Scraper:** `Local Runner` đọc chung `config.yaml` với VPS. Nó sẽ tự động gọi `SUUMORentalHunter` hoặc `NiftyRentalHunter` tùy theo link bạn nhập, không còn bị giới hạn ở Nifty.
- **Đồng bộ qua Git (Cross-PC Sync):** 
  - Lịch sử được lưu tại `results-local/local_seen_listings.json`. 
  - Thư mục này được **commit thẳng vào Git**. Nhờ đó, khi bạn pull code ở máy khác (VD: Máy công ty -> Máy nhà), trạng thái đã xem sẽ được đồng bộ. Bạn không bị nhận tin nhắn trùng lặp dù đổi máy.
- **Cơ chế `tele_sent`:** 
  - Script chỉ đánh dấu hoàn tất một listing khi tin nhắn Telegram gửi đi thành công (`tele_sent = True`).
  - Những căn bị loại bởi bộ lọc sẽ giữ trạng thái `False`. Nhờ lệnh `--refilter`, nếu bạn nới lỏng điều kiện filter (VD: tăng bán kính km), script sẽ dùng data cũ để lọc lại và gửi thông báo những căn mới lọt lưới, **mà không cần mở trình duyệt scrape lại**.
- **Headless mặc định:** `False` (mở browser thật) để giảm khả năng bị WAF phát hiện.

### Lưu ý vận hành

- Tính năng chạy Local hỗ trợ cực tốt cho việc Test Cấu hình (`config.yaml`) nhờ khả năng `--refilter` tức thì.
- Chạy độc lập, không làm ảnh hưởng đến tiến trình chạy nền trên VPS.

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

### 4. Nâng cấp API Bản đồ Chính phủ Nhật Bản (GSI)
- Hệ thống **đã loại bỏ hoàn toàn Nominatim** (do kém chính xác với địa chỉ Nhật Bản) và chuyển sang sử dụng API của **Quốc Tế Địa Lý Viện Nhật Bản (GSI - msearch)**.
- Đảm bảo nhận diện chính xác 100% địa chỉ theo chuẩn Nhật, giải quyết hoàn toàn lỗi "Not Found" của các thư viện phương Tây.
- Tọa độ vẫn được cache vĩnh viễn tại `results/geocode_cache.json` để tăng tốc cực độ và không bị nghẽn mạng.

---

## Các công cụ phụ trợ (Independent Tools)

Ngoài việc crawl nhà, dự án còn cung cấp các công cụ phụ trợ dùng để lọc các tiện ích xung quanh (như Trường mầm non - Hoikuen) nằm gọn trong thư mục `my-data/src/`:

- **`1_extract_geocode.py`**: 
  - Đọc danh sách địa chỉ (VD: hàng trăm trường mầm non) từ file txt.
  - Tự động làm sạch chuỗi địa chỉ, loại bỏ tên tòa nhà, và áp dụng thuật toán **"Fallback" thông minh** (tự động cắt lùi dần số nhà nếu quá chi tiết) để luôn luôn quét ra được tọa độ của khu vực.
  - Xuất toàn bộ ra file `schools_geocoded.json`.
- **`2_filter_schools.py`**:
  - Đọc trực tiếp tọa độ nhà (`center_lat`, `center_lng`) và bán kính (`max_distance_km`) từ file `config.yaml` của Home-Hunter.
  - Rà soát file JSON trên và in thẳng ra Terminal danh sách các trường học nằm trong phạm vi tìm kiếm của bạn, xếp thứ tự từ gần nhất đến xa nhất.
