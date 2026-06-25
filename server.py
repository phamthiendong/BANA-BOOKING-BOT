# import os
# import re
# import time
# import threading
# import calendar
# from datetime import datetime, timedelta
# from contextlib import asynccontextmanager

# from fastapi import FastAPI, BackgroundTasks, HTTPException
# from fastapi.middleware.cors import CORSMiddleware
# from pydantic import BaseModel

# from services.gmail_service import get_realtime_emails, get_google_service
# from services.gmail_service import extract_body_from_payload, get_header_value, build_gmail_link
# from services.booking_parser import parse_booking_email
# from services.sheet_service import (
#     setup_header,
#     append_booking,
#     create_sheet_if_not_exists,
#     update_sheet_booking_status,
# )
# from services.db_service import (
#     init_db,
#     cleanup_old_data,
#     check_booking_exists,
#     insert_booking,
#     update_booking_status,
# )

# # ================================================================
# # TRẠNG THÁI TOÀN CỤC (thay thế cho input() console)
# # ================================================================
# state = {
#     "sheet_id": None,
#     "sheet_month": None,
#     "sheet_year": None,
#     "new_sheet_pending": None,
#     "so_ngay": 5,
#     "ngay_bat_dau": None,       # datetime object
#     "dang_chay": False,          # Giai đoạn 2 đang chạy không
#     "dang_quet_bu": False,       # Giai đoạn 1 đang chạy không
#     "realtime_thread": None,
#     "dang_tam_dung": False,     # TÍNH NĂNG MỚI: Nút Tạm dừng
#     # Thống kê
#     "tong_moi": 0,
#     "tong_huy": 0,
#     "tong_bo_qua": 0,
#     "lan_quet_cuoi": None,
#     "log": [],                   # Log 100 dòng gần nhất
# }

# LOT_SIZE = 15
# NGHI_GIUA_LOT = 2
# NGHI_GIUA_NGAY = 3
# REALTIME_INTERVAL = 600  # 10 phút

# # ================================================================
# # HELPERS
# # ================================================================
# def add_log(msg: str):
#     ts = datetime.now().strftime("%H:%M:%S")
#     line = f"[{ts}] {msg}"
#     print(line)
#     state["log"].append(line)
#     if len(state["log"]) > 200:
#         state["log"] = state["log"][-200:]

# def extract_sheet_id(url_or_id: str) -> str:
#     match = re.search(r"/d/([a-zA-Z0-9-_]+)", url_or_id)
#     return match.group(1) if match else url_or_id.strip()

# def get_last_day_of_month(year, month):
#     return calendar.monthrange(year, month)[1]

# def get_danh_sach_ngay(ngay_bat_dau: datetime, so_ngay: int):
#     return [(ngay_bat_dau + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(so_ngay)]

# def tinh_danh_sach_ngay_realtime():
#     """Tính cửa sổ ngày trượt cho Giai đoạn 2."""
#     today = datetime.now()
#     so_ngay = state["so_ngay"]
#     sheet_month = state["sheet_month"]
#     sheet_year = state["sheet_year"]
#     ngay_bat_dau = state["ngay_bat_dau"]

#     last_day = get_last_day_of_month(sheet_year, sheet_month)
#     last_date_of_month = datetime(sheet_year, sheet_month, last_day)
#     start_date = max(today, ngay_bat_dau)

#     result = []
#     for i in range(so_ngay):
#         d = start_date + timedelta(days=i)
#         if d > last_date_of_month:
#             break
#         result.append(d.strftime("%Y-%m-%d"))
#     return result

# # ================================================================
# # XỬ LÝ 1 EMAIL
# # ================================================================

# def process_email(email, danh_sach_ngay, sheet_id, pha="realtime"):
#     """
#     Logic xử lý 1 email:

#     EMAIL CONFIRMED:
#       - Chưa có DB          → ghi DB (BOOKED)   + ghi Sheets          ✅ MỚI
#       - Đã có DB BOOKED     → bỏ qua                                   ⏭️
#       - Đã có DB CANCELLED  → cập nhật DB→BOOKED + cập nhật Sheets     🔄 PHỤC HỒI

#     EMAIL CANCELLED:
#       - Chưa có DB          → ghi DB (CANCELLED) + ghi Sheets + HỦY VÉ 🔴 HỦY SỚM
#       - Đã có DB CANCELLED  → bỏ qua                                    ⏭️
#       - Đã có DB BOOKED     → cập nhật DB→CANCELLED + cập nhật Sheets  🔴 HỦY VÉ

#     Trả về: "moi" | "huy" | "phuc_hoi" | "bo_qua" | "loi"
#     """
#     try:
#         booking = parse_booking_email(email)
#         code = booking.get("code", "").upper()
#         if not code:
#             return "loi"

#         ngay_di = booking.get("service_date", "").strip()
#         if ngay_di not in danh_sach_ngay:
#             return "bo_qua"

#         ngay_obj = datetime.strptime(ngay_di, "%Y-%m-%d")
#         tab = f"{ngay_obj.day}.{ngay_obj.month}"
#         is_cancel = "cancel" in email.get("subject", "").lower()

#         # Kiểm tra DB
#         is_exist, current_status = check_booking_exists(code)

#         # ============================================================
#         # TRƯỜNG HỢP: EMAIL CANCELLED
#         # ============================================================
#         if is_cancel:
#             if not is_exist:
#                 # Chưa có trong DB → ghi mới với trạng thái CANCELLED
#                 insert_booking(code, ngay_di, status="CANCELLED")
#                 append_booking(booking, sheet_id, tab)
#                 update_sheet_booking_status(sheet_id, tab, code, "HỦY VÉ")
#                 print(f"🔴 [{pha.upper()}][HỦY SỚM - CHƯA CÓ DB] [{code}] → Tab {tab}")
#                 return "huy"

#             elif current_status == "CANCELLED":
#                 # Đã CANCELLED rồi → bỏ qua
#                 print(f"⏭️  [{pha.upper()}][ĐÃ HỦY RỒI - BỎ QUA] [{code}]")
#                 return "bo_qua"

#             else:
#                 # Đang BOOKED → chuyển sang CANCELLED
#                 update_booking_status(code, "CANCELLED")
#                 update_sheet_booking_status(sheet_id, tab, code, "HỦY VÉ")
#                 print(f"🔴 [{pha.upper()}][HỦY VÉ - CẬP NHẬT] [{code}] BOOKED→CANCELLED → Tab {tab}")
#                 return "huy"

#         # ============================================================
#         # TRƯỜNG HỢP: EMAIL CONFIRMED
#         # ============================================================
#         else:
#             if not is_exist:
#                 # Chưa có trong DB → ghi mới BOOKED
#                 ok = insert_booking(code, ngay_di, status="BOOKED")
#                 if ok:
#                     append_booking(booking, sheet_id, tab)
#                     print(f"✅ [{pha.upper()}][ĐƠN MỚI] [{code}] ngày {ngay_di} → Tab {tab}")
#                     return "moi"
#                 return "loi"

#             elif current_status == "BOOKED":
#                 # Đã BOOKED rồi → bỏ qua
#                 print(f"⏭️  [{pha.upper()}][ĐÃ CÓ DB - BỎ QUA] [{code}]")
#                 return "bo_qua"

#             else:
#                 # Trước đó đã CANCELLED, giờ có CONFIRMED → phục hồi lại
#                 update_booking_status(code, "BOOKED")
#                 update_sheet_booking_status(sheet_id, tab, code, "ĐÃ ĐẶT LẠI")
#                 print(f"🔄 [{pha.upper()}][PHỤC HỒI] [{code}] CANCELLED→BOOKED → Tab {tab}")
#                 return "phuc_hoi"

#     except Exception as e:
#         print(f"⚠️ Lỗi process_email [{pha}]: {e}")
#         return "loi"


# # ================================================================
# # GIAI ĐOẠN 1 — QUÉT BÙ THEO LÔ (chạy trong background thread)
# # ================================================================
# def lay_danh_sach_id_email(target_date):
#     service = get_google_service("gmail", "v1")
#     query = (
#         f'from:operator@klook.com '
#         f'(subject:"Klook order confirmed" OR subject:"Klook order canceled") '
#         f'subject:(Fast Track) subject:({target_date})'
#     )
#     all_ids, next_page_token = [], None
#     while True:
#         result = service.users().messages().list(
#             userId="me", maxResults=500, q=query, pageToken=next_page_token
#         ).execute()
#         all_ids.extend(result.get("messages", []))
#         next_page_token = result.get("nextPageToken")
#         if not next_page_token:
#             break
#     return all_ids

# def tai_chi_tiet_mot_email(service, msg_id):
#     try:
#         msg_data = service.users().messages().get(userId="me", id=msg_id, format="full").execute()
#         payload = msg_data.get("payload", {})
#         headers = payload.get("headers", [])
#         mid = msg_data.get("id", "")
#         return {
#             "message_id": mid,
#             "thread_id": msg_data.get("threadId", ""),
#             "from": get_header_value(headers, "From"),
#             "subject": get_header_value(headers, "Subject"),
#             "date": get_header_value(headers, "Date"),
#             "snippet": msg_data.get("snippet", ""),
#             "body": extract_body_from_payload(payload),
#             "email_link": build_gmail_link(mid),
#         }
#     except Exception as e:
#         add_log(f"⚠️ Lỗi tải email {msg_id}: {e}")
#         return None

# def chay_quet_bu():
#     """Giai đoạn 1 — chạy trong background thread."""
#     state["dang_quet_bu"] = True
#     sheet_id = state["sheet_id"]
#     danh_sach_ngay = get_danh_sach_ngay(state["ngay_bat_dau"], state["so_ngay"])
#     tong_moi = tong_huy = tong_bo_qua = 0

#     add_log(f"🚀 [GIAI ĐOẠN 1] Bắt đầu quét bù {len(danh_sach_ngay)} ngày...")

#     for idx, ngay in enumerate(danh_sach_ngay, 1):
#         ngay_obj = datetime.strptime(ngay, "%Y-%m-%d")
#         tab = f"{ngay_obj.day}.{ngay_obj.month}"
#         add_log(f"📅 [{idx}/{len(danh_sach_ngay)}] Ngày {ngay} → Tab '{tab}'")

#         create_sheet_if_not_exists(sheet_id, tab)
#         setup_header(sheet_id, tab)

#         ids = lay_danh_sach_id_email(ngay)
#         add_log(f"  🔍 Tìm thấy {len(ids)} email.")
#         if not ids:
#             continue

#         service = get_google_service("gmail", "v1")
#         tong_ids = len(ids)
#         so_lot = (tong_ids + LOT_SIZE - 1) // LOT_SIZE

#         for so_lot_idx, start in enumerate(range(0, tong_ids, LOT_SIZE), 1):
#             lot = ids[start: start + LOT_SIZE]
#             add_log(f"  📦 Lô {so_lot_idx}/{so_lot} ({len(lot)} email)...")
#             dm = dh = db = 0
#             for msg in lot:
#                 email = tai_chi_tiet_mot_email(service, msg["id"])
#                 if not email:
#                     db += 1
#                     continue
#                 r = process_email(email, [ngay], sheet_id, "quet_bu")
#                 if r == "moi": dm += 1
#                 elif r == "huy": dh += 1
#                 else: db += 1
#             add_log(f"    ✅ Lô xong: Mới={dm} | Hủy={dh} | Bỏ qua={db}")
#             tong_moi += dm; tong_huy += dh; tong_bo_qua += db
#             if start + LOT_SIZE < tong_ids:
#                 time.sleep(NGHI_GIUA_LOT)

#         if idx < len(danh_sach_ngay):
#             time.sleep(NGHI_GIUA_NGAY)

#     state["tong_moi"] += tong_moi
#     state["tong_huy"] += tong_huy
#     state["tong_bo_qua"] += tong_bo_qua
#     state["dang_quet_bu"] = False
#     add_log(f"✅ [GIAI ĐOẠN 1 XONG] Mới={tong_moi} | Hủy={tong_huy} | Bỏ qua={tong_bo_qua}")

#     # Tự động chuyển sang Giai đoạn 2 sau khi quét bù xong
#     chay_realtime_loop()

# # ================================================================
# # GIAI ĐOẠN 2 — REALTIME LOOP (chạy mãi trong background thread)
# # ================================================================
# def chay_realtime_loop():
#     state["dang_chay"] = True
#     add_log("🟢 [GIAI ĐOẠN 2] Realtime bắt đầu (10 phút/lần)...")

#     while state["dang_chay"]:

#         # THÊM ĐOẠN NÀY VÀO ĐẦU VÒNG LẶP:
#         if state.get("dang_tam_dung", False):
#             # Nếu đang bị tạm dừng, ngủ 10 giây rồi quay lại check tiếp
#             time.sleep(1000)
#             continue

#         try:
#             today = datetime.now()
#             cleanup_old_data(days_to_keep=30)

#             # Áp dụng link tháng mới nếu đến tháng
#             pending = state["new_sheet_pending"]
#             if pending and today.month == pending["month"] and today.year == pending["year"]:
#                 state["sheet_id"] = pending["sheet_id"]
#                 state["sheet_month"] = pending["month"]
#                 state["sheet_year"] = pending["year"]
#                 state["new_sheet_pending"] = None
#                 add_log(f"🎉 Chuyển sang tháng {pending['month']}/{pending['year']} thành công!")

#             # Kiểm tra hết tháng
#             if today.month != state["sheet_month"] or today.year != state["sheet_year"]:
#                 add_log("🛑 Đã sang tháng mới! Cần cập nhật link Sheets qua API /config/sheet-moi")
#                 time.sleep(60)
#                 continue

#             danh_sach_ngay = tinh_danh_sach_ngay_realtime()
#             sheet_id = state["sheet_id"]

#             # Tạo tab nếu chưa có
#             for ngay in danh_sach_ngay:
#                 ngay_obj = datetime.strptime(ngay, "%Y-%m-%d")
#                 tab = f"{ngay_obj.day}.{ngay_obj.month}"
#                 create_sheet_if_not_exists(sheet_id, tab)
#                 setup_header(sheet_id, tab)

#             add_log(f"📡 Quét realtime... Vùng: {', '.join(danh_sach_ngay)}")
#             emails = get_realtime_emails()
#             add_log(f"  📧 {len(emails)} email mới trong 10 phút qua.")

#             dm = dh = db = 0
#             for email in emails:
#                 r = process_email(email, danh_sach_ngay, sheet_id, "realtime")
#                 if r == "moi": dm += 1
#                 elif r == "huy": dh += 1
#                 else: db += 1

#             state["tong_moi"] += dm
#             state["tong_huy"] += dh
#             state["tong_bo_qua"] += db
#             state["lan_quet_cuoi"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
#             add_log(f"  ✔️  Kết quả: Mới={dm} | Hủy={dh} | Bỏ qua={db}")
#             add_log(f"  ⏱️  Chờ 10 phút...")

#             time.sleep(REALTIME_INTERVAL)

#         except Exception as e:
#             add_log(f"⚠️ Lỗi realtime: {e}. Thử lại sau 10 phút...")
#             time.sleep(REALTIME_INTERVAL)

# # ================================================================
# # FASTAPI APP
# # ================================================================
# @asynccontextmanager
# async def lifespan(app: FastAPI):
#     init_db()
#     add_log("✅ Database khởi tạo xong. Sẵn sàng nhận lệnh qua API.")
#     yield
#     state["dang_chay"] = False
#     add_log("🛑 Server tắt an toàn.")

# app = FastAPI(
#     title="BANA Booking Bot API",
#     description="API quản lý quét Gmail Klook & ghi Google Sheets",
#     version="2.0.0",
#     lifespan=lifespan,
# )

# app.add_middleware(
#     CORSMiddleware,
#     allow_origins=["*"],
#     allow_methods=["*"],
#     allow_headers=["*"],
# )

# # ================================================================
# # SCHEMAS
# # ================================================================
# class KhoiDongBody(BaseModel):
#     sheet_link: str
#     ngay_bat_dau: str   # YYYY-MM-DD
#     so_ngay: int = 5    # 1-5

# class SheetMoiBody(BaseModel):
#     sheet_link: str

# # ================================================================
# # ENDPOINTS
# # ================================================================

# @app.get("/", summary="Kiểm tra server sống")
# def root():
#     return {"status": "ok", "message": "BANA Booking Bot đang chạy 🤖"}


# @app.post("/khoi-dong", summary="Khởi động bot (Giai đoạn 1 + 2)")
# def khoi_dong(body: KhoiDongBody, background_tasks: BackgroundTasks):
#     """
#     Khởi động toàn bộ bot:
#     1. Nhận link Sheets, ngày bắt đầu, số ngày quét
#     2. Chạy Giai đoạn 1 (quét bù) trong background
#     3. Tự động chuyển sang Giai đoạn 2 (realtime 10 phút) sau khi xong
#     """
#     if state["dang_chay"] or state["dang_quet_bu"]:
#         raise HTTPException(status_code=400, detail="Bot đang chạy rồi! Dùng /dung để tắt trước.")

#     try:
#         ngay_bat_dau_obj = datetime.strptime(body.ngay_bat_dau.replace("/", "-"), "%Y-%m-%d")
#     except ValueError:
#         raise HTTPException(status_code=400, detail="Sai định dạng ngày! Dùng YYYY-MM-DD")

#     if not 1 <= body.so_ngay <= 5:
#         raise HTTPException(status_code=400, detail="so_ngay phải từ 1 đến 5")

#     state["sheet_id"] = extract_sheet_id(body.sheet_link)
#     state["sheet_month"] = ngay_bat_dau_obj.month
#     state["sheet_year"] = ngay_bat_dau_obj.year
#     state["ngay_bat_dau"] = ngay_bat_dau_obj
#     state["so_ngay"] = body.so_ngay
#     state["tong_moi"] = state["tong_huy"] = state["tong_bo_qua"] = 0
#     state["log"] = []

#     t = threading.Thread(target=chay_quet_bu, daemon=True)
#     t.start()
#     state["realtime_thread"] = t

#     return {
#         "status": "started",
#         "sheet_id": state["sheet_id"],
#         "ngay_bat_dau": body.ngay_bat_dau,
#         "so_ngay": body.so_ngay,
#         "message": "Giai đoạn 1 (quét bù) đang chạy. Tự chuyển sang Realtime khi xong."
#     }


# @app.post("/dung", summary="Dừng bot Giai đoạn 2 (Realtime)")
# def dung_bot():
#     if not state["dang_chay"]:
#         raise HTTPException(status_code=400, detail="Bot chưa chạy.")
#     state["dang_chay"] = False
#     return {"status": "stopped", "message": "Đã gửi lệnh dừng. Bot sẽ dừng sau chu kỳ hiện tại."}


# @app.get("/trang-thai", summary="Xem trạng thái hiện tại của bot")
# def trang_thai():
#     return {
#         "dang_quet_bu": state["dang_quet_bu"],
#         "dang_chay_realtime": state["dang_chay"],
#         "sheet_id": state["sheet_id"],
#         "sheet_thang_nam": f"{state['sheet_month']}/{state['sheet_year']}" if state["sheet_month"] else None,
#         "ngay_bat_dau": state["ngay_bat_dau"].strftime("%Y-%m-%d") if state["ngay_bat_dau"] else None,
#         "so_ngay": state["so_ngay"],
#         "thong_ke": {
#             "tong_don_moi": state["tong_moi"],
#             "tong_huy": state["tong_huy"],
#             "tong_bo_qua": state["tong_bo_qua"],
#         },
#         "lan_quet_cuoi": state["lan_quet_cuoi"],
#         "link_sheets_moi_cho": state["new_sheet_pending"]["sheet_id"] if state["new_sheet_pending"] else None,
#     }


# @app.get("/log", summary="Xem 100 dòng log gần nhất")
# def xem_log(n: int = 100):
#     return {"log": state["log"][-n:]}


# @app.post("/config/sheet-moi", summary="Cập nhật link Sheets tháng mới")
# def cap_nhat_sheet_moi(body: SheetMoiBody):
#     """
#     Đăng ký link Sheets cho tháng tiếp theo.
#     Bot sẽ tự động chuyển sang link này khi sang tháng mới.
#     """
#     if not state["sheet_month"]:
#         raise HTTPException(status_code=400, detail="Bot chưa khởi động.")

#     cur_month = state["sheet_month"]
#     cur_year = state["sheet_year"]
#     next_month = 1 if cur_month == 12 else cur_month + 1
#     next_year = cur_year + 1 if cur_month == 12 else cur_year

#     new_id = extract_sheet_id(body.sheet_link)
#     if new_id == state["sheet_id"]:
#         raise HTTPException(status_code=400, detail="Đây là link cũ! Nhập link của tháng mới.")

#     state["new_sheet_pending"] = {
#         "sheet_id": new_id,
#         "month": next_month,
#         "year": next_year,
#     }
#     add_log(f"📋 Đã đăng ký link Sheets mới cho tháng {next_month}/{next_year}")
#     return {
#         "status": "ok",
#         "message": f"Link đã lưu. Sẽ kích hoạt tự động khi sang tháng {next_month}/{next_year}.",
#         "sheet_id_moi": new_id,
#     }


# @app.post("/quet-ngay", summary="Quét thủ công 1 ngày cụ thể")
# def quet_thu_cong(ngay: str, background_tasks: BackgroundTasks):
#     """
#     Quét bù thủ công cho 1 ngày bất kỳ (YYYY-MM-DD).
#     Hữu ích khi muốn đồng bộ lại 1 ngày bị thiếu.
#     """
#     if not state["sheet_id"]:
#         raise HTTPException(status_code=400, detail="Bot chưa được khởi động. Gọi /khoi-dong trước.")
#     try:
#         datetime.strptime(ngay, "%Y-%m-%d")
#     except ValueError:
#         raise HTTPException(status_code=400, detail="Sai định dạng ngày! Dùng YYYY-MM-DD")

#     def _quet():
#         sheet_id = state["sheet_id"]
#         ngay_obj = datetime.strptime(ngay, "%Y-%m-%d")
#         tab = f"{ngay_obj.day}.{ngay_obj.month}"
#         create_sheet_if_not_exists(sheet_id, tab)
#         setup_header(sheet_id, tab)
#         ids = lay_danh_sach_id_email(ngay)
#         add_log(f"🔧 [QUÉT THỦ CÔNG] Ngày {ngay}: {len(ids)} email")
#         if not ids:
#             return
#         service = get_google_service("gmail", "v1")
#         for msg in ids:
#             email = tai_chi_tiet_mot_email(service, msg["id"])
#             if email:
#                 process_email(email, [ngay], sheet_id, "thu_cong")

#     background_tasks.add_task(_quet)
#     return {"status": "queued", "ngay": ngay, "message": f"Đang quét ngày {ngay} trong nền..."}

# @app.post("/tam-dung", summary="Tạm dừng bot tạm thời")
# def tam_dung():
#     """Tạm ngưng quét email nhưng không tắt hẳn server."""
#     if not state["dang_chay"]:
#         raise HTTPException(status_code=400, detail="Bot chưa khởi động.")
#     if state.get("dang_tam_dung"):
#         return {"status": "already_paused", "message": "Bot ĐÃ đang trong trạng thái tạm dừng."}
    
#     state["dang_tam_dung"] = True
#     add_log("⏸️ Đã nhận lệnh TẠM DỪNG. Bot đang ngủ đông chờ lệnh tiếp tục...")
#     return {"status": "paused", "message": "Đã tạm dừng bot thành công."}


# @app.post("/tiep-tuc", summary="Tiếp tục chạy bot")
# def tiep_tuc():
#     """Đánh thức bot dậy sau khi tạm dừng."""
#     if not state["dang_chay"]:
#         raise HTTPException(status_code=400, detail="Bot đã bị tắt hoàn toàn. Vui lòng dùng /khoi-dong.")
#     if not state.get("dang_tam_dung"):
#         return {"status": "already_running", "message": "Bot đang chạy bình thường, không bị tạm dừng."}
    
#     state["dang_tam_dung"] = False
#     add_log("▶️ Đã nhận lệnh TIẾP TỤC. Bot bắt đầu làm việc lại.")
#     return {"status": "resumed", "message": "Bot đã thức dậy và tiếp tục quét."}







import os
import re
import time
import calendar
from datetime import datetime, timedelta
from contextlib import asynccontextmanager

from fastapi import FastAPI, BackgroundTasks, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from apscheduler.schedulers.background import BackgroundScheduler

from services.gmail_service import get_realtime_emails, get_google_service
from services.gmail_service import extract_body_from_payload, get_header_value, build_gmail_link
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
# TRẠNG THÁI TOÀN CỤC
# ================================================================
state = {
    "sheet_id": None,
    "sheet_month": None,
    "sheet_year": None,
    "new_sheet_pending": None,
    "so_ngay": 5,
    "ngay_bat_dau": None,
    "dang_quet_bu": False,       # Giai đoạn 1 đang chạy không
    # Thống kê
    "tong_moi": 0,
    "tong_huy": 0,
    "tong_bo_qua": 0,
    "lan_quet_cuoi": None,
    "log": [],
}

LOT_SIZE = 15
NGHI_GIUA_LOT = 2
NGHI_GIUA_NGAY = 3
REALTIME_INTERVAL_MINUTES = 10  # APScheduler dùng đơn vị phút

# ================================================================
# APSCHEDULER — Quản lý Giai đoạn 2
# ================================================================
# Mỗi job chạy trong thread riêng, tối đa 1 job realtime cùng lúc
scheduler = BackgroundScheduler(
    job_defaults={"coalesce": True, "max_instances": 1},
)
REALTIME_JOB_ID = "realtime_scan"

# ================================================================
# HELPERS
# ================================================================
def add_log(msg: str):
    ts = datetime.now().strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line)
    state["log"].append(line)
    if len(state["log"]) > 200:
        state["log"] = state["log"][-200:]

def extract_sheet_id(url_or_id: str) -> str:
    match = re.search(r"/d/([a-zA-Z0-9-_]+)", url_or_id)
    return match.group(1) if match else url_or_id.strip()

def get_last_day_of_month(year, month):
    return calendar.monthrange(year, month)[1]

def get_danh_sach_ngay(ngay_bat_dau: datetime, so_ngay: int):
    return [(ngay_bat_dau + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(so_ngay)]

def tinh_danh_sach_ngay_realtime():
    """Tính cửa sổ ngày trượt cho Giai đoạn 2."""
    today = datetime.now()
    so_ngay = state["so_ngay"]
    sheet_month = state["sheet_month"]
    sheet_year = state["sheet_year"]
    ngay_bat_dau = state["ngay_bat_dau"]

    last_day = get_last_day_of_month(sheet_year, sheet_month)
    last_date_of_month = datetime(sheet_year, sheet_month, last_day)
    start_date = max(today, ngay_bat_dau)

    result = []
    for i in range(so_ngay):
        d = start_date + timedelta(days=i)
        if d > last_date_of_month:
            break
        result.append(d.strftime("%Y-%m-%d"))
    return result

def is_realtime_running() -> bool:
    """Kiểm tra job realtime có đang được lên lịch không."""
    job = scheduler.get_job(REALTIME_JOB_ID)
    return job is not None

# ================================================================
# XỬ LÝ 1 EMAIL
# ================================================================
def process_email(email, danh_sach_ngay, sheet_id, pha="realtime"):
    """
    EMAIL CONFIRMED:
      - Chưa có DB          → ghi DB (BOOKED)   + ghi Sheets          ✅ MỚI
      - Đã có DB BOOKED     → bỏ qua                                   ⏭️
      - Đã có DB CANCELLED  → cập nhật DB→BOOKED + cập nhật Sheets     🔄 PHỤC HỒI

    EMAIL CANCELLED:
      - Chưa có DB          → ghi DB (CANCELLED) + ghi Sheets + HỦY VÉ 🔴 HỦY SỚM
      - Đã có DB CANCELLED  → bỏ qua                                    ⏭️
      - Đã có DB BOOKED     → cập nhật DB→CANCELLED + cập nhật Sheets  🔴 HỦY VÉ

    Trả về: "moi" | "huy" | "phuc_hoi" | "bo_qua" | "loi"
    """
    try:
        booking = parse_booking_email(email)
        code = booking.get("code", "").upper()
        if not code:
            return "loi"

        ngay_di = booking.get("service_date", "").strip()
        if ngay_di not in danh_sach_ngay:
            return "bo_qua"

        ngay_obj = datetime.strptime(ngay_di, "%Y-%m-%d")
        tab = f"{ngay_obj.day}.{ngay_obj.month}"
        is_cancel = "cancel" in email.get("subject", "").lower()

        is_exist, current_status = check_booking_exists(code)

        if is_cancel:
            if not is_exist:
                insert_booking(code, ngay_di, status="CANCELLED")
                append_booking(booking, sheet_id, tab)
                update_sheet_booking_status(sheet_id, tab, code, "HỦY VÉ")
                add_log(f"🔴 [{pha.upper()}][HỦY SỚM] [{code}] → Tab {tab}")
                return "huy"
            elif current_status == "CANCELLED":
                return "bo_qua"
            else:
                update_booking_status(code, "CANCELLED")
                update_sheet_booking_status(sheet_id, tab, code, "HỦY VÉ")
                add_log(f"🔴 [{pha.upper()}][HỦY VÉ] [{code}] BOOKED→CANCELLED → Tab {tab}")
                return "huy"
        else:
            if not is_exist:
                ok = insert_booking(code, ngay_di, status="BOOKED")
                if ok:
                    append_booking(booking, sheet_id, tab)
                    add_log(f"✅ [{pha.upper()}][ĐƠN MỚI] [{code}] ngày {ngay_di} → Tab {tab}")
                    return "moi"
                return "loi"
            elif current_status == "BOOKED":
                return "bo_qua"
            else:
                update_booking_status(code, "BOOKED")
                update_sheet_booking_status(sheet_id, tab, code, "ĐÃ ĐẶT LẠI")
                add_log(f"🔄 [{pha.upper()}][PHỤC HỒI] [{code}] CANCELLED→BOOKED → Tab {tab}")
                return "phuc_hoi"

    except Exception as e:
        add_log(f"⚠️ Lỗi process_email [{pha}]: {e}")
        return "loi"


# ================================================================
# GIAI ĐOẠN 1 — QUÉT BÙ (chạy 1 lần trong background thread)
# ================================================================
def lay_danh_sach_id_email(target_date):
    service = get_google_service("gmail", "v1")
    query = (
        f'from:operator@klook.com '
        f'(subject:"Klook order confirmed" OR subject:"Klook order canceled") '
        f'subject:(Fast Track) subject:({target_date})'
    )
    all_ids, next_page_token = [], None
    while True:
        result = service.users().messages().list(
            userId="me", maxResults=500, q=query, pageToken=next_page_token
        ).execute()
        all_ids.extend(result.get("messages", []))
        next_page_token = result.get("nextPageToken")
        if not next_page_token:
            break
    return all_ids

def tai_chi_tiet_mot_email(service, msg_id):
    try:
        msg_data = service.users().messages().get(userId="me", id=msg_id, format="full").execute()
        payload = msg_data.get("payload", {})
        headers = payload.get("headers", [])
        mid = msg_data.get("id", "")
        return {
            "message_id": mid,
            "thread_id": msg_data.get("threadId", ""),
            "from": get_header_value(headers, "From"),
            "subject": get_header_value(headers, "Subject"),
            "date": get_header_value(headers, "Date"),
            "snippet": msg_data.get("snippet", ""),
            "body": extract_body_from_payload(payload),
            "email_link": build_gmail_link(mid),
        }
    except Exception as e:
        add_log(f"⚠️ Lỗi tải email {msg_id}: {e}")
        return None

def chay_quet_bu():
    """Giai đoạn 1 — chạy 1 lần duy nhất khi gọi /quet-bu."""
    state["dang_quet_bu"] = True
    sheet_id = state["sheet_id"]
    danh_sach_ngay = get_danh_sach_ngay(state["ngay_bat_dau"], state["so_ngay"])
    tong_moi = tong_huy = tong_bo_qua = 0

    add_log(f"🚀 [GIAI ĐOẠN 1] Bắt đầu quét bù {len(danh_sach_ngay)} ngày...")

    for idx, ngay in enumerate(danh_sach_ngay, 1):
        ngay_obj = datetime.strptime(ngay, "%Y-%m-%d")
        tab = f"{ngay_obj.day}.{ngay_obj.month}"
        add_log(f"📅 [{idx}/{len(danh_sach_ngay)}] Ngày {ngay} → Tab '{tab}'")

        create_sheet_if_not_exists(sheet_id, tab)
        setup_header(sheet_id, tab)

        ids = lay_danh_sach_id_email(ngay)
        add_log(f"  🔍 Tìm thấy {len(ids)} email.")
        if not ids:
            continue

        service = get_google_service("gmail", "v1")
        tong_ids = len(ids)
        so_lot = (tong_ids + LOT_SIZE - 1) // LOT_SIZE

        for so_lot_idx, start in enumerate(range(0, tong_ids, LOT_SIZE), 1):
            lot = ids[start: start + LOT_SIZE]
            add_log(f"  📦 Lô {so_lot_idx}/{so_lot} ({len(lot)} email)...")
            dm = dh = db = 0
            for msg in lot:
                email = tai_chi_tiet_mot_email(service, msg["id"])
                if not email:
                    db += 1
                    continue
                r = process_email(email, [ngay], sheet_id, "quet_bu")
                if r == "moi": dm += 1
                elif r == "huy": dh += 1
                else: db += 1
            add_log(f"    ✅ Lô xong: Mới={dm} | Hủy={dh} | Bỏ qua={db}")
            tong_moi += dm; tong_huy += dh; tong_bo_qua += db
            if start + LOT_SIZE < tong_ids:
                time.sleep(NGHI_GIUA_LOT)

        if idx < len(danh_sach_ngay):
            time.sleep(NGHI_GIUA_NGAY)

    state["tong_moi"] += tong_moi
    state["tong_huy"] += tong_huy
    state["tong_bo_qua"] += tong_bo_qua
    state["dang_quet_bu"] = False
    add_log(f"✅ [GIAI ĐOẠN 1 XONG] Mới={tong_moi} | Hủy={tong_huy} | Bỏ qua={tong_bo_qua}")
    add_log("💡 Gợi ý: Gọi POST /bat-dau-realtime để bắt đầu Giai đoạn 2.")


# ================================================================
# GIAI ĐOẠN 2 — REALTIME JOB (APScheduler gọi mỗi 10 phút)
# ================================================================
def realtime_job():
    """
    Hàm này được APScheduler gọi tự động mỗi 10 phút.
    Không cần vòng lặp while, không cần sleep — scheduler lo hết.
    """
    try:
        today = datetime.now()
        cleanup_old_data(days_to_keep=30)

        # Áp dụng link tháng mới nếu đến tháng
        pending = state["new_sheet_pending"]
        if pending and today.month == pending["month"] and today.year == pending["year"]:
            state["sheet_id"] = pending["sheet_id"]
            state["sheet_month"] = pending["month"]
            state["sheet_year"] = pending["year"]
            state["new_sheet_pending"] = None
            add_log(f"🎉 Chuyển sang tháng {pending['month']}/{pending['year']} thành công!")

        # Kiểm tra hết tháng → tự dừng job, chờ cấu hình lại
        if today.month != state["sheet_month"] or today.year != state["sheet_year"]:
            add_log("🛑 Đã sang tháng mới! Tự dừng realtime. Gọi /config/sheet-moi rồi /bat-dau-realtime lại.")
            scheduler.remove_job(REALTIME_JOB_ID)
            return

        danh_sach_ngay = tinh_danh_sach_ngay_realtime()
        sheet_id = state["sheet_id"]

        for ngay in danh_sach_ngay:
            ngay_obj = datetime.strptime(ngay, "%Y-%m-%d")
            tab = f"{ngay_obj.day}.{ngay_obj.month}"
            create_sheet_if_not_exists(sheet_id, tab)
            setup_header(sheet_id, tab)

        add_log(f"📡 [REALTIME] Quét vùng: {', '.join(danh_sach_ngay)}")
        emails = get_realtime_emails()
        add_log(f"  📧 {len(emails)} email mới trong 10 phút qua.")

        dm = dh = db = 0
        for email in emails:
            r = process_email(email, danh_sach_ngay, sheet_id, "realtime")
            if r == "moi": dm += 1
            elif r == "huy": dh += 1
            else: db += 1

        state["tong_moi"] += dm
        state["tong_huy"] += dh
        state["tong_bo_qua"] += db
        state["lan_quet_cuoi"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        add_log(f"  ✔️  Kết quả: Mới={dm} | Hủy={dh} | Bỏ qua={db}")

    except Exception as e:
        add_log(f"⚠️ [REALTIME] Lỗi: {e}. APScheduler sẽ tự thử lại lần sau.")


# ================================================================
# FASTAPI APP
# ================================================================
@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    scheduler.start()
    add_log("✅ Database & Scheduler khởi tạo xong. Sẵn sàng nhận lệnh.")
    yield
    scheduler.shutdown(wait=False)
    add_log("🛑 Server tắt an toàn.")

app = FastAPI(
    title="BANA Booking Bot API",
    description="API quản lý quét Gmail Klook & ghi Google Sheets",
    version="3.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ================================================================
# SCHEMAS
# ================================================================
class CauHinhBody(BaseModel):
    sheet_link: str
    ngay_bat_dau: str   # YYYY-MM-DD
    so_ngay: int = 5    # 1-5

class SheetMoiBody(BaseModel):
    sheet_link: str

# ================================================================
# ENDPOINTS
# ================================================================

@app.get("/", summary="Kiểm tra server sống")
def root():
    return {"status": "ok", "message": "BANA Booking Bot v3 đang chạy 🤖"}


# ----------------------------------------------------------------
# BƯỚC 0: Cấu hình chung (sheet + ngày) — gọi trước mọi thứ
# ----------------------------------------------------------------
@app.post("/cau-hinh", summary="Cấu hình sheet và ngày bắt đầu")
def cau_hinh(body: CauHinhBody):
    """
    Lưu cấu hình sheet + ngày. Phải gọi trước /quet-bu và /bat-dau-realtime.
    Không khởi động gì cả, chỉ lưu config.
    """
    if state["dang_quet_bu"]:
        raise HTTPException(status_code=400, detail="Đang quét bù! Chờ xong rồi cấu hình lại.")
    if is_realtime_running():
        raise HTTPException(status_code=400, detail="Realtime đang chạy! Gọi /dung-realtime trước.")

    try:
        ngay_bat_dau_obj = datetime.strptime(body.ngay_bat_dau.replace("/", "-"), "%Y-%m-%d")
    except ValueError:
        raise HTTPException(status_code=400, detail="Sai định dạng ngày! Dùng YYYY-MM-DD")

    if not 1 <= body.so_ngay <= 5:
        raise HTTPException(status_code=400, detail="so_ngay phải từ 1 đến 5")

    state["sheet_id"] = extract_sheet_id(body.sheet_link)
    state["sheet_month"] = ngay_bat_dau_obj.month
    state["sheet_year"] = ngay_bat_dau_obj.year
    state["ngay_bat_dau"] = ngay_bat_dau_obj
    state["so_ngay"] = body.so_ngay
    state["tong_moi"] = state["tong_huy"] = state["tong_bo_qua"] = 0
    state["log"] = []

    add_log(f"⚙️ Đã cấu hình: sheet={state['sheet_id']} | ngày={body.ngay_bat_dau} | so_ngay={body.so_ngay}")
    return {
        "status": "configured",
        "sheet_id": state["sheet_id"],
        "ngay_bat_dau": body.ngay_bat_dau,
        "so_ngay": body.so_ngay,
        "buoc_tiep_theo": "Gọi POST /quet-bu để quét bù, hoặc POST /bat-dau-realtime để bắt đầu realtime ngay."
    }


# ----------------------------------------------------------------
# BƯỚC 1: Giai đoạn 1 — Quét bù (chạy 1 lần)
# ----------------------------------------------------------------
@app.post("/quet-bu", summary="[Giai đoạn 1] Quét bù email lịch sử")
def bat_dau_quet_bu(background_tasks: BackgroundTasks):
    """
    Chạy quét bù 1 lần duy nhất dựa trên cấu hình đã lưu.
    Không tự động chuyển sang Giai đoạn 2 — bạn tự quyết định.
    """
    if not state["sheet_id"]:
        raise HTTPException(status_code=400, detail="Chưa cấu hình! Gọi POST /cau-hinh trước.")
    if state["dang_quet_bu"]:
        raise HTTPException(status_code=400, detail="Đang quét bù rồi!")

    background_tasks.add_task(chay_quet_bu)
    return {
        "status": "started",
        "message": "Giai đoạn 1 đang chạy trong nền.",
        "buoc_tiep_theo": "Theo dõi qua GET /trang-thai. Khi xong, gọi POST /bat-dau-realtime."
    }


# ----------------------------------------------------------------
# BƯỚC 2: Giai đoạn 2 — Realtime (APScheduler)
# ----------------------------------------------------------------
@app.post("/bat-dau-realtime", summary="[Giai đoạn 2] Bắt đầu quét realtime mỗi 10 phút")
def bat_dau_realtime():
    """
    Đăng ký job realtime với APScheduler (chạy mỗi 10 phút).
    Có thể gọi độc lập, không cần chạy /quet-bu trước.
    """
    if not state["sheet_id"]:
        raise HTTPException(status_code=400, detail="Chưa cấu hình! Gọi POST /cau-hinh trước.")
    if state["dang_quet_bu"]:
        raise HTTPException(status_code=400, detail="Đang quét bù! Chờ xong rồi bắt đầu realtime.")
    if is_realtime_running():
        raise HTTPException(status_code=400, detail="Realtime đang chạy rồi!")

    # Thêm job interval, chạy ngay lần đầu (next_run_time=now)
    scheduler.add_job(
        realtime_job,
        trigger="interval",
        minutes=REALTIME_INTERVAL_MINUTES,
        id=REALTIME_JOB_ID,
        next_run_time=datetime.now(),   # Chạy ngay lập tức lần đầu
        replace_existing=True,
    )
    add_log("🟢 [GIAI ĐOẠN 2] Realtime job đã được đăng ký (mỗi 10 phút).")
    return {
        "status": "started",
        "interval_minutes": REALTIME_INTERVAL_MINUTES,
        "message": "Realtime đang chạy. Gọi POST /dung-realtime để dừng."
    }


@app.post("/dung-realtime", summary="[Giai đoạn 2] Dừng realtime ngay lập tức")
def dung_realtime():
    """
    Dừng job realtime ngay — không cần chờ hết chu kỳ 10 phút.
    Sau đó có thể gọi /bat-dau-realtime lại bất kỳ lúc nào.
    """
    if not is_realtime_running():
        raise HTTPException(status_code=400, detail="Realtime chưa chạy.")

    scheduler.remove_job(REALTIME_JOB_ID)
    add_log("🛑 Realtime job đã dừng.")
    return {
        "status": "stopped",
        "message": "Đã dừng realtime ngay lập tức. Gọi /bat-dau-realtime để chạy lại."
    }


@app.post("/tam-dung-realtime", summary="Tạm dừng realtime (giữ lịch)")
def tam_dung_realtime():
    """
    Tạm dừng job — APScheduler giữ lịch nhưng không thực thi.
    Dùng /tiep-tuc-realtime để đánh thức lại.
    """
    job = scheduler.get_job(REALTIME_JOB_ID)
    if not job:
        raise HTTPException(status_code=400, detail="Realtime chưa chạy.")

    scheduler.pause_job(REALTIME_JOB_ID)
    add_log("⏸️ Realtime job đã tạm dừng.")
    return {"status": "paused", "message": "Đã tạm dừng. Gọi /tiep-tuc-realtime để tiếp tục."}


@app.post("/tiep-tuc-realtime", summary="Tiếp tục realtime sau tạm dừng")
def tiep_tuc_realtime():
    """Đánh thức job đã bị pause — tiếp tục theo lịch cũ."""
    job = scheduler.get_job(REALTIME_JOB_ID)
    if not job:
        raise HTTPException(status_code=400, detail="Realtime chưa được đăng ký. Gọi /bat-dau-realtime.")

    scheduler.resume_job(REALTIME_JOB_ID)
    add_log("▶️ Realtime job đã tiếp tục.")
    return {"status": "resumed", "message": "Bot đã tiếp tục quét."}


# ----------------------------------------------------------------
# TIỆN ÍCH
# ----------------------------------------------------------------
@app.get("/trang-thai", summary="Xem trạng thái hiện tại")
def trang_thai():
    job = scheduler.get_job(REALTIME_JOB_ID)
    realtime_status = "stopped"
    next_run = None
    if job:
        realtime_status = "paused" if job.next_run_time is None else "running"
        next_run = job.next_run_time.strftime("%Y-%m-%d %H:%M:%S") if job.next_run_time else None

    return {
        "giai_doan_1": {
            "dang_quet_bu": state["dang_quet_bu"],
        },
        "giai_doan_2": {
            "trang_thai": realtime_status,          # "running" | "paused" | "stopped"
            "lan_chay_tiep_theo": next_run,
            "lan_quet_cuoi": state["lan_quet_cuoi"],
        },
        "cau_hinh": {
            "sheet_id": state["sheet_id"],
            "sheet_thang_nam": f"{state['sheet_month']}/{state['sheet_year']}" if state["sheet_month"] else None,
            "ngay_bat_dau": state["ngay_bat_dau"].strftime("%Y-%m-%d") if state["ngay_bat_dau"] else None,
            "so_ngay": state["so_ngay"],
            "link_sheets_moi_cho": state["new_sheet_pending"]["sheet_id"] if state["new_sheet_pending"] else None,
        },
        "thong_ke": {
            "tong_don_moi": state["tong_moi"],
            "tong_huy": state["tong_huy"],
            "tong_bo_qua": state["tong_bo_qua"],
        },
    }


@app.get("/log", summary="Xem log gần nhất")
def xem_log(n: int = 100):
    return {"log": state["log"][-n:]}


@app.post("/quet-ngay", summary="Quét thủ công 1 ngày cụ thể")
def quet_thu_cong(ngay: str, background_tasks: BackgroundTasks):
    """Quét bù thủ công cho 1 ngày bất kỳ (YYYY-MM-DD)."""
    if not state["sheet_id"]:
        raise HTTPException(status_code=400, detail="Chưa cấu hình! Gọi POST /cau-hinh trước.")
    try:
        datetime.strptime(ngay, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(status_code=400, detail="Sai định dạng ngày! Dùng YYYY-MM-DD")

    def _quet():
        sheet_id = state["sheet_id"]
        ngay_obj = datetime.strptime(ngay, "%Y-%m-%d")
        tab = f"{ngay_obj.day}.{ngay_obj.month}"
        create_sheet_if_not_exists(sheet_id, tab)
        setup_header(sheet_id, tab)
        ids = lay_danh_sach_id_email(ngay)
        add_log(f"🔧 [QUÉT THỦ CÔNG] Ngày {ngay}: {len(ids)} email")
        if not ids:
            return
        service = get_google_service("gmail", "v1")
        for msg in ids:
            email = tai_chi_tiet_mot_email(service, msg["id"])
            if email:
                process_email(email, [ngay], sheet_id, "thu_cong")

    background_tasks.add_task(_quet)
    return {"status": "queued", "ngay": ngay, "message": f"Đang quét ngày {ngay} trong nền..."}


@app.post("/config/sheet-moi", summary="Đăng ký link Sheets tháng mới")
def cap_nhat_sheet_moi(body: SheetMoiBody):
    """
    Đăng ký link Sheets cho tháng tiếp theo.
    Realtime job sẽ tự động chuyển sang link này khi sang tháng mới.
    """
    if not state["sheet_month"]:
        raise HTTPException(status_code=400, detail="Chưa cấu hình!")

    cur_month = state["sheet_month"]
    cur_year = state["sheet_year"]
    next_month = 1 if cur_month == 12 else cur_month + 1
    next_year = cur_year + 1 if cur_month == 12 else cur_year

    new_id = extract_sheet_id(body.sheet_link)
    if new_id == state["sheet_id"]:
        raise HTTPException(status_code=400, detail="Đây là link cũ! Nhập link của tháng mới.")

    state["new_sheet_pending"] = {
        "sheet_id": new_id,
        "month": next_month,
        "year": next_year,
    }
    add_log(f"📋 Đã đăng ký link Sheets mới cho tháng {next_month}/{next_year}")
    return {
        "status": "ok",
        "message": f"Link đã lưu. Sẽ kích hoạt tự động khi sang tháng {next_month}/{next_year}.",
        "sheet_id_moi": new_id,
    }