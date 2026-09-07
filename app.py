import io
import re
import os
import sqlite3
import fitz  # PyMuPDF
from flask import Flask, render_template, request, send_file, jsonify, redirect, url_for, session

app = Flask(__name__)
app.secret_key = "secret_admin_key_turki_aflaj"
ADMIN_PASSWORD = "turki2026"

SCHEDULES_PDF = "schedules.pdf"
ATTENDANCE_PDF = "attendance.pdf"
DB_PATH = "portal_data.db"

def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS services (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                url TEXT NOT NULL,
                icon TEXT NOT NULL
            )
        ''')
        # إدخال الخدمات الافتراضية لأول مرة فقط إن كان الجدول فارغاً
        cursor.execute("SELECT COUNT(*) FROM services")
        if cursor.fetchone()[0] == 0:
            defaults = [
                ("التسجيل الذاتي للمتدربين", "https://ugate.tvtc.gov.sa/AFrontGate/", "fa-id-card"),
                ("الخدمات الذاتية للمتدربين", "https://rayat.tvtc.gov.sa", "fa-user-gear"),
                ("عرض جدول المتدرب", "/schedule", "fa-table-cells"),
                ("هل نسيت كلمة المرور ؟", "https://iam.tvtc.gov.sa", "fa-key"),
                ("أمن حسابك", "https://iam.tvtc.gov.sa", "fa-shield-halved"),
                ("البريد الإلكتروني", "https://outlook.office.com", "fa-envelope")
            ]
            cursor.executemany("INSERT INTO services (title, url, icon) VALUES (?, ?, ?)", defaults)
        conn.commit()

init_db()

def get_services():
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id, title, url, icon FROM services ORDER BY id ASC")
        rows = cursor.fetchall()
        return [{"id": r[0], "title": r[1], "url": r[2], "icon": r[3]} for r in rows]

def update_all_services(services_list):
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM services")
        for s in services_list:
            cursor.execute("INSERT INTO services (title, url, icon) VALUES (?, ?, ?)", 
                           (s["title"], s["url"], s["icon"]))
        conn.commit()

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

# --- المسارات ---

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
    current_services = get_services()
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
                msg = "تم تحديث ملف تقرير الغياب بنجاح!"
                msg_type = "success"

        elif action == "save_services":
            if not session.get("logged_in"): return redirect(url_for("admin"))
            
            titles = request.form.getlist("title[]")
            urls = request.form.getlist("url[]")
            icons = request.form.getlist("icon[]")
            
            cleaned = []
            for t, u, i in zip(titles, urls, icons):
                if t.strip() and u.strip():
                    cleaned.append({
                        "title": t.strip(),
                        "url": u.strip(),
                        "icon": i.strip() if i.strip() else "fa-link"
                    })
            
            update_all_services(cleaned)
            msg = "تم حفظ وتثبيت الخدمات بنجاح في قاعدة البيانات!"
            msg_type = "success"

    services_data = get_services() if session.get("logged_in") else []
    return render_template("admin.html", 
                           logged_in=session.get("logged_in", False), 
                           msg=msg, 
                           msg_type=msg_type, 
                           services=services_data)

@app.route("/admin/logout")
def admin_logout():
    session.pop("logged_in", None)
    return redirect(url_for("admin"))

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
