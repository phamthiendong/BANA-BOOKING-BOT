import os
import psycopg2
from dotenv import load_dotenv
import psycopg2
# Tải các biến môi trường từ file .env
load_dotenv()

# Lấy chuỗi kết nối từ file .env
# DB_URL = os.environ.get("DATABASE_URL")

# def get_connection():
#     """Tạo kết nối tới PostgreSQL"""
#     if not DB_URL:
#         raise ValueError("❌ CHƯA CÓ DATABASE_URL! Hãy thêm vào file .env nhé.")
#     return psycopg2.connect(DB_URL)

def get_connection():
    db_url = os.getenv("DATABASE_URL")

    if not db_url:
        raise ValueError("❌ CHƯA CÓ DATABASE_URL!")

    return psycopg2.connect(db_url)


def init_db():
    """Tạo bảng nếu chưa tồn tại (Chỉ chạy 1 lần lúc bật tool)"""
    create_table_query = """
    CREATE TABLE IF NOT EXISTS klook_bookings (
        booking_id VARCHAR(50) PRIMARY KEY,
        service_date VARCHAR(20),
        status VARCHAR(50),
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(create_table_query)
                conn.commit()
        print("✅ [Database] Đã kết nối và khởi tạo thành công!")
    except Exception as e:
        print(f"❌ [Database] Lỗi khởi tạo: {e}")

def check_booking_exists(booking_id):
    """
    Kiểm tra xem mã vé đã có trong Database chưa.
    Trả về: (True/False, Trạng thái hiện tại)
    """
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT status FROM klook_bookings WHERE booking_id = %s;", (booking_id,))
                result = cur.fetchone()
                if result:
                    return True, result[0] # Đã có mã này
                return False, None # Chưa có mã này
    except Exception as e:
        print(f"❌ Lỗi kiểm tra DB mã {booking_id}: {e}")
        return False, None

def insert_booking(booking_id, service_date, status="BOOKED"):
    """Lưu mã vé mới vào Database"""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO klook_bookings (booking_id, service_date, status) VALUES (%s, %s, %s);",
                    (booking_id, service_date, status)
                )
                conn.commit()
                return True
    except Exception as e:
        print(f"❌ Lỗi lưu DB mã {booking_id}: {e}")
        return False

def update_booking_status(booking_id, new_status="CANCELLED"):
    """Cập nhật trạng thái vé (Dành cho luồng Hủy/Đổi sau này)"""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE klook_bookings SET status = %s WHERE booking_id = %s;",
                    (new_status, booking_id)
                )
                conn.commit()
                return True
    except Exception as e:
        return False

def cleanup_old_data(days_to_keep=30):
    """Tự động xóa các mã vé đã lưu quá 30 ngày để nhẹ Database"""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                delete_query = f"DELETE FROM klook_bookings WHERE created_at < NOW() - INTERVAL '{days_to_keep} days';"
                cur.execute(delete_query)
                deleted_rows = cur.rowcount
                conn.commit()
                if deleted_rows > 0:
                    print(f"🧹 [Database] Đã tự động dọn dẹp {deleted_rows} mã vé cũ hơn {days_to_keep} ngày.")
    except Exception as e:
        print(f"❌ Lỗi dọn rác Database: {e}")

