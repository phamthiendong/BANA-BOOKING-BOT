

import os
import base64
from bs4 import BeautifulSoup

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/spreadsheets"
]

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CREDENTIALS_PATH = os.path.join(BASE_DIR, "config", "credentials.json")
TOKEN_PATH = os.path.join(BASE_DIR, "data", "token.json")


def get_google_service(api_name, api_version):

    ensure_google_auth_files()   # <-- THÊM DÒNG NÀY
    creds = None
    if os.path.exists(TOKEN_PATH):
        creds = Credentials.from_authorized_user_file(TOKEN_PATH, SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_PATH, SCOPES)
            creds = flow.run_local_server(port=0)

        with open(TOKEN_PATH, "w", encoding="utf-8") as token:
            token.write(creds.to_json())

    return build(api_name, api_version, credentials=creds)


def decode_base64url(data):
    if not data:
        return ""
    decoded_bytes = base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))
    return decoded_bytes.decode("utf-8", errors="ignore")


def html_to_text(html):
    soup = BeautifulSoup(html, "lxml")
    return soup.get_text("\n")


def extract_body_from_payload(payload):
    body_text = ""
    if not payload:
        return body_text

    if "parts" in payload:
        for part in payload["parts"]:
            mime_type = part.get("mimeType", "")
            body_data = part.get("body", {}).get("data")

            if mime_type == "text/plain" and body_data:
                body_text += "\n" + decode_base64url(body_data)
            elif mime_type == "text/html" and body_data:
                html = decode_base64url(body_data)
                body_text += "\n" + html_to_text(html)
            elif "parts" in part:
                body_text += "\n" + extract_body_from_payload(part)
    else:
        body_data = payload.get("body", {}).get("data")
        mime_type = payload.get("mimeType", "")

        if body_data:
            content = decode_base64url(body_data)
            if mime_type == "text/html":
                body_text += "\n" + html_to_text(content)
            else:
                body_text += "\n" + content

    return body_text.strip()


def get_header_value(headers, header_name):
    for header in headers:
        if header.get("name", "").lower() == header_name.lower():
            return header.get("value", "")
    return ""


def build_gmail_link(message_id):
    return f"https://mail.google.com/mail/u/0/#all/{message_id}"


def get_emails_by_date(target_date):
    """[CHẾ ĐỘ QUÉT BÙ] Vét cạn toàn bộ mail cũ trong hòm thư của ngày đích danh (Chỉ chạy Vòng 1)"""
    service = get_google_service("gmail", "v1")
    emails = []

    print(f"\n--- 🔎 ĐANG QUÉT BÙ GMAIL TÌM ĐƠN NGÀY {target_date} ---")

    gmail_query = (
        f'from:operator@klook.com '
        f'(subject:"Klook order confirmed" OR subject:"Klook order canceled") '
        f'subject:(Fast Track) '
        f'subject:({target_date})'
    )

    all_messages = []
    next_page_token = None

    while True:
        result = service.users().messages().list(
            userId="me",
            maxResults=500,
            q=gmail_query,
            pageToken=next_page_token
        ).execute()

        messages = result.get("messages", [])
        all_messages.extend(messages)

        next_page_token = result.get("nextPageToken")
        if not next_page_token:
            break

    if not all_messages:
        print(f"❌ Không tìm thấy thư cũ nào cho ngày: {target_date}")
        return emails

    print(f"✅ Phát hiện tổng cộng {len(all_messages)} đơn cũ. Đang đồng bộ chi tiết...")

    for msg in all_messages:
        try:
            msg_data = service.users().messages().get(userId="me", id=msg["id"], format="full").execute()
            payload = msg_data.get("payload", {})
            headers = payload.get("headers", [])
            message_id = msg_data.get("id", "")

            emails.append({
                "message_id": message_id,
                "thread_id": msg_data.get("threadId", ""),
                "from": get_header_value(headers, "From"),
                "subject": get_header_value(headers, "Subject"),
                "date": get_header_value(headers, "Date"),
                "snippet": msg_data.get("snippet", ""),
                "body": extract_body_from_payload(payload),
                "email_link": build_gmail_link(message_id)
            })
        except Exception as e:
            print(f"⚠️ Lỗi tải chi tiết mail bù: {e}")
            
    return emails


def get_realtime_emails():
    """[CHẾ ĐỘ REALTIME] Chỉ lấy những thư mới bay vào hòm thư trong vòng 10 phút qua (Dùng từ Vòng 2)"""
    service = get_google_service("gmail", "v1")
    emails = []

    # newer_than:10m lọc siêu tốc trên cụm máy chủ Google, không phân biệt ngày đi bên trong thư
    gmail_query = (
        'from:operator@klook.com '
        '(subject:"Klook order confirmed" OR subject:"Klook order canceled") '
        'subject:(Fast Track) '
        'newer_than:10m'
    )

    result = service.users().messages().list(
        userId="me",
        maxResults=100, 
        q=gmail_query
    ).execute()

    messages = result.get("messages", [])
    if not messages:
        return emails

    for msg in messages:
        try:
            msg_data = service.users().messages().get(userId="me", id=msg["id"], format="full").execute()
            payload = msg_data.get("payload", {})
            headers = payload.get("headers", [])
            message_id = msg_data.get("id", "")

            emails.append({
                "message_id": message_id,
                "thread_id": msg_data.get("threadId", ""),
                "from": get_header_value(headers, "From"),
                "subject": get_header_value(headers, "Subject"),
                "date": get_header_value(headers, "Date"),
                "snippet": msg_data.get("snippet", ""),
                "body": extract_body_from_payload(payload),
                "email_link": build_gmail_link(message_id)
            })
        except Exception as e:
            print(f"⚠️ Lỗi tải chi tiết mail Realtime: {e}")

    return emails


def ensure_google_auth_files():
    os.makedirs(os.path.dirname(CREDENTIALS_PATH), exist_ok=True)
    os.makedirs(os.path.dirname(TOKEN_PATH), exist_ok=True)

    credentials_json = os.environ.get("GOOGLE_CREDENTIALS_JSON")
    token_json = os.environ.get("GOOGLE_TOKEN_JSON")

    print("GOOGLE_CREDENTIALS_JSON =", bool(credentials_json))
    print("GOOGLE_TOKEN_JSON =", bool(token_json))

    if credentials_json:
        with open(CREDENTIALS_PATH, "w", encoding="utf-8") as f:
            f.write(credentials_json)

    if token_json:
        with open(TOKEN_PATH, "w", encoding="utf-8") as f:
            f.write(token_json)



