

from services.gmail_service import get_google_service

BOOKING_COLUMNS = [
    "Sân Bay", "Dịch vụ", "Gói", "Code", "Số điện thoại", "Số khách",
    "TRE EM", "Tên khách", "CHỮ IN HOA", "Chuyến bay", "Thời gian",
    "Thời gian gặp", "NOTE", "XÁC NHẬN", "LÝ DO HỦY"
]

def get_sheets_service():
    return get_google_service("sheets", "v4")

def setup_header(spreadsheet_id, sheet_name):
    service = get_sheets_service()
    range_name = f"'{sheet_name}'!A1:O1"  # Thêm nháy đơn phòng trường hợp tên tab có dấu cách

    service.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id,
        range=range_name,
        valueInputOption="RAW",
        body={"values": [BOOKING_COLUMNS]},
    ).execute()

def get_processed_codes(spreadsheet_id, sheet_name):
    service = get_sheets_service()
    range_name = f"'{sheet_name}'!D2:D"

    result = service.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id,
        range=range_name,
    ).execute()

    rows = result.get("values", [])
    return set(row[0].strip().upper() for row in rows if row and row[0].strip())

def is_processed(code, spreadsheet_id, sheet_name):
    if not code:
        return False
    processed_codes = get_processed_codes(spreadsheet_id, sheet_name)
    return code.strip().upper() in processed_codes



def append_booking(booking, spreadsheet_id, sheet_name):
    service = get_sheets_service()

    row = [
        booking.get("san_bay", ""),
        booking.get("dich_vu", ""),
        booking.get("goi", ""),
        booking.get("code", ""),
        booking.get("phone", ""),
        booking.get("pax", ""),
        booking.get("child", ""),
        booking.get("name", ""),
        booking.get("name_upper", ""),
        booking.get("flight", ""),
        booking.get("time", ""),
        "", "", "", "",
    ]

    # Đếm hàng dựa theo cột A (Sân Bay) - bỏ qua dropdown ở cột N
    result = service.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id,
        range=f"'{sheet_name}'!A:A"
    ).execute()
    
    all_rows = result.get("values", [])
    next_row = len(all_rows) + 1  # Hàng tiếp theo

    # Ghi thẳng vào đúng hàng cuối
    range_name = f"'{sheet_name}'!A{next_row}"
    
    service.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id,
        range=range_name,
        valueInputOption="USER_ENTERED",
        body={"values": [row]},
    ).execute()

def create_sheet_if_not_exists(spreadsheet_id, sheet_name):
    """Kiểm tra xem Tab đã tồn tại chưa, nếu chưa thì tự động tạo mới."""
    service = get_sheets_service()
    
    # 1. Lấy danh sách tất cả các Tab đang có trong file
    sheet_metadata = service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
    sheets = sheet_metadata.get('sheets', '')
    
    # Lọc ra danh sách tên của các Tab hiện tại
    existing_sheet_names = [sheet.get("properties", {}).get("title", "") for sheet in sheets]
    
    # 2. Nếu tên Tab chưa có, gửi lệnh tạo mới
    if sheet_name not in existing_sheet_names:
        print(f"✨ Phát hiện ngày mới! Đang tự động tạo Tab: '{sheet_name}'...")
        request_body = {
            'requests': [{
                'addSheet': {
                    'properties': {
                        'title': sheet_name,
                    }
                }
            }]
        }
        service.spreadsheets().batchUpdate(
            spreadsheetId=spreadsheet_id,
            body=request_body
        ).execute()
        return True # Trả về True nếu vừa tạo mới
    return False # Trả về False nếu Tab đã tồn tại rồi


def update_sheet_booking_status(spreadsheet_id, sheet_name, code, status_text="HỦY VÉ"):
    """Dò tìm mã Code trên Sheets, nếu thấy thì ghi chữ 'HỦY VÉ' vào cột XÁC NHẬN (Cột N)"""
    service = get_sheets_service()
    range_name = f"'{sheet_name}'!D2:D" # Quét toàn bộ cột Code (Cột D) để tìm hàng

    result = service.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id,
        range=range_name,
    ).execute()
    
    rows = result.get("values", [])
    for idx, row in enumerate(rows):
        if row and row[0].strip().upper() == code.strip().upper():
            row_num = idx + 2 # Cộng 2 vì dữ liệu tính từ hàng số 2
            
            # Cột N là cột thứ 14 (Cột XÁC NHẬN)
            update_range = f"'{sheet_name}'!N{row_num}" 
            
            service.spreadsheets().values().update(
                spreadsheetId=spreadsheet_id,
                range=update_range,
                valueInputOption="USER_ENTERED",
                body={"values": [[status_text]]}
            ).execute()
            return True
    return False


