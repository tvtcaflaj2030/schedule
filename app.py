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
TRAINERS_SCHEDULES_PDF = os.path.join(BASE_DIR, "trainers_schedules.pdf")
DB_PATH = os.path.join(BASE_DIR, "attendance_archive.db")

def normalize_digits(text):
    if not text:
        return ""
    arabic_digits = "٠١٢٣٤٥٦٧٨٩"
    english_digits = "0123456789"
    return str(text).translate(str.maketrans(arabic_digits, english_digits)).strip()

def clean_employee_id(text):
    digits = normalize_digits(text)
    return digits.lstrip('0')

def init_db():
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
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS trainers_roles (
            employee_id TEXT PRIMARY KEY,
            trainer_name TEXT NOT NULL,
            department TEXT DEFAULT '',
            role TEXT DEFAULT 'trainer'
        )
    ''')
    conn.commit()
    conn.close()

init_db()

SERVICES_LIST = [
    {"title": "الخدمات الذاتية للمتدربين (رايات)", "url": "https://tvtc.gov.sa/ar/Departments/tvtcdepartments/Rayat/pages/E-Services.aspx", "icon": "fa-user-gear"},
    {"title": "شرح اضافة الايبان", "url": "https://youtu.be/rTF7pRETF2A?si=4x6fBDz-oXKRSxKp", "icon": "fa-money-bill-transfer"},
    {"title": "عرض جدول المتدرب", "url": "/schedule", "icon": "fa-table-cells"},
    {"title": "طلب شهادة تعريف", "url": "https://tvtc.gov.sa/ar/Training-Units/Boys-Colleges/AQTC/Documents/%D8%A3%D8%AF%D9%84%D8%A9%20%D8%AE%D8%A7%D8%B5%D8%A9%20%D9%84%D9%84%D9%85%D8%AA%D8%AF%D8%B1%D8%A8%D9%8A%D9%86/%D8%A3%D8%AF%D9%84%D8%A9%20%D8%A8%D9%88%D8%A7%D8%A8%D8%A9%20%D8%B1%D8%A7%D9%8A%D8%AA%20%D9%84%D9%84%D9%85%D8%AA%D8%AF%D8%B1%D8%A8/%D8%AF%D9%84%D9%8A%D9%84%20%D8%B7%D9%84%D8%A8%20%D8%B4%D9%87%D8%A7%D8%AF%D8%A9%20%D8%A7%D9%84%D8%AA%D8%B9%D8%B1%D9%8A%D9%81%20%D8%B9%D9%86%20%D8%B7%D8%B1%D9%8A%D9%82%20%D8%B1%D8%A7%D9%8A%D8%AA.pdf", "icon": "fa-file-lines"},
    {"title": "التدرب عن بعد (تقني)", "url": "https://tvtclms.edu.sa/?ref=saudiwins.com", "icon": "fa-laptop-code"},
    {"title": "البريد الإلكتروني", "url": "https://outlook.office.com", "icon": "fa-envelope"}
]

TRAINER_SERVICES = [
    {"title": "نظام رايات للمدربين", "url": "https://tvtc.gov.sa/ar/Departments/tvtcdepartments/Rayat/pages/E-Services.aspx", "icon": "fa-chalkboard-user"},
    {"title": "منصة التدرب الإلكتروني (تقني)", "url": "https://tvtclms.edu.sa/?ref=saudiwins.com", "icon": "fa-laptop-code"},
    {"title": "البريد الإلكتروني", "url": "https://outlook.office.com", "icon": "fa-envelope"},
    {"title": "بوابة للموظفين", "url": "https://serv.tvtc.gov.sa/", "icon": "fa-id-card"}
]

def load_absence_dataframe():
    possible_files = [
        os.path.join(BASE_DIR, "absence_data.xlsx"),
        os.path.join(BASE_DIR, "absence_data.xls"),
        os.path.join(BASE_DIR, "absence_data.csv")
    ]
    for filepath in possible_files:
        if os.path.exists(filepath):
            try:
                if filepath.endswith(('.xlsx', '.xls')):
                    return pd.read_excel(filepath)
                else:
                    try:
                        return pd.read_csv(filepath, encoding='utf-8')
                    except UnicodeDecodeError:
                        return pd.read_csv(filepath, encoding='windows-1256')
            except Exception:
                continue
    return None

def load_so09_dataframe():
    possible_files = [
        os.path.join(BASE_DIR, "so09_data.xlsx"),
        os.path.join(BASE_DIR, "so09_data.xls"),
        os.path.join(BASE_DIR, "so09_data.csv")
    ]
    for filepath in possible_files:
        if os.path.exists(filepath):
            try:
                if filepath.endswith(('.xlsx', '.xls')):
                    return pd.read_excel(filepath)
                else:
                    try:
                        return pd.read_csv(filepath, encoding='utf-8')
                    except UnicodeDecodeError:
                        return pd.read_csv(filepath, encoding='windows-1256')
            except Exception:
                continue
    return None

def get_trainer_sections(emp_id):
    """استخراج قائمة الشعب المسندة للمدرب من واقع ملف SO09"""
    df_sec = load_so09_dataframe()
    if df_sec is None or df_sec.empty:
        return []
    clean_target = clean_employee_id(emp_id)
    df_sec['emp_clean'] = df_sec['رقم الحاسب'].astype(str).apply(clean_employee_id)
    matched = df_sec[df_sec['emp_clean'] == clean_target]
    if matched.empty:
        return []
    # تحويل الشعب إلى نصوص خالية من الأصفار والكسور
    sections = []
    for s in matched['رمز المقرر'].dropna():
        s_str = str(s).split('.')[0].strip()
        if s_str:
            sections.append(s_str)
    return list(set(sections))

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

def find_trainer_pages(pdf_path, search_term):
    if not search_term or not os.path.exists(pdf_path):
        return []
    term_clean = clean_employee_id(search_term)
    matched_pages = []
    try:
        doc = fitz.open(pdf_path)
        for page_num in range(len(doc)):
            text = doc[page_num].get_text()
            norm_text = normalize_digits(text)
            clean_text_no_zeros = re.sub(r'\b0+(\d+)\b', r'\1', norm_text)
            if term_clean in clean_text_no_zeros or search_term in text:
                matched_pages.append(page_num)
        return matched_pages
    except Exception:
        return []

# --- الواجهات العامة للطلاب ---
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

@app.route("/search_absence", methods=["POST"])
def search_absence():
    trainee_id = normalize_digits(request.form.get("trainee_id", "").strip())
    if len(trainee_id) < 9 or not trainee_id.isdigit():
        return jsonify({"success": False, "message": "يرجى إدخال الرقم التدريبي بشكل صحيح (9 أرقام)."})

    df = load_absence_dataframe()
    if df is None or df.empty:
        return jsonify({"success": False, "message": "لم يتم رفع أو تحديث بيانات الغياب من قبل الإدارة بعد."})

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
        return "معلمات غير صالحة أو الملف غير متوفر", 400

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

# --- بوابة المدربين والمسؤولين ---

@app.route("/trainer")
def trainer_login_page():
    return render_template("trainer_login.html")

@app.route("/trainer/login", methods=["POST"])
def trainer_login_action():
    emp_id = clean_employee_id(request.form.get("employee_id", ""))
    if not emp_id:
        return render_template("trainer_login.html", error="يرجى إدخال الرقم الوظيفي.")

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM trainers_roles WHERE employee_id = ?", (emp_id,))
    user = cursor.fetchone()
    conn.close()

    if not user:
        df_sec = load_so09_dataframe()
        found_trainer = None
        if df_sec is not None and not df_sec.empty:
            df_sec['emp_clean'] = df_sec['رقم الحاسب'].astype(str).apply(clean_employee_id)
            matched = df_sec[df_sec['emp_clean'] == emp_id]
            if not matched.empty:
                t_name = matched.iloc[0]['اسم المدرب']
                conn = sqlite3.connect(DB_PATH)
                c = conn.cursor()
                c.execute("INSERT OR REPLACE INTO trainers_roles (employee_id, trainer_name, role) VALUES (?, ?, 'trainer')", (emp_id, t_name))
                conn.commit()
                conn.close()
                found_trainer = {"employee_id": emp_id, "trainer_name": t_name, "role": "trainer"}

        if not found_trainer:
            return render_template("trainer_login.html", error="الرقم الوظيفي غير مسجل في النظام. تواصل مع الإدارة لتفعيل حسابك.")
        user = found_trainer

    session["trainer_id"] = emp_id
    session["trainer_name"] = user["trainer_name"]
    session["trainer_role"] = user["role"]
    return redirect(url_for("trainer_dashboard"))

@app.route("/trainer/dashboard")
def trainer_dashboard():
    if not session.get("trainer_id"):
        return redirect(url_for("trainer_login_page"))
    return render_template("trainer_dashboard.html",
                           trainer_id=session.get("trainer_id"),
                           trainer_name=session.get("trainer_name"),
                           trainer_role=session.get("trainer_role"),
                           services=TRAINER_SERVICES)

@app.route("/trainer/my_schedule_pdf")
def trainer_my_schedule_pdf():
    if not session.get("trainer_id"):
        return redirect(url_for("trainer_login_page"))

    emp_id = session.get("trainer_id")
    t_name = session.get("trainer_name", "")

    if not os.path.exists(TRAINERS_SCHEDULES_PDF):
        return """
        <div style='font-family:sans-serif; text-align:center; padding:50px; direction:rtl;'>
            <h2 style='color:#b91c1c;'>ملف جداول المدربين غير متوفر حالياً</h2>
            <p style='color:#64748b;'>يرجى من إدارة المعهد رفع ملف جداول المدربين (PDF) من لوحة التحكم.</p>
            <a href='/trainer/dashboard' style='display:inline-block; margin-top:15px; padding:10px 20px; background:#1e293b; color:#fff; text-decoration:none; border-radius:8px;'>العودة للوحة المدرب</a>
        </div>
        """, 404

    pages = find_trainer_pages(TRAINERS_SCHEDULES_PDF, emp_id)
    if not pages and t_name:
        pages = find_trainer_pages(TRAINERS_SCHEDULES_PDF, t_name)

    if not pages:
        return f"""
        <div style='font-family:sans-serif; text-align:center; padding:50px; direction:rtl;'>
            <h2 style='color:#b91c1c;'>لم يتم العثور على جدول مطابق</h2>
            <p style='color:#64748b;'>لم نتمكن من إيجاد صفحة الجدول للرقم الوظيفي ({emp_id}) أو الاسم ({t_name}) داخل الملف المرفوع.</p>
            <a href='/trainer/dashboard' style='display:inline-block; margin-top:15px; padding:10px 20px; background:#1e293b; color:#fff; text-decoration:none; border-radius:8px;'>العودة للوحة المدرب</a>
        </div>
        """, 404

    try:
        doc = fitz.open(TRAINERS_SCHEDULES_PDF)
        out_doc = fitz.open()
        for p in pages:
            if 0 <= p < len(doc):
                out_doc.insert_pdf(doc, from_page=p, to_page=p)

        pdf_bytes = io.BytesIO()
        out_doc.save(pdf_bytes)
        pdf_bytes.seek(0)
        return send_file(pdf_bytes, mimetype="application/pdf", download_name=f"جدول_المدرب_{emp_id}.pdf")
    except Exception as e:
        return str(e), 500

@app.route("/trainer/absence_view")
def trainer_absence_view():
    if not session.get("trainer_id"):
        return redirect(url_for("trainer_login_page"))

    emp_id = session.get("trainer_id")
    role = session.get("trainer_role")

    df_abs = load_absence_dataframe()
    departments = []
    courses = []

    if df_abs is not None and not df_abs.empty:
        if role == "trainer":
            assigned_sections = get_trainer_sections(emp_id)
            if assigned_sections:
                df_abs_copy = df_abs.copy()
                df_abs_copy['sec_clean'] = df_abs_copy['أرقام شعب المقرر'].astype(str).apply(lambda x: str(x).split('.')[0].strip())
                trainer_records = df_abs_copy[df_abs_copy['sec_clean'].isin(assigned_sections)]
                if 'اسم المقرر' in trainer_records.columns:
                    courses = sorted([c for c in trainer_records['اسم المقرر'].dropna().unique() if str(c).strip()])
        else:
            if 'اسم القسم' in df_abs.columns:
                departments = sorted([d for d in df_abs['اسم القسم'].dropna().unique() if str(d).strip()])
            if 'اسم المقرر' in df_abs.columns:
                courses = sorted([c for c in df_abs['اسم المقرر'].dropna().unique() if str(c).strip()])

    return render_template("trainer_absence_view.html",
                           trainer_name=session.get("trainer_name"),
                           trainer_role=role,
                           departments=departments,
                           courses=courses)

@app.route("/trainer/logout")
def trainer_logout():
    session.pop("trainer_id", None)
    session.pop("trainer_name", None)
    session.pop("trainer_role", None)
    return redirect(url_for("trainer_login_page"))

# API موحد ومحكم للفصل التام بين المدرب والأدمن
@app.route("/api/absence_records_query")
def api_absence_records_query():
    # التحقق من مصدر الطلب
    source = request.args.get("source", "") # 'trainer' or 'admin'
    is_admin = session.get("logged_in", False)
    trainer_id = session.get("trainer_id", None)
    trainer_role = session.get("trainer_role", "trainer")

    if source == "trainer" and not trainer_id:
        return jsonify({"success": False, "message": "غير مصرح للمدرب"}), 403
    elif source == "admin" and not is_admin:
        return jsonify({"success": False, "message": "غير مصرح للإدارة"}), 403
    elif not is_admin and not trainer_id:
        return jsonify({"success": False, "message": "غير مصرح"}), 403

    df = load_absence_dataframe()
    if df is None or df.empty:
        return jsonify({"success": False, "records": [], "stats": {}, "courses": []})

    filtered = df.copy()
    rate_col = 'إجمالي نسبة الغياب بعذر وبدون عذر'
    hours_col = 'إجمالي ساعات الغياب بعذر وبدون عذر'
    filtered['rate'] = pd.to_numeric(filtered[rate_col], errors='coerce').fillna(0.0)
    filtered['hours'] = pd.to_numeric(filtered[hours_col], errors='coerce').fillna(0.0)

    # إذا كان الطلب من بوابة المدرب وكان المدرب برتبة trainer عادية
    if source == "trainer" and trainer_role == "trainer":
        assigned_sections = get_trainer_sections(trainer_id)
        if assigned_sections:
            filtered['sec_clean'] = filtered['أرقام شعب المقرر'].astype(str).apply(lambda x: str(x).split('.')[0].strip())
            filtered = filtered[filtered['sec_clean'].isin(assigned_sections)]
        else:
            filtered = filtered.iloc[0:0] # لا توجد شعب مسندة في SO09

    dept_filter = request.args.get("department", "ALL")
    course_filter = request.args.get("course", "ALL")
    status_filter = request.args.get("status", "ALL")

    if dept_filter != "ALL" and 'اسم القسم' in filtered.columns:
        filtered = filtered[filtered['اسم القسم'] == dept_filter]

    # استخراج قائمة المقررات التابعة لهذا النطاق فقط
    available_courses = sorted([c for c in filtered['اسم المقرر'].dropna().unique() if str(c).strip()]) if 'اسم المقرر' in filtered.columns else []

    if course_filter != "ALL" and 'اسم المقرر' in filtered.columns:
        filtered = filtered[filtered['اسم المقرر'] == course_filter]

    total_records = len(filtered)
    danger_count = len(filtered[filtered['rate'] >= 20.0])
    warn2_count = len(filtered[(filtered['rate'] >= 15.0) & (filtered['rate'] < 20.0)])
    warn1_count = len(filtered[(filtered['rate'] >= 10.0) & (filtered['rate'] < 15.0)])
    safe_count = len(filtered[filtered['rate'] < 10.0])

    if status_filter == "danger":
        filtered = filtered[filtered['rate'] >= 20.0]
    elif status_filter == "warn2":
        filtered = filtered[(filtered['rate'] >= 15.0) & (filtered['rate'] < 20.0)]
    elif status_filter == "warn1":
        filtered = filtered[(filtered['rate'] >= 10.0) & (filtered['rate'] < 15.0)]
    elif status_filter == "safe":
        filtered = filtered[filtered['rate'] < 10.0]

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
        "courses": available_courses,
        "stats": {
            "total": total_records,
            "danger": danger_count,
            "warn2": warn2_count,
            "warn1": warn1_count,
            "safe": safe_count
        }
    })

# --- لوحة الإدارة الرئيسية ---

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
                msg = "تم رفع وتحديث ملف جداول المتدربين بنجاح!"
                msg_type = "success"

        elif action == "upload_trainers_schedule":
            if not session.get("logged_in"): return redirect(url_for("admin"))
            file = request.files.get("pdf_file")
            if file and file.filename.endswith(".pdf"):
                file.save(TRAINERS_SCHEDULES_PDF)
                msg = "تم رفع وتحديث ملف جداول المدربين بنجاح!"
                msg_type = "success"

        elif action == "upload_absence_file":
            if not session.get("logged_in"): return redirect(url_for("admin"))
            file = request.files.get("absence_file")
            if file and (file.filename.endswith(".csv") or file.filename.endswith(".xlsx") or file.filename.endswith(".xls")):
                ext = os.path.splitext(file.filename)[1]
                save_path = os.path.join(BASE_DIR, f"absence_data{ext}")
                file.save(save_path)
                msg = "تم رفع وتحديث ملف نسب الغياب بنجاح!"
                msg_type = "success"

        elif action == "upload_so09_file":
            if not session.get("logged_in"): return redirect(url_for("admin"))
            file = request.files.get("so09_file")
            if file and (file.filename.endswith(".csv") or file.filename.endswith(".xlsx") or file.filename.endswith(".xls")):
                ext = os.path.splitext(file.filename)[1]
                save_path = os.path.join(BASE_DIR, f"so09_data{ext}")
                file.save(save_path)
                df_sec = load_so09_dataframe()
                if df_sec is not None and not df_sec.empty:
                    conn = sqlite3.connect(DB_PATH)
                    c = conn.cursor()
                    for _, row in df_sec.iterrows():
                        raw_emp = str(row.get('رقم الحاسب', ''))
                        clean_emp = clean_employee_id(raw_emp)
                        t_name = str(row.get('اسم المدرب', '')).strip()
                        if clean_emp and t_name:
                            c.execute("INSERT OR IGNORE INTO trainers_roles (employee_id, trainer_name, role) VALUES (?, ?, 'trainer')", (clean_emp, t_name))
                    conn.commit()
                    conn.close()
                msg = "تم رفع ملف الشعب (SO09) واستيراد قائمة المدربين تلقائياً!"
                msg_type = "success"

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM trainers_roles ORDER BY role DESC, trainer_name ASC")
    trainers_list = cursor.fetchall()
    conn.close()

    return render_template("admin.html", 
                           logged_in=session.get("logged_in", False), 
                           trainers_list=trainers_list,
                           msg=msg, 
                           msg_type=msg_type)

@app.route("/admin/trainer_role/update", methods=["POST"])
def update_trainer_role():
    if not session.get("logged_in"):
        return jsonify({"success": False, "message": "غير مصرح"}), 403
    emp_id = clean_employee_id(request.form.get("employee_id", ""))
    new_role = request.form.get("role", "trainer")
    if not emp_id:
        return jsonify({"success": False, "message": "رقم المدرب مفقود"})

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("UPDATE trainers_roles SET role = ? WHERE employee_id = ?", (new_role, emp_id))
    conn.commit()
    conn.close()
    return jsonify({"success": True})

@app.route("/admin/trainer_role/add", methods=["POST"])
def add_trainer_role():
    if not session.get("logged_in"):
        return redirect(url_for("admin"))
    emp_id = clean_employee_id(request.form.get("employee_id", ""))
    t_name = request.form.get("trainer_name", "").strip()
    role = request.form.get("role", "trainer")
    if emp_id and t_name:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("INSERT OR REPLACE INTO trainers_roles (employee_id, trainer_name, role) VALUES (?, ?, ?)", (emp_id, t_name, role))
        conn.commit()
        conn.close()
    return redirect(url_for("admin"))

@app.route("/admin/trainer_role/delete/<emp_id>", methods=["POST"])
def delete_trainer_role(emp_id):
    if not session.get("logged_in"):
        return redirect(url_for("admin"))
    clean_id = clean_employee_id(emp_id)
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("DELETE FROM trainers_roles WHERE employee_id = ?", (clean_id,))
    conn.commit()
    conn.close()
    return redirect(url_for("admin"))

@app.route("/admin/logout")
def admin_logout():
    session.pop("logged_in", None)
    return redirect(url_for("admin"))

@app.route("/admin/absence_dashboard")
def absence_dashboard():
    if not session.get("logged_in"):
        return redirect(url_for("admin"))
    df = load_absence_dataframe()
    departments = []
    courses = []
    if df is not None and not df.empty:
        if 'اسم القسم' in df.columns:
            departments = sorted([d for d in df['اسم القسم'].dropna().unique() if str(d).strip()])
        if 'اسم المقرر' in df.columns:
            courses = sorted([c for c in df['اسم المقرر'].dropna().unique() if str(c).strip()])

    return render_template("admin_absence_dashboard.html",
                           has_data=df is not None and not df.empty,
                           departments=departments,
                           courses=courses)

@app.route('/admin/attendance')
def attendance_tracker():
    if not session.get("logged_in"):
        return redirect(url_for("admin"))
    return render_template('attendance_tracker.html', is_archived_view=False)

@app.route('/admin/attendance/approve', methods=['POST'])
def approve_report():
    if not session.get("logged_in"):
        return jsonify({'success': False, 'message': 'غير مصرح'}), 403
    try:
        data = request.get_json()
        approval_date = datetime.now().strftime('%Y-%m-%d %H:%M')
        training_week = data.get('training_week', 'غير محدد')
        training_term = data.get('training_term', 'الفصل الأول 1448')
        department = data.get('department', 'القسم التدريبي')
        notes = data.get('notes', '')
        kpis = data.get('kpis', {})
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
            int(kpis.get('trainers_count', 0)), float(kpis.get('trainers_attendance_rate', 0.0)),
            int(kpis.get('trainees_count', 0)), int(kpis.get('trainees_present_count', 0)), float(kpis.get('trainees_attendance_rate', 0.0)),
            int(kpis.get('sections_total', 0)), int(kpis.get('sections_executed', 0)), notes, full_json
        ))
        conn.commit()
        conn.close()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/admin/attendance/archive')
def attendance_archive():
    if not session.get("logged_in"):
        return redirect(url_for("admin"))
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM approved_reports ORDER BY id DESC')
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
