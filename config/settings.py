import os
from dotenv import load_dotenv


load_dotenv()


SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")
BOOKING_SHEET_NAME = os.getenv("BOOKING_SHEET_NAME", "Bookings")