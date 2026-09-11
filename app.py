import io
import re
import os
import json
import sqlite3
from datetime import datetime
import fitz  # PyMuPDF
from flask import Flask, render_template, request, send_file, jsonify, redirect, url_for, session

app = Flask(__name__)
app.secret_key = "secret_admin_key_turki_aflaj"
ADMIN_PASSWORD = "turki2026"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SCHEDULES_PDF = os.path.join(BASE_DIR, "schedules.pdf")
ATTENDANCE_PDF = os.path.join(BASE_DIR, "attendance.pdf")
DB_PATH = os.path.join(BASE_DIR, "attendance_archive.db")

# --- تهيئة وتحديث قاعدة بيانات الأرشيف والتقارير المعتمدة ---
def init_attendance_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS approved_reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            approval_date TEXT NOT NULL,
            training_week TEXT NOT NULL,
            training_term TEXT NOT NULL,
            department TEXT NOT NULL,
            trainers_count INTEGER DEFAULT 0,
            trainers_attendance_rate REAL DEFAULT 0,
            trainees_count INTEGER DEFAULT 0,
            trainees_present_count INTEGER DEFAULT 0,
            trainees_attendance_rate REAL DEFAULT 0,
            sections_total INTEGER DEFAULT 0,
            sections_executed INTEGER DEFAULT 0,
            notes TEXT,
            full_data_json TEXT NOT NULL
        )
    ''')
    conn.commit()
    conn.close()

init_attendance_db()

# القائمة الثابتة بالخدمات للمتدربين
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

# --- مسارات الواجهة العامة ---

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

# --- لوحة التحكم والإدارة ---

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

# --- مسارات نظام متابعة التدريب والأرشيف والاعتماد ---

# 1. شاشة متابعة سير العملية التدريبية الرئيسية
@app.route('/admin/attendance')
def attendance_tracker():
    if not session.get("logged_in"):
        return redirect(url_for("admin"))
    return render_template('attendance_tracker.html', is_archived_view=False)

# 2. مسار اعتماد وحفظ التقرير (Snapshot)
@app.route('/admin/attendance/approve', methods=['POST'])
def approve_report():
    if not session.get("logged_in"):
        return jsonify({'success': False, 'message': 'غير مصرح بالدخول'}), 403
    try:
        data = request.get_json()
        if not data:
            return jsonify({'success': False, 'message': 'لا توجد بيانات صالحة'}), 400

        approval_date = datetime.now().strftime('%Y-%m-%d %H:%M')
        training_week = data.get('training_week', 'غير محدد')
        training_term = data.get('training_term', 'الفصل التدريبي الأول 1448')
        department = data.get('department', 'القسم التدريبي')
        notes = data.get('notes', '')

        kpis = data.get('kpis', {})
        trainers_count = int(kpis.get('trainers_count', 0))
        trainers_attendance_rate = float(kpis.get('trainers_attendance_rate', 0.0))
        trainees_count = int(kpis.get('trainees_count', 0))
        trainees_present_count = int(kpis.get('trainees_present_count', 0))
        trainees_attendance_rate = float(kpis.get('trainees_attendance_rate', 0.0))
        sections_total = int(kpis.get('sections_total', 0))
        sections_executed = int(kpis.get('sections_executed', 0))

        data['approval_date'] = approval_date
        full_json = json.dumps(data, ensure_ascii=False)

        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO approved_reports (
                approval_date, training_week, training_term, department,
                trainers_count, trainers_attendance_rate,
                trainees_count, trainees_present_count, trainees_attendance_rate,
                sections_total, sections_executed, notes, full_data_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            approval_date, training_week, training_term, department,
            trainers_count, trainers_attendance_rate,
            trainees_count, trainees_present_count, trainees_attendance_rate,
            sections_total, sections_executed, notes, full_json
        ))
        report_id = cursor.lastrowid
        conn.commit()
        conn.close()

        return jsonify({'success': True, 'report_id': report_id, 'message': 'تم اعتماد التقرير وأرشفته بنجاح'}), 200
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

# 3. شاشة سجل الأرشيف للتقارير المعتمدة (تضم الحقول الإحصائية الجديدة)
@app.route('/admin/attendance/archive')
def attendance_archive():
    if not session.get("logged_in"):
        return redirect(url_for("admin"))
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute('''
        SELECT id, approval_date, training_week, training_term, department,
               trainees_count, trainees_present_count, trainees_attendance_rate,
               trainers_attendance_rate, sections_total, sections_executed, notes
        FROM approved_reports
        ORDER BY id DESC
    ''')
    reports = cursor.fetchall()
    conn.close()
    return render_template('attendance_archive.html', reports=reports)

# 4. فتح واستعراض أي تقرير مؤرشف مسبقاً بكامل رسوماته ونموذجه الرسمي
@app.route('/admin/attendance/archive/<int:report_id>')
def get_archived_report(report_id):
    if not session.get("logged_in"):
        return redirect(url_for("admin"))
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute('SELECT full_data_json FROM approved_reports WHERE id = ?', (report_id,))
    row = cursor.fetchone()
    conn.close()
    if not row:
        return "التقرير غير موجود", 404
    return render_template('attendance_tracker.html', archived_json=row['full_data_json'], is_archived_view=True)

# 5. حذف تقرير معتمد من الأرشيف
@app.route('/admin/attendance/archive/<int:report_id>/delete', methods=['POST'])
def delete_archived_report(report_id):
    if not session.get("logged_in"):
        return redirect(url_for("admin"))
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('DELETE FROM approved_reports WHERE id = ?', (report_id,))
    conn.commit()
    conn.close()
    return redirect(url_for('attendance_archive'))

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
