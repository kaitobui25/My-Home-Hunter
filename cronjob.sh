#!/bin/bash
# HƯỚNG DẪN THIẾT LẬP CRONJOB TRÊN UBUNTU VPS (CHẠY MỖI 8 TIẾNG)
# =========================================================================

# BƯỚC 1: Lấy đường dẫn tuyệt đối của thư mục dự án
# Mở Terminal trên Ubuntu, di chuyển vào thư mục dự án và gõ lệnh:
# pwd
# (Giả sử kết quả trả về là: /root/home-hunter)

# BƯỚC 2: Mở bảng cài đặt Cronjob
# Gõ lệnh sau trên Terminal:
# crontab -e
# (Nếu máy hỏi chọn Editor, hãy chọn Nano - thường là số 1)

# BƯỚC 3: Thêm lệnh chạy tự động
# Cuộn xuống dưới cùng của file và thêm dòng sau:
# (LƯU Ý: Thay "/root/home-hunter" bằng kết quả ở BƯỚC 1 của bạn)

# 0 */8 * * * cd /root/home-hunter && /root/home-hunter/venv/bin/python run.py --once >> /root/home-hunter/cron.log 2>&1

# Giải thích dòng lệnh trên:
# - `0 */8 * * *` : Chạy vào phút thứ 0, mỗi 8 tiếng một lần (ví dụ: 0:00, 8:00, 16:00).
# - `cd /root/home-hunter` : Di chuyển vào đúng thư mục dự án.
# - `.../venv/bin/python run.py --once` : Gọi đúng Python trong môi trường ảo (venv) và chạy ở chế độ 1 lần (--once).
# - `>> cron.log 2>&1` : Ghi toàn bộ kết quả (kể cả lỗi nếu có) vào file cron.log để bạn dễ dàng theo dõi.

# BƯỚC 4: Lưu và thoát
# - Bấm Ctrl + O để lưu -> Enter để xác nhận.
# - Bấm Ctrl + X để thoát.

# BƯỚC 5: Kiểm tra xem đã lưu thành công chưa
# crontab -l

# MẸO KIỂM TRA NHANH:
# Nếu bạn muốn chạy thử ngay lập tức (chạy mỗi 2 phút) để xem cấu hình đúng chưa, dùng dòng này:
# */2 * * * * cd /root/home-hunter && /root/home-hunter/venv/bin/python run.py --once >> /root/home-hunter/cron.log 2>&1
