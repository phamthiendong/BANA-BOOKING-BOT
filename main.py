import os
import re
import time
import threading
import calendar
from datetime import datetime, timedelta

from services.gmail_service import get_emails_by_date, get_realtime_emails
from services.booking_parser import parse_booking_email
from services.sheet_service import (
    setup_header,
    append_booking,
    create_sheet_if_not_exists,
    update_sheet_booking_status,
)
from services.db_service import (
    init_db,
    cleanup_old_data,
    check_booking_exists,
    insert_booking,
    update_booking_status,
)

# ================================================================
# BIẾN TOÀN CỤC
# ================================================================
current_sheet_id_holder = [None]   # [0] = link hiện tại
sheet_month_holder = [None]         # [0] = tháng của link hiện tại (VD: 6)
sheet_year_holder = [None]          # [0] = năm của link hiện tại (VD: 2026)
new_sheet_pending = [None]          # [0] = link mới đang chờ áp dụng
so_ngay_holder = [5]                # [0] = số ngày quét (1-5)

# ================================================================
# HELPER FUNCTIONS
# ================================================================
def extract_sheet_id(url_or_id):
    match = re.search(r"/d/([a-zA-Z0-9-_]+)", url_or_id)
    if match:
        return match.group(1)
    return url_or_id.strip()

def get_last_day_of_month(year, month):
    return calendar.monthrange(year, month)[1]

def get_danh_sach_ngay(ngay_bat_dau, so_ngay):
    result = []
    for i in range(so_ngay):
        next_date = ngay_bat_dau + timedelta(days=i)
        result.append(next_date.strftime("%Y-%m-%d"))
    return result

def canh_bao_cuoi_thang(today, sheet_month, sheet_year):
    last_day = get_last_day_of_month(sheet_year, sheet_month)
    ngay_con_lai = last_day - today.day
    if today.month == sheet_month and 0 <= ngay_con_lai <= 2:
        print("\n" + "⚠️ " * 20)
        print(f"🔔 CẢNH BÁO: Còn {ngay_con_lai + 1} ngày nữa là hết tháng {sheet_month}/{sheet_year}!")
        print(f"📋 Vui lòng chuẩn bị Link Google Sheets cho tháng mới!")
        print(f"👉 Gõ lệnh 'doi' + Enter để nhập link mới sẵn sàng chuyển giao!")
        print("⚠️ " * 20 + "\n")

def kiem_tra_het_thang(today, sheet_month, sheet_year):
    return today.month != sheet_month or today.year != sheet_year

def listen_for_commands():
    """Chạy ngầm, lắng nghe lệnh 'doi' từ người dùng"""
    while True:
        try:
            user_input = input().strip().lower()
            if user_input == "doi":
                print("\n" + "=" * 60)
                print("🔄 NHẬP LINK GOOGLE SHEETS THÁNG MỚI")
                print("=" * 60)

                current_month = sheet_month_holder[0]
                current_year = sheet_year_holder[0]

                if current_month == 12:
                    next_month = 1
                    next_year = current_year + 1
                else:
                    next_month = current_month + 1
                    next_year = current_year

                print(f"📅 Link này sẽ dùng cho tháng {next_month}/{next_year}")
                new_link = input("Dán Link Google Sheets mới: ").strip()

                if not new_link:
                    print("❌ Link trống, hủy bỏ.")
                    continue

                new_id = extract_sheet_id(new_link)

                if new_id == current_sheet_id_holder[0]:
                    print("❌ Đây là link cũ! Vui lòng nhập link của tháng mới.")
                    continue

                new_sheet_pending[0] = {
                    "sheet_id": new_id,
                    "month": next_month,
                    "year": next_year
                }
                print(f"✅ Đã lưu link mới cho tháng {next_month}/{next_year}!")
                print(f"🕐 Link sẽ tự động kích hoạt khi sang tháng {next_month}.")
        except Exception:
            pass

def ap_dung_link_moi_neu_co(today):
    pending = new_sheet_pending[0]
    if not pending:
        return False
    if today.month == pending["month"] and today.year == pending["year"]:
        current_sheet_id_holder[0] = pending["sheet_id"]
        sheet_month_holder[0] = pending["month"]
        sheet_year_holder[0] = pending["year"]
        new_sheet_pending[0] = None
        print("\n" + "🎉 " * 15)
        print(f"🚀 CHUYỂN SANG THÁNG {pending['month']}/{pending['year']} THÀNH CÔNG!")
        print(f"📊 Đang dùng Sheets mới: {pending['sheet_id']}")
        print("🎉 " * 15 + "\n")
        return True
    return False

# ================================================================
# MAIN
# ================================================================
def main():
    print("=" * 60)
    print("🤖 TOOL QUÉT GMAIL KLOOK (VERSION: REALTIME HYBRID PRO)")
    print("=" * 60)

    init_db()

    print("\n[CẤU HÌNH VÙNG QUẢN LÝ SHEETS]")

    # 1. Nhập link
    while True:
        link_nhap_vao = input("1. Nhập Link Google Sheets: ").strip()
        if not link_nhap_vao:
            print("❌ Lỗi: Bạn chưa dán Link!")
        else:
            current_sheet_id_holder[0] = extract_sheet_id(link_nhap_vao)
            break

    # 2. Nhập ngày bắt đầu
    while True:
        ngay_bat_dau_str = input("2. Nhập NGÀY BẮT ĐẦU (YYYY-MM-DD): ").strip().replace("/", "-")
        try:
            ngay_bat_dau_obj = datetime.strptime(ngay_bat_dau_str, "%Y-%m-%d")
            break
        except ValueError:
            print("❌ Sai định dạng! Gõ chuẩn YYYY-MM-DD")

    # 3. Nhập số ngày quét (1-5)
    while True:
        so_ngay_str = input("3. Số ngày quét (1-5): ").strip()
        try:
            so_ngay = int(so_ngay_str)
            if so_ngay < 1 or so_ngay > 5:
                print("❌ Vui lòng nhập từ 1 đến 5 ngày!")
                continue
            so_ngay_holder[0] = so_ngay
            break
        except ValueError:
            print("❌ Vui lòng nhập số!")

    # Ghi nhớ tháng/năm của link hiện tại
    sheet_month_holder[0] = ngay_bat_dau_obj.month
    sheet_year_holder[0] = ngay_bat_dau_obj.year

    # Tính danh sách ngày ban đầu
    danh_sach_ngay = get_danh_sach_ngay(ngay_bat_dau_obj, so_ngay_holder[0])

    print("\n" + "=" * 60)
    print(f"🎯 VÙNG QUẢN LÝ: {', '.join(danh_sach_ngay)}")
    print(f"📅 Link này quản lý tháng: {sheet_month_holder[0]}/{sheet_year_holder[0]}")
    print("💡 Gõ 'doi' + Enter bất cứ lúc nào để chuẩn bị link tháng mới!")
    print("=" * 60)

    # ================================================================
    # VÒNG 1: QUÉT BÙ
    # ================================================================
    print("\n🚀 [GIAI ĐOẠN 1]: ĐỒNG BỘ DỮ LIỆU CŨ...")

    for ngay_quet in danh_sach_ngay:
        ngay_obj = datetime.strptime(ngay_quet, "%Y-%m-%d")
        auto_sheet_name = f"{ngay_obj.day}.{ngay_obj.month}"

        create_sheet_if_not_exists(current_sheet_id_holder[0], auto_sheet_name)
        setup_header(current_sheet_id_holder[0], auto_sheet_name)

        emails_cu = get_emails_by_date(ngay_quet)

        for email in emails_cu:
            booking = parse_booking_email(email)
            code = booking.get("code", "").upper()
            if not code:
                continue

            is_canceled_mail = "cancel" in email.get("subject", "").lower()
            try:
                is_exist, current_status = check_booking_exists(code)
                if is_canceled_mail:
                    if is_exist and current_status != "CANCELLED":
                        update_booking_status(code, "CANCELLED")
                        update_sheet_booking_status(current_sheet_id_holder[0], auto_sheet_name, code, "HỦY VÉ")
                    elif not is_exist:
                        insert_booking(code, ngay_quet, status="CANCELLED")
                        append_booking(booking, current_sheet_id_holder[0], auto_sheet_name)
                        update_sheet_booking_status(current_sheet_id_holder[0], auto_sheet_name, code, "HỦY VÉ")
                else:
                    if not is_exist:
                        db_success = insert_booking(code, ngay_quet, status="BOOKED")
                        if db_success:
                            append_booking(booking, current_sheet_id_holder[0], auto_sheet_name)
            except Exception as e:
                print(f"⚠️ Lỗi quét bù [{code}]: {e}")

    print("\n✅ [ĐỒNG BỘ XONG] Chuyển sang chế độ Realtime!")

    # ================================================================
    # VÒNG 2: REALTIME
    # ================================================================
    t = threading.Thread(target=listen_for_commands, daemon=True)
    t.start()

    print("\n" + "=" * 60)
    print("🟢 [GIAI ĐOẠN 2]: REALTIME ĐANG HOẠT ĐỘNG...")
    print("💡 Gõ 'doi' + Enter để chuẩn bị link tháng mới!")
    print("=" * 60)

    while True:
        try:
            today = datetime.now()
            cleanup_old_data(days_to_keep=30)

            # BƯỚC 1: Áp dụng link mới nếu đã sang tháng
            ap_dung_link_moi_neu_co(today)

            # BƯỚC 2: Kiểm tra hết tháng mà chưa có link mới → DỪNG chờ
            if kiem_tra_het_thang(today, sheet_month_holder[0], sheet_year_holder[0]):
                if not new_sheet_pending[0]:
                    print("\n" + "🛑 " * 20)
                    print("🛑 ĐÃ SANG THÁNG MỚI NHƯNG CHƯA CÓ LINK SHEETS!")
                    print("👉 Gõ 'doi' + Enter để nhập link tháng mới và tiếp tục!")
                    print("🛑 " * 20)
                    time.sleep(60)
                    continue

            # BƯỚC 3: Cảnh báo nếu gần cuối tháng
            canh_bao_cuoi_thang(today, sheet_month_holder[0], sheet_year_holder[0])

            # BƯỚC 4: Tính lại danh sách ngày (cửa sổ trượt, không vượt cuối tháng)
            last_day = get_last_day_of_month(sheet_year_holder[0], sheet_month_holder[0])
            last_date_of_month = datetime(sheet_year_holder[0], sheet_month_holder[0], last_day)
            start_date = max(today, ngay_bat_dau_obj)

            danh_sach_ngay = []
            for i in range(so_ngay_holder[0]):
                d = start_date + timedelta(days=i)
                if d > last_date_of_month:
                    break
                danh_sach_ngay.append(d.strftime("%Y-%m-%d"))

            thoi_gian = today.strftime('%H:%M:%S')
            print(f"📡 [{thoi_gian}] Lắng nghe... Vùng: {', '.join(danh_sach_ngay)}")

            # BƯỚC 5: Tự tạo Tab mới nếu chưa có
            for ngay in danh_sach_ngay:
                ngay_obj = datetime.strptime(ngay, "%Y-%m-%d")
                tab_name = f"{ngay_obj.day}.{ngay_obj.month}"
                create_sheet_if_not_exists(current_sheet_id_holder[0], tab_name)
                setup_header(current_sheet_id_holder[0], tab_name)

            # BƯỚC 6: Xử lý email realtime
            realtime_emails = get_realtime_emails()
            for email in realtime_emails:
                booking = parse_booking_email(email)
                code = booking.get("code", "").upper()
                if not code:
                    continue

                ngay_di = booking.get("service_date", "").strip()
                if ngay_di not in danh_sach_ngay:
                    continue

                ngay_obj = datetime.strptime(ngay_di, "%Y-%m-%d")
                auto_sheet_name = f"{ngay_obj.day}.{ngay_obj.month}"
                is_canceled_mail = "cancel" in email.get("subject", "").lower()
                is_exist, current_status = check_booking_exists(code)

                if is_canceled_mail:
                    if is_exist and current_status != "CANCELLED":
                        update_booking_status(code, "CANCELLED")
                        update_sheet_booking_status(current_sheet_id_holder[0], auto_sheet_name, code, "HỦY VÉ")
                        print(f"🔴 [HỦY VÉ] [{code}] → Tab {auto_sheet_name}")
                    elif not is_exist:
                        insert_booking(code, ngay_di, status="CANCELLED")
                        append_booking(booking, current_sheet_id_holder[0], auto_sheet_name)
                        update_sheet_booking_status(current_sheet_id_holder[0], auto_sheet_name, code, "HỦY VÉ")
                        print(f"🔴 [HỦY SỚM] [{code}] → Tab {auto_sheet_name}")
                else:
                    if not is_exist:
                        db_success = insert_booking(code, ngay_di, status="BOOKED")
                        if db_success:
                            append_booking(booking, current_sheet_id_holder[0], auto_sheet_name)
                            print(f"✅ [ĐƠN MỚI] [{code}] ngày {ngay_di} → Tab {auto_sheet_name}")

            time.sleep(300)

        except KeyboardInterrupt:
            print("\n🛑 ĐÃ TẮT SERVER AN TOÀN.")
            break
        except Exception as e:
            print(f"⚠️ Lỗi: {e}. Thử lại sau 5 phút...")
            time.sleep(300)

if __name__ == "__main__":
    main()