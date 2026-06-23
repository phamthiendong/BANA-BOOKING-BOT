import os
import re
import time
import threading
import calendar
from datetime import datetime, timedelta
from typing import Optional

from fastapi import FastAPI, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
import asyncio
import json

# ================================================================
# IMPORT TỪ CÁC SERVICE CỦA BẠN
# ================================================================
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
# KHỞI TẠO APP
# ================================================================
app = FastAPI(title="Klook Scanner API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Cho phép mọi origin (dev). Production thì đổi lại.
    allow_methods=["*"],
    allow_headers=["*"],
)

# ================================================================
# TRẠNG THÁI TOÀN CỤC (Global State)
# ================================================================
state = {
    # Cấu hình
    "current_sheet_id": None,
    "sheet_month": None,
    "sheet_year": None,
    "ngay_bat_dau": None,
    "so_ngay": 5,
    "danh_sach_ngay": [],

    # Link tháng mới đang chờ
    "new_sheet_pending": None,  # {"sheet_id", "month", "year"}

    # Trạng thái server
    "server_running": False,
    "server_thread": None,
    "phase": "stopped",  # "stopped" | "backfill" | "realtime"

    # Thống kê dashboard
    "don_hom_nay": 0,
    "da_ghi_sheets": 0,
    "huy_ve": 0,
    "next_scan_seconds": 0,  # Đếm ngược giây đến lần quét tiếp theo

    # Danh sách đơn mới nhất (10 dòng)
    "recent_bookings": [],

    # Log terminal
    "logs": [],
}

# Lock để tránh race condition
state_lock = threading.Lock()

# ================================================================
# PYDANTIC MODELS (Request body)
# ================================================================
class StartRequest(BaseModel):
    sheet_link: str
    ngay_bat_dau: str       # "YYYY-MM-DD"
    so_ngay: int = 5

class NewSheetRequest(BaseModel):
    sheet_link: str

# ================================================================
# HELPER FUNCTIONS
# ================================================================
def extract_sheet_id(url_or_id: str) -> str:
    match = re.search(r"/d/([a-zA-Z0-9-_]+)", url_or_id)
    return match.group(1) if match else url_or_id.strip()

def get_last_day_of_month(year: int, month: int) -> int:
    return calendar.monthrange(year, month)[1]

def get_danh_sach_ngay(ngay_bat_dau: datetime, so_ngay: int = 5):
    result = []
    for i in range(so_ngay):
        d = ngay_bat_dau + timedelta(days=i)
        result.append(d.strftime("%Y-%m-%d"))
    return result

def add_log(message: str, level: str = "info"):
    """
    Thêm log vào danh sách.
    level: "info" | "success" | "error" | "warning"
    """
    timestamp = datetime.now().strftime("%H:%M:%S")
    log_entry = {
        "time": timestamp,
        "message": message,
        "level": level,
    }
    with state_lock:
        state["logs"].append(log_entry)
        # Giữ tối đa 200 dòng log
        if len(state["logs"]) > 200:
            state["logs"] = state["logs"][-200:]

def add_recent_booking(booking: dict, status: str = "new"):
    """Thêm đơn vào danh sách recent (tối đa 50)"""
    entry = {
        "san_bay": booking.get("san_bay", ""),
        "dich_vu": booking.get("dich_vu", ""),
        "code": booking.get("code", ""),
        "pax": booking.get("pax", ""),
        "name": booking.get("name", ""),
        "status": status,  # "new" | "cancelled"
        "time": datetime.now().strftime("%H:%M:%S"),
    }
    with state_lock:
        state["recent_bookings"].insert(0, entry)
        if len(state["recent_bookings"]) > 50:
            state["recent_bookings"] = state["recent_bookings"][:50]

def reset_stats():
    with state_lock:
        state["don_hom_nay"] = 0
        state["da_ghi_sheets"] = 0
        state["huy_ve"] = 0
        state["recent_bookings"] = []

# ================================================================
# LOGIC CHÍNH (Chạy trong background thread)
# ================================================================
def run_scanner():
    """Hàm chạy ngầm: Vòng 1 quét bù + Vòng 2 realtime"""

    add_log("🚀 Bắt đầu đồng bộ dữ liệu cũ...", "info")
    with state_lock:
        state["phase"] = "backfill"

    # ---------- VÒNG 1: QUÉT BÙ ----------
    danh_sach_ngay = state["danh_sach_ngay"][:]
    current_sheet_id = state["current_sheet_id"]

    for ngay_quet in danh_sach_ngay:
        if not state["server_running"]:
            return

        ngay_obj = datetime.strptime(ngay_quet, "%Y-%m-%d")
        auto_sheet_name = f"{ngay_obj.day}.{ngay_obj.month}"

        create_sheet_if_not_exists(current_sheet_id, auto_sheet_name)
        setup_header(current_sheet_id, auto_sheet_name)

        emails_cu = get_emails_by_date(ngay_quet)
        add_log(f"📨 Ngày {ngay_quet}: tìm thấy {len(emails_cu)} đơn cũ", "info")

        for email in emails_cu:
            if not state["server_running"]:
                return

            booking = parse_booking_email(email)
            code = booking.get("code", "").upper()
            if not code:
                continue

            is_canceled = "cancel" in email.get("subject", "").lower()
            try:
                is_exist, current_status = check_booking_exists(code)

                if is_canceled:
                    if is_exist and current_status != "CANCELLED":
                        update_booking_status(code, "CANCELLED")
                        update_sheet_booking_status(current_sheet_id, auto_sheet_name, code, "HỦY VÉ")
                        add_log(f"🔴 HỦY VÉ (cũ): [{code}]", "error")
                        with state_lock:
                            state["huy_ve"] += 1
                        add_recent_booking(booking, "cancelled")
                    elif not is_exist:
                        insert_booking(code, ngay_quet, status="CANCELLED")
                        append_booking(booking, current_sheet_id, auto_sheet_name)
                        update_sheet_booking_status(current_sheet_id, auto_sheet_name, code, "HỦY VÉ")
                        add_log(f"🔴 HỦY SỚM (cũ): [{code}]", "error")
                        with state_lock:
                            state["huy_ve"] += 1
                        add_recent_booking(booking, "cancelled")
                else:
                    if not is_exist:
                        db_success = insert_booking(code, ngay_quet, status="BOOKED")
                        if db_success:
                            append_booking(booking, current_sheet_id, auto_sheet_name)
                            add_log(f"✅ ĐƠN MỚI (cũ): [{code}] → Tab {auto_sheet_name}", "success")
                            with state_lock:
                                state["don_hom_nay"] += 1
                                state["da_ghi_sheets"] += 1
                            add_recent_booking(booking, "new")
            except Exception as e:
                add_log(f"⚠️ Lỗi quét bù [{code}]: {e}", "warning")

    add_log("✅ Đồng bộ xong! Chuyển sang chế độ Realtime.", "success")
    with state_lock:
        state["phase"] = "realtime"

    # ---------- VÒNG 2: REALTIME ----------
    SLEEP_SECONDS = 300  # 5 phút

    while state["server_running"]:
        try:
            today = datetime.now()
            cleanup_old_data(days_to_keep=30)

            # Áp dụng link tháng mới nếu đã đến tháng đó
            _apply_new_sheet_if_ready(today)

            # Kiểm tra hết tháng mà chưa có link mới
            if _is_month_expired(today):
                add_log("🛑 Đã sang tháng mới nhưng chưa có link Sheets! Tool tạm dừng.", "error")
                with state_lock:
                    state["phase"] = "waiting_new_sheet"
                # Chờ cho đến khi có link mới
                while state["server_running"] and _is_month_expired(datetime.now()):
                    if state.get("new_sheet_pending"):
                        break
                    time.sleep(10)
                continue

            # Cảnh báo cuối tháng
            _warn_end_of_month(today)

            # Tính lại danh sách ngày (cửa sổ trượt)
            sheet_month = state["sheet_month"]
            sheet_year = state["sheet_year"]
            last_day = get_last_day_of_month(sheet_year, sheet_month)
            last_date = datetime(sheet_year, sheet_month, last_day)

            ngay_bat_dau_obj = datetime.strptime(state["ngay_bat_dau"], "%Y-%m-%d")
            start_date = max(today, ngay_bat_dau_obj)

            danh_sach_ngay = []
            for i in range(state["so_ngay"]):
                d = start_date + timedelta(days=i)
                if d > last_date:
                    break
                danh_sach_ngay.append(d.strftime("%Y-%m-%d"))

            with state_lock:
                state["danh_sach_ngay"] = danh_sach_ngay

            current_sheet_id = state["current_sheet_id"]

            # Tự tạo Tab mới nếu chưa có
            for ngay in danh_sach_ngay:
                ngay_obj = datetime.strptime(ngay, "%Y-%m-%d")
                tab_name = f"{ngay_obj.day}.{ngay_obj.month}"
                create_sheet_if_not_exists(current_sheet_id, tab_name)
                setup_header(current_sheet_id, tab_name)

            add_log(f"📡 Đang lắng nghe... Vùng: {', '.join(danh_sach_ngay)}", "info")

            # Quét email realtime
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
                is_canceled = "cancel" in email.get("subject", "").lower()
                is_exist, current_status = check_booking_exists(code)

                if is_canceled:
                    if is_exist and current_status != "CANCELLED":
                        update_booking_status(code, "CANCELLED")
                        update_sheet_booking_status(current_sheet_id, auto_sheet_name, code, "HỦY VÉ")
                        add_log(f"🔴 HỦY VÉ: [{code}] → Tab {auto_sheet_name}", "error")
                        with state_lock:
                            state["huy_ve"] += 1
                        add_recent_booking(booking, "cancelled")
                    elif not is_exist:
                        insert_booking(code, ngay_di, status="CANCELLED")
                        append_booking(booking, current_sheet_id, auto_sheet_name)
                        update_sheet_booking_status(current_sheet_id, auto_sheet_name, code, "HỦY VÉ")
                        add_log(f"🔴 HỦY SỚM: [{code}] → Tab {auto_sheet_name}", "error")
                        with state_lock:
                            state["huy_ve"] += 1
                        add_recent_booking(booking, "cancelled")
                else:
                    if not is_exist:
                        db_success = insert_booking(code, ngay_di, status="BOOKED")
                        if db_success:
                            append_booking(booking, current_sheet_id, auto_sheet_name)
                            add_log(f"✅ ĐƠN MỚI: [{code}] ngày {ngay_di} → Tab {auto_sheet_name}", "success")
                            with state_lock:
                                state["don_hom_nay"] += 1
                                state["da_ghi_sheets"] += 1
                            add_recent_booking(booking, "new")

            # Đếm ngược 5 phút
            for remaining in range(SLEEP_SECONDS, 0, -1):
                if not state["server_running"]:
                    return
                with state_lock:
                    state["next_scan_seconds"] = remaining
                time.sleep(1)

        except Exception as e:
            add_log(f"⚠️ Lỗi: {e}. Thử lại sau 5 phút...", "warning")
            time.sleep(SLEEP_SECONDS)

    add_log("🛑 Server đã dừng an toàn.", "info")
    with state_lock:
        state["phase"] = "stopped"
        state["server_running"] = False

def _apply_new_sheet_if_ready(today: datetime):
    pending = state.get("new_sheet_pending")
    if not pending:
        return
    if today.month == pending["month"] and today.year == pending["year"]:
        with state_lock:
            state["current_sheet_id"] = pending["sheet_id"]
            state["sheet_month"] = pending["month"]
            state["sheet_year"] = pending["year"]
            state["new_sheet_pending"] = None
        add_log(f"🎉 Đã chuyển sang Sheets tháng {pending['month']}/{pending['year']}!", "success")

def _is_month_expired(today: datetime) -> bool:
    return (
        today.month != state["sheet_month"] or
        today.year != state["sheet_year"]
    ) and state["new_sheet_pending"] is None

def _warn_end_of_month(today: datetime):
    sheet_month = state["sheet_month"]
    sheet_year = state["sheet_year"]
    if today.month != sheet_month or today.year != sheet_year:
        return
    last_day = get_last_day_of_month(sheet_year, sheet_month)
    days_left = last_day - today.day
    if 0 <= days_left <= 2:
        add_log(
            f"⚠️ Còn {days_left + 1} ngày hết tháng {sheet_month}/{sheet_year}! Chuẩn bị link mới!",
            "warning"
        )

# ================================================================
# API ENDPOINTS
# ================================================================

@app.get("/")
def root():
    return {"status": "Klook Scanner API đang chạy"}


@app.post("/api/start")
def start_server(req: StartRequest):
    """Khởi động scanner"""
    if state["server_running"]:
        return {"success": False, "message": "Server đang chạy rồi!"}

    # Validate ngày
    try:
        ngay_bat_dau_obj = datetime.strptime(req.ngay_bat_dau, "%Y-%m-%d")
    except ValueError:
        return {"success": False, "message": "Ngày không đúng định dạng YYYY-MM-DD"}

    # Validate số ngày
    so_ngay = max(1, min(req.so_ngay, 5))  # Tối thiểu 1, tối đa 5

    sheet_id = extract_sheet_id(req.sheet_link)
    if not sheet_id:
        return {"success": False, "message": "Link Sheets không hợp lệ"}

    # Khởi tạo DB
    init_db()

    # Reset state
    reset_stats()

    danh_sach_ngay = get_danh_sach_ngay(ngay_bat_dau_obj, so_ngay)

    with state_lock:
        state["current_sheet_id"] = sheet_id
        state["sheet_month"] = ngay_bat_dau_obj.month
        state["sheet_year"] = ngay_bat_dau_obj.year
        state["ngay_bat_dau"] = req.ngay_bat_dau
        state["so_ngay"] = so_ngay
        state["danh_sach_ngay"] = danh_sach_ngay
        state["server_running"] = True
        state["phase"] = "backfill"
        state["logs"] = []

    # Chạy ngầm
    t = threading.Thread(target=run_scanner, daemon=True)
    t.start()

    with state_lock:
        state["server_thread"] = t

    add_log(f"🚀 Server khởi động! Vùng: {', '.join(danh_sach_ngay)}", "success")

    return {
        "success": True,
        "message": "Server đã khởi động!",
        "danh_sach_ngay": danh_sach_ngay,
    }


@app.post("/api/stop")
def stop_server():
    """Dừng scanner"""
    if not state["server_running"]:
        return {"success": False, "message": "Server chưa chạy"}

    with state_lock:
        state["server_running"] = False
        state["phase"] = "stopped"

    add_log("🛑 Đã gửi lệnh dừng server.", "warning")
    return {"success": True, "message": "Đang dừng server..."}


@app.post("/api/new-sheet")
def save_new_sheet(req: NewSheetRequest):
    """Lưu link Sheets tháng mới vào hàng chờ"""
    if not state["current_sheet_id"]:
        return {"success": False, "message": "Chưa có server đang chạy"}

    new_id = extract_sheet_id(req.sheet_link)

    # Không cho nhập link cũ
    if new_id == state["current_sheet_id"]:
        return {"success": False, "message": "Đây là link cũ! Vui lòng nhập link tháng mới."}

    # Tính tháng mới
    current_month = state["sheet_month"]
    current_year = state["sheet_year"]
    if current_month == 12:
        next_month, next_year = 1, current_year + 1
    else:
        next_month, next_year = current_month + 1, current_year

    with state_lock:
        state["new_sheet_pending"] = {
            "sheet_id": new_id,
            "month": next_month,
            "year": next_year,
        }

    add_log(f"💾 Đã lưu link tháng {next_month}/{next_year}. Tự kích hoạt lúc 00:00 ngày 1/{next_month}.", "success")

    return {
        "success": True,
        "message": f"Đã lưu! Link sẽ kích hoạt ngày 1/{next_month}/{next_year}",
        "next_month": next_month,
        "next_year": next_year,
    }


@app.get("/api/status")
def get_status():
    """Trả về toàn bộ trạng thái hiện tại cho UI poll"""
    today = datetime.now()
    sheet_month = state["sheet_month"] or today.month
    sheet_year = state["sheet_year"] or today.year
    last_day = get_last_day_of_month(sheet_year, sheet_month)
    days_left = last_day - today.day if today.month == sheet_month else 0

    return {
        # Server
        "server_running": state["server_running"],
        "phase": state["phase"],

        # Cấu hình
        "current_sheet_id": state["current_sheet_id"],
        "sheet_month": state["sheet_month"],
        "sheet_year": state["sheet_year"],
        "ngay_bat_dau": state["ngay_bat_dau"],
        "so_ngay": state["so_ngay"],
        "danh_sach_ngay": state["danh_sach_ngay"],

        # Link tháng mới
        "new_sheet_pending": state["new_sheet_pending"],

        # Thống kê
        "don_hom_nay": state["don_hom_nay"],
        "da_ghi_sheets": state["da_ghi_sheets"],
        "huy_ve": state["huy_ve"],
        "next_scan_seconds": state["next_scan_seconds"],

        # Cảnh báo cuối tháng
        "days_left_in_month": days_left,
        "warn_end_of_month": 0 <= days_left <= 2 and today.month == sheet_month,

        # Đơn gần nhất
        "recent_bookings": state["recent_bookings"][:10],
    }


@app.get("/api/logs")
def get_logs(limit: int = 50):
    """Trả về log gần nhất"""
    logs = state["logs"]
    return {"logs": logs[-limit:]}


@app.get("/api/logs/stream")
async def stream_logs():
    """
    Server-Sent Events: UI subscribe để nhận log realtime
    Dùng: EventSource('/api/logs/stream') trong JS
    """
    async def event_generator():
        last_index = len(state["logs"])
        while True:
            await asyncio.sleep(1)
            current_logs = state["logs"]
            if len(current_logs) > last_index:
                new_logs = current_logs[last_index:]
                for log in new_logs:
                    yield f"data: {json.dumps(log, ensure_ascii=False)}\n\n"
                last_index = len(current_logs)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        }
    )


@app.delete("/api/logs")
def clear_logs():
    """Xóa toàn bộ log"""
    with state_lock:
        state["logs"] = []
    return {"success": True}


# ================================================================
# CHẠY SERVER
# ================================================================
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server:app", host="0.0.0.0", port=8000, reload=False)