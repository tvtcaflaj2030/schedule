import io
import re
import os
import json
import fitz  # PyMuPDF
from flask import Flask, render_template, request, send_file, jsonify, redirect, url_for, session

app = Flask(__name__)
app.secret_key = "secret_admin_key_turki_aflaj"
ADMIN_PASSWORD = "turki2026"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SCHEDULES_PDF = os.path.join(BASE_DIR, "schedules.pdf")
ATTENDANCE_PDF = os.path.join(BASE_DIR, "attendance.pdf")
SERVICES_FILE = os.path.join(BASE_DIR, "services_data.json")

DEFAULT_SERVICES = [
    {"title": "الخدمات الذاتية للمتدربين (رايات)", "url": "https://tvtc.gov.sa/ar/Departments/tvtcdepartments/Rayat/pages/E-Services.aspx": "fa-user-gear"},
    {"title": "شرح اضافة الايبان", "url": "https://youtu.be/rTF7pRETF2A?si=4x6fBDz-oXKRSxKp"},
    {"title": "عرض جدول المتدرب", "url": "/schedule", "icon": "fa-table-cells"},
    {"title": "طلب شهادة تعريف", "url": "https://tvtc.gov.sa/ar/Training-Units/Boys-Colleges/AQTC/Documents/%D8%A3%D8%AF%D9%84%D8%A9%20%D8%AE%D8%A7%D8%B5%D8%A9%20%D9%84%D9%84%D9%85%D8%AA%D8%AF%D8%B1%D8%A8%D9%8A%D9%86/%D8%A3%D8%AF%D9%84%D8%A9%20%D8%A8%D9%88%D8%A7%D8%A8%D8%A9%20%D8%B1%D8%A7%D9%8A%D8%A7%D8%AA%20%D9%84%D9%84%D9%85%D8%AA%D8%AF%D8%B1%D8%A8/%D8%AF%D9%84%D9%8A%D9%84%20%D8%B7%D9%84%D8%A8%20%D8%B4%D9%87%D8%A7%D8%AF%D8%A9%20%D8%A7%D9%84%D8%AA%D8%B9%D8%B1%D9%8A%D9%81%20%D8%B9%D9%86%20%D8%B7%D8%B1%D9%8A%D9%82%20%D8%B1%D8%A7%D9%8A%D8%A7%D8%AA.pdf", "icon": "fa-key"},
    {"title": "التدرب عن بعد (تقني)", "url": "https://tvtclms.edu.sa/?ref=saudiwins.com", "icon": "fa-vellum fa-solid fa-laptop"},
    {"title": "البريد الإلكتروني", "url": "https://outlook.office.com", "icon": "fa-envelope"}
]

def load_services():
    """قراءة الخدمات المحفوظة وضمان عدم فقدانها"""
    if not os.path.exists(SERVICES_FILE):
        save_services(DEFAULT_SERVICES)
        return DEFAULT_SERVICES
    try:
        with open(SERVICES_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, list) and len(data) > 0:
                return data
            return DEFAULT_SERVICES
    except Exception as e:
        print(f"Error loading services: {e}")
        return DEFAULT_SERVICES

def save_services(data):
    """حفظ فوري ومباشر على القرص مع ضمان تفريغ الذاكرة المؤقتة"""
    try:
        with open(SERVICES_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
            f.flush()
            os.fsync(f.fileno())
        return True
    except Exception as e:
        print(f"Error saving services: {e}")
        return False

def normalize_digits(text):
    arabic_digits = "٠١٢٣٤٥٦٧٨٩"
    english_digits = "0123456789"
    return text.translate(str.maketrans(arabic_digits, english_digits))

def find_student_pages(pdf_path, trainee_id):
    clean_id = normalize_digits(trainee_id).strip()
    if len(clean_id) < 9 or not clean_id.isdigit():
        return []

    id_pattern = re.compile(rf'(?<!\d){re.escape(clean_id)}(?!\d)|(?<!\d){re.escape(clean_id[::-1])}(?!\d)')
    matched_pages = []
    
    if not os.path.exists(pdf_path):
        return []

    try:
        doc = fitz.open(pdf_path)
        for page_num in range(len(doc)):
            clean_page_text = normalize_digits(doc[page_num].get_text())
            if id_pattern.search(clean_page_text):
                matched_pages.append(page_num)
        return matched_pages
    except Exception:
        return []

# --- مسارات الواجهة ---

@app.route("/")
def home():
    return render_template("home.html")

@app.route("/schedule")
def schedule_page():
    return render_template("schedule.html")

@app.route("/attendance")
def attendance_page():
    return render_template("attendance.html")

@app.route("/services")
def services():
    current_services = load_services()
    return render_template("services.html", services=current_services)

@app.route("/search_schedule", methods=["POST"])
def search_schedule():
    trainee_id = normalize_digits(request.form.get("trainee_id", "").strip())
    if len(trainee_id) < 9 or not trainee_id.isdigit():
        return jsonify({"success": False, "message": "يرجى إدخال الرقم التدريبي بشكل صحيح."})

    pages = find_student_pages(SCHEDULES_PDF, trainee_id)
    if not pages:
        return jsonify({"success": False, "message": "لم يتم العثور على جدول مطابق لهذا الرقم."})

    return jsonify({"success": True, "pages": ",".join(map(str, pages))})

@app.route("/get_schedule_image")
def get_schedule_image():
    pages_param = request.args.get("pages", "")
    if not pages_param or not os.path.exists(SCHEDULES_PDF):
        return "معلمات غير صالحة", 400

    try:
        page_indices = [int(p) for p in pages_param.split(",") if p.isdigit()]
        doc = fitz.open(SCHEDULES_PDF)
        temp_doc = fitz.open()

        for p_num in page_indices:
            if 0 <= p_num < len(doc):
                temp_doc.insert_pdf(doc, from_page=p_num, to_page=p_num)

        if len(temp_doc) == 0:
            return "لم يتم العثور على صفحات", 404

        if len(temp_doc) == 1:
            pix = temp_doc[0].get_pixmap(dpi=200)
            img_bytes = pix.tobytes("png")
        else:
            combined_doc = fitz.open()
            p_width = max(p.rect.width for p in temp_doc)
            p_height = sum(p.rect.height for p in temp_doc)
            new_page = combined_doc.new_page(width=p_width, height=p_height)

            cur_y = 0
            for p in temp_doc:
                new_page.show_pdf_page(fitz.Rect(0, cur_y, p.rect.width, cur_y + p.rect.height), temp_doc, p.number)
                cur_y += p.rect.height

            img_bytes = new_page.get_pixmap(dpi=200).tobytes("png")

        return send_file(io.BytesIO(img_bytes), mimetype="image/png", download_name="schedule.png")
    except Exception as e:
        return str(e), 500

# --- لوحة التحكم ---

@app.route("/admin", methods=["GET", "POST"])
def admin():
    msg = None
    msg_type = None

    if request.method == "POST":
        action = request.form.get("action")
        
        if action == "login":
            if request.form.get("password") == ADMIN_PASSWORD:
                session["logged_in"] = True
            else:
                msg = "كلمة المرور غير صحيحة"
                msg_type = "error"

        elif action == "upload_schedule":
            if not session.get("logged_in"): return redirect(url_for("admin"))
            file = request.files.get("pdf_file")
            if file and file.filename.endswith(".pdf"):
                file.save(SCHEDULES_PDF)
                msg = "تم رفع وتحديث ملف الجداول بنجاح!"
                msg_type = "success"

        elif action == "upload_attendance":
            if not session.get("logged_in"): return redirect(url_for("admin"))
            file = request.files.get("pdf_file")
            if file and file.filename.endswith(".pdf"):
                file.save(ATTENDANCE_PDF)
                msg = "تم رفع وتحديث تقرير الغياب بنجاح!"
                msg_type = "success"

        elif action == "save_services":
            if not session.get("logged_in"): return redirect(url_for("admin"))
            
            raw_payload = request.form.get("services_payload")
            try:
                parsed_data = json.loads(raw_payload)
                if isinstance(parsed_data, list) and len(parsed_data) > 0:
                    save_services(parsed_data)
                    msg = "تم حفظ وتثبيت الخدمات بنجاح ولن تتغير بعد الآن!"
                    msg_type = "success"
                else:
                    msg = "قائمة الخدمات فارغة، لم يتم الحفظ."
                    msg_type = "error"
            except Exception as err:
                msg = f"خطأ أثناء معالجة البيانات: {err}"
                msg_type = "error"

    current_services = load_services() if session.get("logged_in") else []
    return render_template("admin.html", 
                           logged_in=session.get("logged_in", False), 
                           msg=msg, 
                           msg_type=msg_type, 
                           services=current_services)

@app.route("/admin/logout")
def admin_logout():
    session.pop("logged_in", None)
    return redirect(url_for("admin"))

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
