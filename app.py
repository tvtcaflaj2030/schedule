import io
import re
import os
import fitz  # PyMuPDF
from flask import Flask, render_template, request, send_file, jsonify, redirect, url_for, session

app = Flask(__name__)
app.secret_key = "secret_admin_key_turki_aflaj"
ADMIN_PASSWORD = "turki2026"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SCHEDULES_PDF = os.path.join(BASE_DIR, "schedules.pdf")
ATTENDANCE_PDF = os.path.join(BASE_DIR, "attendance.pdf")

# القائمة الثابتة بالخدمات الجديدة والمصححة تماماً
SERVICES_LIST = [
    {
        "title": "الخدمات الذاتية للمتدربين (رايات)",
        "url": "https://tvtc.gov.sa/ar/Departments/tvtcdepartments/Rayat/pages/E-Services.aspx",
        "icon": "fa-user-gear"
    },
    {
        "title": "شرح اضافة الايبان",
        "url": "https://youtu.be/rTF7pRETF2A?si=4x6fBDz-oXKRSxKp",
        "icon": "fa-money-bill-transfer"
    },
    {
        "title": "عرض جدول المتدرب",
        "url": "/schedule",
        "icon": "fa-table-cells"
    },
    {
        "title": "طلب شهادة تعريف",
        "url": "https://tvtc.gov.sa/ar/Training-Units/Boys-Colleges/AQTC/Documents/%D8%A3%D8%AF%D9%84%D8%A9%20%D8%AE%D8%A7%D8%B5%D8%A9%20%D9%84%D9%84%D9%85%D8%AA%D8%AF%D8%B1%D8%A8%D9%8A%D9%86/%D8%A3%D8%AF%D9%84%D8%A9%20%D8%A8%D9%88%D8%A7%D8%A8%D8%A9%20%D8%B1%D8%A7%D9%8A%D8%A7%D8%AA%20%D9%84%D9%84%D9%85%D8%AA%D8%AF%D8%B1%D8%A8/%D8%AF%D9%84%D9%8A%D9%84%20%D8%B7%D9%84%D8%A8%20%D8%B4%D9%87%D8%A7%D8%AF%D8%A9%20%D8%A7%D9%84%D8%AA%D8%B9%D8%B1%D9%8A%D9%81%20%D8%B9%D9%86%20%D8%B7%D8%B1%D9%8A%D9%82%20%D8%B1%D8%A7%D9%8A%D8%A7%D8%AA.pdf",
        "icon": "fa-file-lines"
    },
    {
        "title": "التدرب عن بعد (تقني)",
        "url": "https://tvtclms.edu.sa/?ref=saudiwins.com",
        "icon": "fa-laptop-code"
    },
    {
        "title": "البريد الإلكتروني",
        "url": "https://outlook.office.com",
        "icon": "fa-envelope"
    }
]

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
    return render_template("services.html", services=SERVICES_LIST)

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

    return render_template("admin.html", 
                           logged_in=session.get("logged_in", False), 
                           msg=msg, 
                           msg_type=msg_type)

@app.route("/admin/logout")
def admin_logout():
    session.pop("logged_in", None)
    return redirect(url_for("admin"))

@app.route('/admin/attendance')
# ضع ديكوريتور الحماية الخاص بك هنا إن وجد (مثل @login_required)
def attendance_tracker():
    return render_template('attendance_tracker.html')

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
