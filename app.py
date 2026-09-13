import io
import re
import os
import json
import sqlite3
from datetime import datetime
import fitz  # PyMuPDF
import pandas as pd
from flask import Flask, render_template, request, send_file, jsonify, redirect, url_for, session

app = Flask(__name__)
app.secret_key = "secret_admin_key_turki_aflaj"
ADMIN_PASSWORD = "turki2026"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SCHEDULES_PDF = os.path.join(BASE_DIR, "schedules.pdf")
ATTENDANCE_DATA_FILE = os.path.join(BASE_DIR, "absence_data.csv")
DB_PATH = os.path.join(BASE_DIR, "attendance_archive.db")

# --- تهيئة قاعدة بيانات الأرشيف والتقارير المعتمدة ---
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

# القائمة الثابتة للخدمات الطلابية
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
    return str(text).translate(str.maketrans(arabic_digits, english_digits))

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

# دالة مساعدة لقراءة ملف الغياب سواء كان CSV أو Excel
def load_absence_dataframe():
    if not os.path.exists(ATTENDANCE_DATA_FILE):
        return None
    try:
        if ATTENDANCE_DATA_FILE.endswith(('.xlsx', '.xls')):
            return pd.read_excel(ATTENDANCE_DATA_FILE)
        else:
            try:
                return pd.read_csv(ATTENDANCE_DATA_FILE, encoding='utf-8')
            except UnicodeDecodeError:
                return pd.read_csv(ATTENDANCE_DATA_FILE, encoding='windows-1256')
    except Exception:
        return None

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

# استعلام المتدرب عن الغياب والإنذارات والحرمان
@app.route("/search_absence", methods=["POST"])
def search_absence():
    trainee_id = normalize_digits(request.form.get("trainee_id", "").strip())
    if len(trainee_id) < 9 or not trainee_id.isdigit():
        return jsonify({"success": False, "message": "يرجى إدخال الرقم التدريبي بشكل صحيح (9 أرقام)."})

    df = load_absence_dataframe()
    if df is None or df.empty:
        return jsonify({"success": False, "message": "لم يتم رفع أو تحديث بيانات الغياب من قبل الإدارة بعد."})

    # مطابقة رقم المتدرب
    df['رقم_نظيف'] = df['رقم المتدرب'].astype(str).apply(lambda x: normalize_digits(x).strip())
    matched = df[df['رقم_نظيف'] == trainee_id]

    if matched.empty:
        return jsonify({"success": False, "message": "لم يتم العثور على سجلات غياب مطابقة لهذا الرقم التدريبي."})

    trainee_name = matched.iloc[0]['اسم المتدرب']
    department = matched.iloc[0]['اسم القسم'] if 'اسم القسم' in matched.columns else ''
    program = matched.iloc[0]['اسم البرنامج'] if 'اسم البرنامج' in matched.columns else ''

    courses = []
    max_rate = 0.0

    for _, row in matched.iterrows():
        course_name = row['اسم المقرر']
        section_no = row['أرقام شعب المقرر'] if 'أرقام شعب المقرر' in row else ''
        
        # الاعتماد على إجمالي نسبة وساعات الغياب بعذر وبدون عذر
        raw_rate = row.get('إجمالي نسبة الغياب بعذر وبدون عذر', 0)
        raw_hours = row.get('إجمالي ساعات الغياب بعذر وبدون عذر', 0)
        
        try:
            rate = float(raw_rate)
        except (ValueError, TypeError):
            rate = 0.0
            
        try:
            hours = float(raw_hours)
        except (ValueError, TypeError):
            hours = 0.0

        if rate > max_rate:
            max_rate = rate

        # تصنيف الحالة
        if rate >= 20.0:
            status_text = "محروم نظاماً"
            status_color = "danger"
        elif rate >= 15.0:
            status_text = "إنذار ثانٍ (خطر حرمان)"
            status_color = "warning-high"
        elif rate >= 10.0:
            status_text = "إنذار أول"
            status_color = "warning-low"
        else:
            status_text = "مستمر (وضع آمن)"
            status_color = "safe"

        courses.append({
            "course_name": course_name,
            "section_no": str(section_no),
            "absence_rate": round(rate, 2),
            "absence_hours": round(hours, 1),
            "status_text": status_text,
            "status_color": status_color
        })

    return jsonify({
        "success": True,
        "trainee_name": trainee_name,
        "trainee_id": trainee_id,
        "department": department,
        "program": program,
        "max_rate": round(max_rate, 2),
        "courses": courses
    })

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

        elif action == "upload_absence_file":
            if not session.get("logged_in"): return redirect(url_for("admin"))
            file = request.files.get("absence_file")
            if file and (file.filename.endswith(".csv") or file.filename.endswith(".xlsx") or file.filename.endswith(".xls")):
                ext = os.path.splitext(file.filename)[1]
                save_path = os.path.join(BASE_DIR, f"absence_data{ext}")
                file.save(save_path)
                global ATTENDANCE_DATA_FILE
                ATTENDANCE_DATA_FILE = save_path
                msg = "تم رفع وتحديث ملف نسب الغياب بنجاح، والنظام جاهز لفرز الكشوفات!"
                msg_type = "success"

    return render_template("admin.html", 
                           logged_in=session.get("logged_in", False), 
                           msg=msg, 
                           msg_type=msg_type)

@app.route("/admin/logout")
def admin_logout():
    session.pop("logged_in", None)
    return redirect(url_for("admin"))

# شاشة الإدارة المخصصة لفرز وسحب كشوفات الغياب والحرمان
@app.route("/admin/absence_dashboard")
def absence_dashboard():
    if not session.get("logged_in"):
        return redirect(url_for("admin"))

    df = load_absence_dataframe()
    if df is None or df.empty:
        return render_template("admin_absence_dashboard.html", has_data=False, departments=[])

    # استخراج قائمة الأقسام
    departments = sorted([d for d in df['اسم القسم'].dropna().unique() if str(d).strip()])

    return render_template("admin_absence_dashboard.html", has_data=True, departments=departments)

# API جلب البيانات المفروزة لجدول الإدارة وتصديرها
@app.route("/admin/api/absence_records")
def api_absence_records():
    if not session.get("logged_in"):
        return jsonify({"success": False, "message": "غير مصرح"}), 403

    df = load_absence_dataframe()
    if df is None or df.empty:
        return jsonify({"success": False, "records": [], "stats": {}})

    dept = request.args.get("department", "ALL")
    status_filter = request.args.get("status", "ALL")

    filtered = df.copy()

    # تحويل نسب وساعات الغياب لأرقام
    rate_col = 'إجمالي نسبة الغياب بعذر وبدون عذر'
    hours_col = 'إجمالي ساعات الغياب بعذر وبدون عذر'

    filtered['rate'] = pd.to_numeric(filtered[rate_col], errors='coerce').fillna(0.0)
    filtered['hours'] = pd.to_numeric(filtered[hours_col], errors='coerce').fillna(0.0)

    # فلترة القسم
    if dept != "ALL":
        filtered = filtered[filtered['اسم القسم'] == dept]

    # حساب الإحصائيات قبل فلترة الحالة
    total_records = len(filtered)
    danger_count = len(filtered[filtered['rate'] >= 20.0])
    warn2_count = len(filtered[(filtered['rate'] >= 15.0) & (filtered['rate'] < 20.0)])
    warn1_count = len(filtered[(filtered['rate'] >= 10.0) & (filtered['rate'] < 15.0)])
    safe_count = len(filtered[filtered['rate'] < 10.0])

    # تطبيق فلترة الحالة
    if status_filter == "danger":
        filtered = filtered[filtered['rate'] >= 20.0]
    elif status_filter == "warn2":
        filtered = filtered[(filtered['rate'] >= 15.0) & (filtered['rate'] < 20.0)]
    elif status_filter == "warn1":
        filtered = filtered[(filtered['rate'] >= 10.0) & (filtered['rate'] < 15.0)]
    elif status_filter == "safe":
        filtered = filtered[filtered['rate'] < 10.0]

    # ترتيب الأكثر غياباً أولاً
    filtered = filtered.sort_values(by='rate', ascending=False)

    records = []
    for _, row in filtered.iterrows():
        rate = float(row['rate'])
        if rate >= 20.0:
            st_text = "محروم (20%+)"
            st_badge = "danger"
        elif rate >= 15.0:
            st_text = "إنذار ثانٍ (15-20%)"
            st_badge = "warn2"
        elif rate >= 10.0:
            st_text = "إنذار أول (10-15%)"
            st_badge = "warn1"
        else:
            st_text = "مستمر طبيعي"
            st_badge = "safe"

        records.append({
            "trainee_id": str(row.get('رقم المتدرب', '')),
            "trainee_name": str(row.get('اسم المتدرب', '')),
            "department": str(row.get('اسم القسم', '')),
            "course_name": str(row.get('اسم المقرر', '')),
            "section_no": str(row.get('أرقام شعب المقرر', '')),
            "absence_rate": round(rate, 2),
            "absence_hours": round(float(row['hours']), 1),
            "status_text": st_text,
            "status_badge": st_badge
        })

    return jsonify({
        "success": True,
        "records": records,
        "stats": {
            "total": total_records,
            "danger": danger_count,
            "warn2": warn2_count,
            "warn1": warn1_count,
            "safe": safe_count
        }
    })

# مسارات سير العملية التدريبية والأرشيف
@app.route('/admin/attendance')
def attendance_tracker():
    if not session.get("logged_in"):
        return redirect(url_for("admin"))
    return render_template('attendance_tracker.html', is_archived_view=False)

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
