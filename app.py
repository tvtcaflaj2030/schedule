import io
import re
import os
import json
import fitz  # PyMuPDF
from flask import Flask, render_template, request, send_file, jsonify, redirect, url_for, session

app = Flask(__name__)
app.secret_key = "secret_admin_key_turki_aflaj"
ADMIN_PASSWORD = "turki2026"

SCHEDULES_PDF = "schedules.pdf"
ATTENDANCE_PDF = "attendance.pdf"
SERVICES_FILE = "services.json"

def normalize_digits(text):
    arabic_digits = "٠١٢٣٤٥٦٧٨٩"
    english_digits = "0123456789"
    return text.translate(str.maketrans(arabic_digits, english_digits))

def load_services():
    if not os.path.exists(SERVICES_FILE):
        default_services = [
            {"title": "التسجيل الذاتي للمتدربين", "url": "https://ugate.tvtc.gov.sa/AFrontGate/", "icon": "fa-id-card"},
            {"title": "الخدمات الذاتية للمتدربين", "url": "https://rayat.tvtc.gov.sa", "icon": "fa-user-gear"},
            {"title": "عرض جدول المتدرب", "url": "/schedule", "icon": "fa-table-cells"},
            {"title": "هل نسيت كلمة المرور ؟", "url": "https://iam.tvtc.gov.sa", "icon": "fa-key"},
            {"title": "أمن حسابك", "url": "https://iam.tvtc.gov.sa", "icon": "fa-shield-halved"},
            {"title": "البريد الإلكتروني", "url": "https://outlook.office.com", "icon": "fa-envelope"}
        ]
        with open(SERVICES_FILE, "w", encoding="utf-8") as f:
            json.dump(default_services, f, ensure_ascii=False, indent=2)
        return default_services
    try:
        with open(SERVICES_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []

def save_services(data):
    with open(SERVICES_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

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

def extract_student_attendance(trainee_id):
    clean_id = normalize_digits(trainee_id).strip()
    if not os.path.exists(ATTENDANCE_PDF) or len(clean_id) < 9:
        return None

    try:
        doc = fitz.open(ATTENDANCE_PDF)
        target_page = None
        id_pattern = re.compile(rf'(?<!\d){re.escape(clean_id)}(?!\d)|(?<!\d){re.escape(clean_id[::-1])}(?!\d)')

        for page in doc:
            txt = normalize_digits(page.get_text())
            if id_pattern.search(txt):
                target_page = page
                break

        if not target_page:
            return None

        raw_text = target_page.get_text()
        
        # استخراج اسم المتدرب
        student_name = "متدرب"
        name_match = re.search(r'اسم المتدرب\s+([^\n\r]+)', raw_text)
        if name_match:
            student_name = name_match.group(1).strip()

        courses = []
        tabs = target_page.find_tables()
        if tabs.tables:
            table_data = tabs.tables[0].extract()
            for row in table_data:
                clean_row = [normalize_digits(str(cell or '')).strip() for cell in row if str(cell or '').strip()]
                row_str = " ".join(clean_row)

                # البحث عن صفوف المقررات
                if any(k in row_str for k in ['حاسب', 'سلك', 'سلم', 'مهني', 'فيزي', 'نشاط', 'عرب', 'ريض', 'مقرر']):
                    c_name = clean_row[-1] if clean_row else "مقرر"
                    
                    # استخراج الأرقام العشرية ونسب الغياب
                    numbers = re.findall(r'\b\d+(?:\.\d+)?\b', row_str)
                    float_vals = [float(n) for n in numbers]
                    
                    pct = 0.0
                    for val in float_vals:
                        if 0.0 < val <= 100.0:
                            pct = val
                            break
                    
                    status = "منتظم"
                    if pct >= 20.0 or "حرمان" in row_str:
                        status = "حرمان"
                    elif pct >= 15.0:
                        status = "إنذار ثانٍ"
                    elif pct >= 10.0:
                        status = "إنذار أول"

                    courses.append({
                        "course_name": c_name.replace('\n', ' '),
                        "percentage": pct,
                        "status": status
                    })

        return {
            "student_id": clean_id,
            "student_name": student_name,
            "courses": courses
        }
    except Exception as e:
        print(f"Error reading attendance: {e}")
        return None

# --- المسارات (Routes) ---

@app.route("/")
def home():
    # الصفحة الرئيسية بالخيارات الثلاثة
    return render_template("home.html")

@app.route("/schedule")
def schedule_page():
    return render_template("schedule.html")

@app.route("/attendance")
def attendance_page():
    return render_template("attendance.html")

@app.route("/services")
def services():
    return render_template("services.html", services=load_services())

@app.route("/search_schedule", methods=["POST"])
def search_schedule():
    trainee_id = normalize_digits(request.form.get("trainee_id", "").strip())
    if len(trainee_id) < 9 or not trainee_id.isdigit():
        return jsonify({"success": False, "message": "يرجى إدخال الرقم التدريبي بشكل صحيح."})

    pages = find_student_pages(SCHEDULES_PDF, trainee_id)
    if not pages:
        return jsonify({"success": False, "message": "لم يتم العثور على جدول مطابق لهذا الرقم."})

    return jsonify({"success": True, "pages": ",".join(map(str, pages))})

@app.route("/search_attendance", methods=["POST"])
def search_attendance():
    trainee_id = normalize_digits(request.form.get("trainee_id", "").strip())
    if len(trainee_id) < 9 or not trainee_id.isdigit():
        return jsonify({"success": False, "message": "يرجى إدخال الرقم التدريبي بشكل صحيح."})

    data = extract_student_attendance(trainee_id)
    if not data or not data["courses"]:
        return jsonify({"success": False, "message": "لم يتم العثور على سجل غياب مطابق لهذا الرقم التدريبي."})

    return jsonify({"success": True, "data": data})

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
                msg = "تم تحديث ملف الجداول بنجاح!"
                msg_type = "success"

        elif action == "upload_attendance":
            if not session.get("logged_in"): return redirect(url_for("admin"))
            file = request.files.get("pdf_file")
            if file and file.filename.endswith(".pdf"):
                file.save(ATTENDANCE_PDF)
                msg = "تم تحديث ملف الغياب بنجاح!"
                msg_type = "success"

        elif action == "save_services":
            if not session.get("logged_in"): return redirect(url_for("admin"))
            titles = request.form.getlist("title[]")
            urls = request.form.getlist("url[]")
            icons = request.form.getlist("icon[]")
            
            updated_services = [
                {"title": t.strip(), "url": u.strip(), "icon": i.strip() if i.strip() else "fa-link"}
                for t, u, i in zip(titles, urls, icons) if t.strip() and u.strip()
            ]
            save_services(updated_services)
            msg = "تم حفظ خدمات المتدربين بنجاح!"
            msg_type = "success"

    services_list = load_services() if session.get("logged_in") else []
    return render_template("admin.html", logged_in=session.get("logged_in", False), msg=msg, msg_type=msg_type, services=services_list)

@app.route("/admin/logout")
def admin_logout():
    session.pop("logged_in", None)
    return redirect(url_for("admin"))

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
