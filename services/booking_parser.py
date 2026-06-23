
import re

def clean_value(value):
    if not value:
        return ""
    # Cắt bỏ khoảng trắng và dấu hai chấm thừa ở 2 đầu rìa
    return value.strip(" :\n\r")

def parse_service_and_package(body, subject):
    san_bay = ""
    dich_vu = ""
    goi = "" # Sẽ được điền tự động ngay bên dưới

    combined = (body + " " + subject).lower()

    if "tan son nhat" in combined or "sgn" in combined:
        san_bay = "TSN"
    elif "da nang" in combined or "dad" in combined:
        san_bay = "DN"
    elif "phu quoc" in combined or "pqc" in combined:
        san_bay = "PQ"

    package_match = re.search(r"Package:\s*([^\n\r]+)", body, re.IGNORECASE)
    
    if package_match:
        dich_vu = package_match.group(1).strip()

        if not san_bay:
            if "Basic" in dich_vu or "Premium" in dich_vu or "VIP" in dich_vu:
                san_bay = "TSN"
            elif "Standard" in dich_vu:
                san_bay = "DN"
            elif dich_vu in ["International Arrival", "International Departure"]:
                san_bay = "PQ"
    else:
        if "arrival" in combined: dich_vu = "International Arrival"
        elif "departure" in combined: dich_vu = "International Departure"

    # =========================================================
    # TỰ ĐỘNG PHÂN LOẠI GÓI DỰA THEO QUY TẮC CỦA BẠN
    # =========================================================
    dich_vu_lower = dich_vu.lower()
    
    if "international arrival · premium fast track · no add-on" in dich_vu_lower:
        goi = "VIP B - DN + HÀNH LÝ"
    elif "international departure · premium (immigration + screening) · no add-on" in dich_vu_lower:
        goi = "X-RAY"
    elif "international departure · premium fast track" in dich_vu_lower:
        goi = "X-RAY"
    elif "international arrival · vip (no waiting time + immigration only) · no add-on" in dich_vu_lower:
        goi = "VIP B"

    return san_bay, dich_vu, goi


def parse_booking_email(email):
    body = email.get("body", "")
    subject = email.get("subject", "")

    san_bay, dich_vu, goi = parse_service_and_package(body, subject)

    code_match = re.search(r"(?:Booking reference ID|Booking No|Mã đơn hàng)[\:\s]*([A-Z0-9]+)", body, re.IGNORECASE)
    code = code_match.group(1) if code_match else ""

    phone_match = re.search(r"(?:Lead person mobile|Mobile|Phone|SĐT|Số điện thoại)[\:\n\s]*([\+\d\-\s\(\)]+?)(?=\r|\n|Participant|Activity|Lead|Email|$)", body, re.IGNORECASE)
    phone = clean_value(phone_match.group(1)) if phone_match else ""

    pax_match = re.search(r"Participant:\s*(\d+)", body, re.IGNORECASE)
    pax = int(pax_match.group(1)) if pax_match else 1

    # =========================================================
    # 5. TÊN KHÁCH (LƯỚI LỌC 3 TẦNG THÔNG MINH)
    # =========================================================
    name = ""

    # QUY TẮC ĐẶC QUYỀN CHO ĐÀ NẴNG (DN)
    if san_bay == "DN":
        name_match = re.search(r"(?:Lead participant|Customer Name)[\:\n\s]*([^\n\r]+?)(?=\r|\n|Country|Email|Phone|Mobile|National|$)", body, re.IGNORECASE)
        name = clean_value(name_match.group(1)) if name_match else ""

    # QUY TẮC CHUNG CHO CÁC SÂN BAY KHÁC (TSN, PQ)
    else:
        # Tầng 1: Ưu tiên chộp Full name
        names_list = []
        for match in re.finditer(r"Participant\s*\d+\s*Full\s*name:\s*([^\n\r]+)", body, re.IGNORECASE):
            names_list.append(clean_value(match.group(1)))
        
        if names_list:
            name = "\n".join(names_list)
        else:
            # Tầng 2: Không có Full name thì chộp First + Last name
            first_names = {}
            for match in re.finditer(r"Participant\s*1\s*First\s*name:\s*([^\n\r]+)", body, re.IGNORECASE):
                first_names["1"] = clean_value(match.group(1))
            for match in re.finditer(r"Participant\s*(\d+)\s*First\s*name:\s*([^\n\r]+)", body, re.IGNORECASE):
                first_names[match.group(1)] = clean_value(match.group(2))
                
            last_names = {}
            for match in re.finditer(r"Participant\s*(\d+)\s*Last\s*name:\s*([^\n\r]+)", body, re.IGNORECASE):
                last_names[match.group(1)] = clean_value(match.group(2))
                
            if first_names:
                for idx in sorted(first_names.keys(), key=int):
                    first = first_names[idx]
                    last = last_names.get(idx, "")
                    names_list.append(f"{first} {last}".strip())
                name = "\n".join(names_list)
            else:
                # Tầng 3: Bí quá thì hốt luôn Lead participant
                fallback = re.search(r"(?:Lead participant|Customer Name)[\:\n\s]*([^\n\r]+?)(?=\r|\n|Country|Email|Phone|Mobile|National|$)", body, re.IGNORECASE)
                name = clean_value(fallback.group(1)) if fallback else ""

    if not name:
        name = "CHƯA RÕ TÊN"

    name_upper = name.upper()

    # 6. Chuyến bay
    flight_match = re.search(r"(?:Flight Number|Flight details)[\:\n\s]*([A-Z0-9\s]+?)(?=\r|\n|If you|Extra|Time|Lead|Preferred|$)", body, re.IGNORECASE)
    flight = clean_value(flight_match.group(1)) if flight_match else ""

    # 7. Thời gian 
    time_match = re.search(r"(?:Time Request|Pick up time|Meet-up time)[\:\n\s]*([\d\:\sAMPMampm]+)", body, re.IGNORECASE)
    time_req = clean_value(time_match.group(1)) if time_match else ""
    if "NA" in time_req.upper(): 
        time_req = ""

      # =========================================================
    # THÊM VÀO ĐÂY: Parse ngày dịch vụ từ Subject
    # =========================================================
    service_date = ""
    date_match = re.search(r"(\d{4}-\d{2}-\d{2})", subject)
    if date_match:
        service_date = date_match.group(1)


    booking = {
        "san_bay": san_bay,
        "dich_vu": dich_vu,
        "goi": goi, 
        "code": code,
        "phone": phone,
        "pax": pax,
        "child": "",
        "name": name,
        "name_upper": name_upper,
        "flight": flight,
        "time": time_req,
        "service_date": service_date,   # ← THÊM DÒNG NÀY
        "message_id": email.get("message_id", "")
    }

    return booking