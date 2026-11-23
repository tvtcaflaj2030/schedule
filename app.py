import io
import re
import os
import time
import json
import shutil
from datetime import datetime, timedelta, timezone
import fitz  # PyMuPDF
import pandas as pd
from flask import Flask, render_template, request, send_file, jsonify, redirect, url_for, session

app = Flask(__name__)

# --- 1. إعدادات الأمان ومتغيرات البيئة ---
app.secret_key = os.environ.get("SECRET_KEY", "secret_admin_key_turki_aflaj")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", ".").strip()

app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    PERMANENT_SESSION_LIFETIME=timedelta(hours=8)
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# --- 2. إعداد مسارات OneDrive المؤسسي (مزامنة مباشرة بدون قواعد بيانات) ---
def resolve_onedrive_data_dir():
    # الأولوية لمتغير البيئة إذا تم تحديده يدوياً
    custom_dir = os.environ.get("ONEDRIVE_DATA_DIR", "").strip()
    if custom_dir and os.path.exists(custom_dir):
        return custom_dir

    # البحث التلقائي عن مجلد OneDrive المؤسسي في نظام ويندوز
    user_profile = os.environ.get("USERPROFILE", "")
    if user_profile and os.path.exists(user_profile):
        for folder_name in os.listdir(user_profile):
            if folder_name.lower().startswith("onedrive"):
                candidate = os.path.join(user_profile, folder_name, "TVTC_Portal_Data")
                return candidate

    # مسار احتياطي داخل مجلد المشروع في حال لم يتوفر OneDrive على الجهاز
    return os.path.join(BASE_DIR, "TVTC_Portal_Data")

DATA_ROOT = resolve_onedrive_data_dir()
DIR_CURRENT = os.path.join(DATA_ROOT, "01_Current")
DIR_REPORTS = os.path.join(DATA_ROOT, "02_Admin_Reports")
DIR_CONFIG = os.path.join(DATA_ROOT, "03_Config")

for d in (DIR_CURRENT, DIR_REPORTS, DIR_CONFIG):
    os.makedirs(d, exist_ok=True)

SCHEDULES_PDF = os.path.join(DIR_CURRENT, "schedules.pdf")
TRAINERS_SCHEDULES_PDF = os.path.join(DIR_CURRENT, "trainers_schedules.pdf")
STAFF_ROLES_XLSX = os.path.join(DIR_CONFIG, "staff_roles.xlsx")
META_JSON_PATH = os.path.join(DIR_CONFIG, "system_meta.json")

# توقيت المملكة العربية السعودية (UTC+3)
KSA_TZ = timezone(timedelta(hours=3))

ROLE_LABELS = {
    "general_admin": "مسؤول عام",
    "manager": "مسؤول عام",
    "vp": "وكيل شؤون المتدربين",
    "counselor": "مرشد طلابي",
    "head_of_dept": "رئيس قسم",
    "trainer": "مدرب"
}

FULL_ACCESS_ROLES = ("general_admin", "manager", "vp", "counselor")
SINGLETON_ROLES = ("general_admin", "manager", "vp", "counselor")

def get_role_label(role, dept=""):
    base = ROLE_LABELS.get(role, "مدرب")
    if role == "head_of_dept" and dept:
        return f"رئيس قسم ({dept})"
    return base

def get_ksa_now_formatted():
    now_ksa = datetime.now(KSA_TZ)
    period = "مساءً" if now_ksa.hour >= 12 else "صباحاً"
    hour_12 = now_ksa.hour % 12 or 12
    return f"{now_ksa.strftime('%Y/%m/%d')} الساعة {hour_12:02d}:{now_ksa.minute:02d} {period}"

# --- 3. إدارة الإعدادات والصلاحيات عبر ملفات OneDrive ---
def set_meta_value(key, value):
    meta = {}
    if os.path.exists(META_JSON_PATH):
        try:
            with open(META_JSON_PATH, "r", encoding="utf-8") as f:
                meta = json.load(f)
        except Exception:
            meta = {}
    meta[key] = value
    with open(META_JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

def get_meta_value(key, default=""):
    if os.path.exists(META_JSON_PATH):
        try:
            with open(META_JSON_PATH, "r", encoding="utf-8") as f:
                meta = json.load(f)
            return meta.get(key, default)
        except Exception:
            pass
    return default

def get_absence_last_update_text():
    return get_meta_value("absence_last_update", "")

def load_staff_roles():
    cols = ["employee_id", "trainer_name", "department", "role"]
    if not os.path.exists(STAFF_ROLES_XLSX):
        df_empty = pd.DataFrame(columns=cols)
        df_empty.to_excel(STAFF_ROLES_XLSX, index=False)
        return []
    try:
        df = pd.read_excel(STAFF_ROLES_XLSX, dtype=str).fillna("")
        for c in cols:
            if c not in df.columns:
                df[c] = ""
        df["employee_id"] = df["employee_id"].apply(clean_employee_id)
        df = df[df["employee_id"] != ""]
        return df[cols].to_dict(orient="records")
    except Exception as e:
        print("Error reading staff_roles.xlsx:", e)
        return []

def save_staff_roles(records):
    cols = ["employee_id", "trainer_name", "department", "role"]
    df = pd.DataFrame(records, columns=cols) if records else pd.DataFrame(columns=cols)
    df.to_excel(STAFF_ROLES_XLSX, index=False)

def get_staff_by_id(emp_id):
    clean_id = clean_employee_id(emp_id)
    for r in load_staff_roles():
        if r["employee_id"] == clean_id:
            return r
    return None

def assign_staff_role_with_singleton_check(emp_id, trainer_name, new_role, new_dept="", force_replace=False):
    """
    تطبيق قاعدة منع ازدواج المناصب الإدارية الفردية:
    - المناصب العامة الفردية (vp, counselor, general_admin) لا يشغلها إلا شخص واحد.
    - منصب رئيس قسم (head_of_dept) لا يشغله إلا شخص واحد لكل قسم على حدة.
    - إذا كان المنصب مشغولاً ولم يتم التأكيد (force_replace=False)، يعيد تحذيراً باسم الشاغل الحالي.
    """
    clean_id = clean_employee_id(emp_id)
    new_dept = (new_dept or "").strip()
    if new_role != "head_of_dept":
        new_dept = ""

    records = load_staff_roles()
    conflict_user = None

    for r in records:
        if r["employee_id"] == clean_id:
            continue
        r_role = r.get("role", "trainer")
        r_dept = (r.get("department") or "").strip()

        if new_role in SINGLETON_ROLES and r_role in SINGLETON_ROLES:
            if (new_role in ("general_admin", "manager") and r_role in ("general_admin", "manager")) or (new_role == r_role):
                conflict_user = r
                break
        elif new_role == "head_of_dept" and new_dept and r_role == "head_of_dept" and r_dept == new_dept:
            conflict_user = r
            break

    if conflict_user and not force_replace:
        role_title = get_role_label(new_role, new_dept)
        return False, f"تنبيه: منصب ({role_title}) مسند حالياً للموظف ({conflict_user['trainer_name']}). هل تريد نقل المنصب إلى الموظف الجديد وإعادة السابق إلى صفة مدرب؟", conflict_user

    # تنفيذ الإحلال التلقائي وإعادة الشاغل السابق إلى مدرب
    if conflict_user and force_replace:
        for r in records:
            if r["employee_id"] == conflict_user["employee_id"]:
                r["role"] = "trainer"
                r["department"] = ""

    updated = False
    for r in records:
        if r["employee_id"] == clean_id:
            if trainer_name:
                r["trainer_name"] = trainer_name
            r["role"] = new_role
            r["department"] = new_dept
            updated = True
            break

    if not updated and trainer_name:
        records.append({
            "employee_id": clean_id,
            "trainer_name": trainer_name,
            "department": new_dept,
            "role": new_role
        })

    save_staff_roles(records)
    return True, "تم تحديث الصلاحية بنجاح.", None

# --- 4. نظام الحماية من تخمين كلمات المرور ---
_LOGIN_ATTEMPTS = {}
MAX_ATTEMPTS = 5
LOCKOUT_SECONDS = 600

def is_rate_limited(ip):
    now = time.time()
    record = _LOGIN_ATTEMPTS.get(ip)
    if not record:
        return False
    attempts, last_time = record
    if now - last_time > LOCKOUT_SECONDS:
        _LOGIN_ATTEMPTS.pop(ip, None)
        return False
    return attempts >= MAX_ATTEMPTS

def record_failed_attempt(ip):
    now = time.time()
    attempts, last_time = _LOGIN_ATTEMPTS.get(ip, (0, now))
    if now - last_time > LOCKOUT_SECONDS:
        attempts = 0
    _LOGIN_ATTEMPTS[ip] = (attempts + 1, now)

def clear_failed_attempts(ip):
    _LOGIN_ATTEMPTS.pop(ip, None)

# --- 5. التخزين المؤقت الذكي لقراءة ملفات OneDrive ---
_DATAFRAME_CACHE = {
    "absence": {"mtime": None, "path": None, "df": None},
    "so09": {"mtime": None, "path": None, "df": None}
}

_PDF_INDEX_CACHE = {
    SCHEDULES_PDF: {"mtime": None, "pages_norm": []},
    TRAINERS_SCHEDULES_PDF: {"mtime": None, "pages_raw": [], "pages_no_zeros": []}
}

def invalidate_caches():
    for key in _DATAFRAME_CACHE:
        _DATAFRAME_CACHE[key] = {"mtime": None, "path": None, "df": None}
    for pdf_path in _PDF_INDEX_CACHE:
        _PDF_INDEX_CACHE[pdf_path]["mtime"] = None

def normalize_digits(text):
    if not text:
        return ""
    arabic_digits = "٠١٢٣٤٥٦٧٨٩"
    english_digits = "0123456789"
    return str(text).translate(str.maketrans(arabic_digits, english_digits)).strip()

def clean_employee_id(text):
    digits = normalize_digits(text)
    return digits.lstrip('0')

def extract_row_course_status(row):
    for col in ['حالة المقرر', 'حالة المتدرب', 'الحالة']:
        if col in row and pd.notna(row[col]):
            val = str(row[col]).strip()
            if val and val.lower() != 'nan':
                return val
    return ""

def is_folded_or_deprived_text(status_str):
    if not status_str:
        return False
    keywords = ["مطوي", "طي قيد", "قيده", "محروم", "حرمان", "منقطع", "انقطاع", "إنقطاع", "مفصول"]
    return any(k in status_str for k in keywords)

def compute_remaining_hours(rate, hours, row):
    """
    حساب الساعات المتبقية للمتدرب قبل الوصول لنسبة الحرمان (20%).
    يستخرج إجمالي ساعات المقرر من العمود المباشر إن وجد، أو يستنتجها من (ساعات الغياب / نسبة الغياب).
    """
    total_course_hours = 0.0
    for col in ['إجمالي ساعات المقرر', 'ساعات المقرر', 'الساعات المعتمدة', 'إجمالي الساعات']:
        if col in row and pd.notna(row[col]):
            try:
                val = float(row[col])
                if val > 0:
                    total_course_hours = val
                    break
            except (ValueError, TypeError):
                pass

    if total_course_hours <= 0 and rate > 0 and hours > 0:
        total_course_hours = round((hours * 100.0) / rate)

    if total_course_hours > 0:
        max_allowed_hours = total_course_hours * 0.20
        remaining = max(0.0, round(max_allowed_hours - hours, 1))
        return remaining, round(max_allowed_hours, 1)

    return None, None

SERVICES_LIST = [
    {"title": "الخدمات الذاتية للمتدربين (رايات)", "url": "https://tvtc.gov.sa/ar/Departments/tvtcdepartments/Rayat/pages/E-Services.aspx", "icon": "fa-user-gear"},
    {"title": "شرح اضافة الايبان", "url": "https://youtu.be/rTF7pRETF2A?si=4x6fBDz-oXKRSxKp", "icon": "fa-money-bill-transfer"},
    {"title": "عرض جدول المتدرب", "url": "/schedule", "icon": "fa-table-cells"},
    {"title": "طلب شهادة تعريف", "url": "https://mttvtcedu-my.sharepoint.com/:b:/g/personal/turki_a4_tvtc_gov_sa/IQA2Gb2Q9NNZTq_Hx5qOJWYkAUlCJmtt6aUFNpaymdYv9Mc?e=QomvaC", "icon": "fa-file-lines"},
    {"title": "التدرب عن بعد (تقني)", "url": "https://tvtclms.edu.sa/?ref=saudiwins.com", "icon": "fa-laptop-code"},
    {"title": "البريد الإلكتروني", "url": "https://outlook.office.com", "icon": "fa-envelope"}
]

TRAINER_SERVICES = [
    {"title": "نظام رايات للمدربين", "url": "https://tvtc.gov.sa/ar/Departments/tvtcdepartments/Rayat/pages/E-Services.aspx", "icon": "fa-chalkboard-user"},
    {"title": "منصة التدرب الإلكتروني (تقني)", "url": "https://tvtclms.edu.sa/?ref=saudiwins.com", "icon": "fa-laptop-code"},
    {"title": "البريد الإلكتروني", "url": "https://outlook.office.com", "icon": "fa-envelope"},
    {"title": "بوابة للموظفين", "url": "https://serv.tvtc.gov.sa/", "icon": "fa-id-card"}
]

def _load_cached_dataframe(cache_key, base_filename):
    possible_exts = ['.xlsx', '.xls', '.csv']
    for ext in possible_exts:
        filepath = os.path.join(DIR_CURRENT, f"{base_filename}{ext}")
        if os.path.exists(filepath):
            try:
                mtime = os.path.getmtime(filepath)
                cache_entry = _DATAFRAME_CACHE[cache_key]
                if cache_entry["path"] == filepath and cache_entry["mtime"] == mtime and cache_entry["df"] is not None:
                    return cache_entry["df"].copy()

                if filepath.endswith(('.xlsx', '.xls')):
                    df = pd.read_excel(filepath)
                else:
                    try:
                        df = pd.read_csv(filepath, encoding='utf-8-sig')
                    except UnicodeDecodeError:
                        df = pd.read_csv(filepath, encoding='windows-1256')

                df.columns = [str(c).replace('﻿', '').strip() for c in df.columns]

                if cache_key == "absence" and 'رقم المتدرب' in df.columns:
                    df['رقم_نظيف'] = df['رقم المتدرب'].astype(str).apply(lambda x: normalize_digits(x).split('.')[0].strip())
                if cache_key == "so09" and 'رقم الحاسب' in df.columns:
                    df['emp_clean'] = df['رقم الحاسب'].astype(str).apply(clean_employee_id)

                _DATAFRAME_CACHE[cache_key] = {"mtime": mtime, "path": filepath, "df": df}
                return df.copy()
            except Exception as e:
                print(f"Error loading {filepath}:", e)
                continue
    return None

def load_absence_dataframe():
    return _load_cached_dataframe("absence", "absence_data")

def load_so09_dataframe():
    return _load_cached_dataframe("so09", "so09_data")

def get_trainer_sections(emp_id):
    df_sec = load_so09_dataframe()
    if df_sec is None or df_sec.empty:
        return []
    clean_target = clean_employee_id(emp_id)
    if 'emp_clean' not in df_sec.columns:
        df_sec['emp_clean'] = df_sec['رقم الحاسب'].astype(str).apply(clean_employee_id)
    matched = df_sec[df_sec['emp_clean'] == clean_target]
    if matched.empty:
        return []
    sections = []
    for s in matched['رمز المقرر'].dropna():
        s_str = str(s).split('.')[0].strip()
        if s_str:
            sections.append(s_str)
    return list(set(sections))

def _ensure_student_pdf_index(pdf_path):
    if not os.path.exists(pdf_path):
        return []
    mtime = os.path.getmtime(pdf_path)
    cache = _PDF_INDEX_CACHE.setdefault(pdf_path, {"mtime": None, "pages_norm": []})
    if cache["mtime"] == mtime and cache["pages_norm"]:
        return cache["pages_norm"]
    pages_norm = []
    with fitz.open(pdf_path) as doc:
        for page_num in range(len(doc)):
            pages_norm.append(normalize_digits(doc[page_num].get_text()))
    cache["mtime"] = mtime
    cache["pages_norm"] = pages_norm
    return pages_norm

def find_student_pages(pdf_path, trainee_id):
    clean_id = normalize_digits(trainee_id).strip()
    if len(clean_id) < 9 or not clean_id.isdigit():
        return []
    id_pattern = re.compile(rf'(?<!\d){re.escape(clean_id)}(?!\d)|(?<!\d){re.escape(clean_id[::-1])}(?!\d)')
    try:
        pages_norm = _ensure_student_pdf_index(pdf_path)
        return [idx for idx, text in enumerate(pages_norm) if id_pattern.search(text)]
    except Exception:
        return []

def find_trainer_pages(pdf_path, search_term):
    if not search_term or not os.path.exists(pdf_path):
        return []
    term_clean = clean_employee_id(search_term)
    try:
        mtime = os.path.getmtime(pdf_path)
        cache = _PDF_INDEX_CACHE.setdefault(pdf_path, {"mtime": None, "pages_raw": [], "pages_no_zeros": []})
        if cache["mtime"] != mtime or not cache.get("pages_raw"):
            pages_raw, pages_no_zeros = [], []
            with fitz.open(pdf_path) as doc:
                for page_num in range(len(doc)):
                    raw_t = doc[page_num].get_text()
                    norm_t = normalize_digits(raw_t)
                    no_zeros_t = re.sub(r'0+(\d+)', r'', norm_t)
                    pages_raw.append(raw_t)
                    pages_no_zeros.append(no_zeros_t)
            cache["mtime"] = mtime
            cache["pages_raw"] = pages_raw
            cache["pages_no_zeros"] = pages_no_zeros

        matched_pages = []
        for idx in range(len(cache["pages_raw"])):
            if (term_clean and term_clean in cache["pages_no_zeros"][idx]) or (search_term in cache["pages_raw"][idx]):
                matched_pages.append(idx)
        return matched_pages
    except Exception:
        return []

# --- 6. توليد تقارير الحضور الأسبوعية بصيغة PDF وحفظها في مجلد الإدارة في OneDrive ---
def generate_weekly_admin_pdf(data, pdf_filepath):
    """
    توليد تقرير حضور أسبوعي رسمي بصيغة PDF باستخدام PyMuPDF (HTML-to-PDF)
    وحفظه في مجلد 02_Admin_Reports داخل OneDrive للإدارة فقط.
    """
    kpis = data.get("kpis", {})
    training_week = data.get("training_week", "غير محدد")
    training_term = data.get("training_term", "الفصل التدريبي")
    department = data.get("department", "جميع الأقسام")
    approval_date = data.get("approval_date", get_ksa_now_formatted())
    notes = data.get("notes", "لا توجد ملاحظات إضافية.")
    details = data.get("details", [])

    rows_html = ""
    if isinstance(details, list) and details:
        for idx, item in enumerate(details, 1):
            if isinstance(item, dict):
                col1 = item.get("name") or item.get("department") or item.get("course") or f"بند {idx}"
                col2 = item.get("rate") or item.get("attendance_rate") or "-"
                col3 = item.get("status") or item.get("notes") or "-"
                rows_html += f"<tr><td>{idx}</td><td>{col1}</td><td>{col2}</td><td>{col3}</td></tr>"

    table_section = ""
    if rows_html:
        table_section = f"""
        <h3>تفاصيل المؤشرات والبيانات المسجلة</h3>
        <table>
            <thead>
                <tr><th>#</th><th>البيان / القسم</th><th>النسبة / القيمة</th><th>ملاحظات</th></tr>
            </thead>
            <tbody>{rows_html}</tbody>
        </table>
        """

    html_content = f"""
    <!DOCTYPE html>
    <html lang="ar" dir="rtl">
    <head>
        <meta charset="UTF-8">
        <style>
            body {{ font-family: Arial, Tahoma, sans-serif; direction: rtl; color: #1e293b; padding: 20px; }}
            .header {{ text-align: center; border-bottom: 2px solid #065f46; padding-bottom: 12px; margin-bottom: 20px; }}
            .header h1 {{ margin: 0; font-size: 18px; color: #065f46; }}
            .header p {{ margin: 4px 0; font-size: 12px; color: #475569; }}
            .meta-box {{ background: #f8fafc; border: 1px solid #cbd5e1; border-radius: 8px; padding: 12px; margin-bottom: 18px; font-size: 12px; }}
            .meta-box span {{ display: inline-block; margin-left: 20px; font-weight: bold; }}
            .kpi-grid {{ width: 100%; border-collapse: collapse; margin-bottom: 20px; }}
            .kpi-grid td {{ border: 1px solid #cbd5e1; padding: 10px; text-align: center; background: #ffffff; }}
            .kpi-title {{ font-size: 11px; color: #64748b; display: block; }}
            .kpi-val {{ font-size: 16px; font-weight: bold; color: #0f172a; margin-top: 4px; display: block; }}
            table {{ width: 100%; border-collapse: collapse; font-size: 11px; margin-top: 10px; }}
            th {{ background: #065f46; color: #ffffff; padding: 8px; border: 1px solid #065f46; }}
            td {{ padding: 7px; border: 1px solid #cbd5e1; text-align: center; }}
            .notes {{ background: #fffbeb; border: 1px solid #fde68a; padding: 12px; border-radius: 8px; font-size: 12px; margin-top: 18px; }}
            .footer {{ margin-top: 30px; text-align: left; font-size: 11px; color: #64748b; border-top: 1px solid #e2e8f0; padding-top: 10px; }}
        </style>
    </head>
    <body>
        <div class="header">
            <h1>المؤسسة العامة للتدريب التقني والمهني</h1>
            <p>المعهد الصناعي الثانوي بالأفلاج — تقرير متابعة الحضور وسير العملية التدريبية (للإدارة فقط)</p>
        </div>

        <div class="meta-box">
            <span>الأسبوع التدريبي: {training_week}</span>
            <span>الفصل التدريبي: {training_term}</span>
            <span>النطاق / القسم: {department}</span>
            <span>تاريخ الاعتماد: {approval_date}</span>
        </div>

        <table class="kpi-grid">
            <tr>
                <td><span class="kpi-title">عدد المدربين</span><span class="kpi-val">{kpis.get('trainers_count', 0)}</span></td>
                <td><span class="kpi-title">نسبة حضور المدربين</span><span class="kpi-val">{kpis.get('trainers_attendance_rate', 0)}%</span></td>
                <td><span class="kpi-title">إجمالي المتدربين</span><span class="kpi-val">{kpis.get('trainees_count', 0)}</span></td>
                <td><span class="kpi-title">نسبة حضور المتدربين</span><span class="kpi-val">{kpis.get('trainees_attendance_rate', 0)}%</span></td>
                <td><span class="kpi-title">الشعب المنفذة / الإجمالية</span><span class="kpi-val">{kpis.get('sections_executed', 0)} / {kpis.get('sections_total', 0)}</span></td>
            </tr>
        </table>

        {table_section}

        <div class="notes">
            <strong>ملاحظات الإدارة والإجراءات التصحيحية:</strong><br>
            {notes}
        </div>

        <div class="footer">
            تم توليد هذا التقرير آلياً وحفظه في سحابة OneDrive المؤسسية للمعهد.
        </div>
    </body>
    </html>
    """

    story = fitz.Story(html=html_content)
    writer = fitz.DocumentWriter(pdf_filepath)
    mediabox = fitz.paper_rect("a4")
    where = mediabox + (36, 36, -36, -36)
    more = True
    while more:
        device = writer.begin_page(mediabox)
        more, _ = story.place(where)
        story.draw(device)
        writer.end_page()
    writer.close()

# --- 7. الواجهات العامة للمتدربين (نسبة الغياب الحالية والساعات المتبقية فقط) ---
@app.route("/")
def home():
    return render_template("home.html")

@app.route("/schedule")
def schedule_page():
    return render_template("schedule.html")

@app.route("/attendance")
def attendance_page():
    last_update = get_absence_last_update_text()
    return render_template("attendance.html", absence_last_update=last_update)

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

    if 'رقم_نظيف' not in df.columns:
        df['رقم_نظيف'] = df['رقم المتدرب'].astype(str).apply(lambda x: normalize_digits(x).split('.')[0].strip())
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
        section_no = str(row['أرقام شعب المقرر']).split('.')[0] if 'أرقام شعب المقرر' in row else ''
        raw_rate = row.get('إجمالي نسبة الغياب بعذر وبدون عذر', 0)
        raw_hours = row.get('إجمالي ساعات الغياب بعذر وبدون عذر', 0)
        course_status_raw = extract_row_course_status(row)

        try:
            rate = float(raw_rate)
        except (ValueError, TypeError):
            rate = 0.0

        try:
            hours = float(raw_hours)
        except (ValueError, TypeError):
            hours = 0.0

        remaining_hours, max_allowed_hours = compute_remaining_hours(rate, hours, row)

        if is_folded_or_deprived_text(course_status_raw):
            if "مطوي" in course_status_raw or "قيده" in course_status_raw or "انقطاع" in course_status_raw or "إنقطاع" in course_status_raw:
                status_text = f"تم طي قيد المتدرب من المعهد ({course_status_raw})"
            else:
                status_text = f"محروم نظاماً ({course_status_raw})"
            status_color = "danger"
            remaining_hours = 0.0
            if max_rate < 20.0:
                max_rate = 20.0
        elif rate >= 20.0:
            status_text = "محروم نظاماً (0 ساعات متبقية)"
            status_color = "danger"
            remaining_hours = 0.0
        elif rate >= 15.0:
            rem_note = f" — المتبقي: {remaining_hours} ساعة" if remaining_hours is not None else ""
            status_text = f"إنذار ثانٍ (خطر حرمان){rem_note}"
            status_color = "warning-high"
        elif rate >= 10.0:
            rem_note = f" — المتبقي: {remaining_hours} ساعة" if remaining_hours is not None else ""
            status_text = f"إنذار أول{rem_note}"
            status_color = "warning-low"
        else:
            rem_note = f" — المتبقي: {remaining_hours} ساعة" if remaining_hours is not None else ""
            status_text = f"مستمر (وضع آمن){rem_note}"
            status_color = "safe"

        if rate > max_rate:
            max_rate = rate

        courses.append({
            "course_name": course_name,
            "section_no": str(section_no),
            "absence_rate": round(rate, 2),
            "absence_hours": round(hours, 1),
            "remaining_hours": remaining_hours,
            "max_allowed_hours": max_allowed_hours,
            "status_text": status_text,
            "status_color": status_color,
            "course_status": course_status_raw
        })

    return jsonify({
        "success": True,
        "trainee_name": trainee_name,
        "trainee_id": trainee_id,
        "department": department,
        "program": program,
        "max_rate": round(max_rate, 2),
        "courses": courses,
        "last_update": get_absence_last_update_text()
    })

@app.route("/search_schedule", methods=["POST"])
def search_schedule():
    trainee_id = normalize_digits(request.form.get("trainee_id", "").strip())
    if len(trainee_id) < 9 or not trainee_id.isdigit():
        return jsonify({"success": False, "message": "يرجى إدخال الرقم التدريبي بشكل صحيح."})
    pages = find_student_pages(SCHEDULES_PDF, trainee_id)
    if not pages:
        return jsonify({"success": False, "message": "لم يتم العثور على جدول مطابق لهذا الرقم."})
    pages_str = ",".join(map(str, pages))
    return jsonify({
        "success": True,
        "pages": pages_str,
        "pdf_url": f"/get_schedule_pdf?pages={pages_str}&trainee_id={trainee_id}"
    })

@app.route("/get_schedule_pdf")
def get_schedule_pdf():
    pages_param = request.args.get("pages", "")
    trainee_id = normalize_digits(request.args.get("trainee_id", "")).strip()
    if not pages_param or not os.path.exists(SCHEDULES_PDF):
        return """
        <div style='font-family:sans-serif; text-align:center; padding:50px; direction:rtl;'>
            <h2 style='color:#b91c1c;'>ملف جداول المتدربين غير متوفر حالياً</h2>
            <p style='color:#64748b;'>يرجى التأكد من رفع ملف الجداول من قبل الإدارة أو إعادة المحاولة.</p>
            <a href='/schedule' style='display:inline-block; margin-top:15px; padding:10px 20px; background:#0f2d59; color:#fff; text-decoration:none; border-radius:8px;'>العودة لصفحة الاستعلام</a>
        </div>
        """, 404

    try:
        page_indices = [int(p) for p in pages_param.split(",") if p.isdigit()][:10]
        pdf_bytes = io.BytesIO()
        with fitz.open(SCHEDULES_PDF) as doc, fitz.open() as out_doc:
            for p_num in page_indices:
                if 0 <= p_num < len(doc):
                    out_doc.insert_pdf(doc, from_page=p_num, to_page=p_num)
            if len(out_doc) == 0:
                return "لم يتم العثور على صفحات مطابقة", 404
            out_doc.save(pdf_bytes)

        pdf_bytes.seek(0)
        filename = f"جدول_المتدرب_{trainee_id}.pdf" if trainee_id else "جدول_المتدرب.pdf"
        return send_file(pdf_bytes, mimetype="application/pdf", download_name=filename)
    except Exception as e:
        return str(e), 500

@app.route("/get_schedule_image")
def get_schedule_image():
    pages_param = request.args.get("pages", "")
    if not pages_param or not os.path.exists(SCHEDULES_PDF):
        return "معلمات غير صالحة أو الملف غير متوفر", 400

    try:
        page_indices = [int(p) for p in pages_param.split(",") if p.isdigit()][:5]
        with fitz.open(SCHEDULES_PDF) as doc, fitz.open() as temp_doc:
            for p_num in page_indices:
                if 0 <= p_num < len(doc):
                    temp_doc.insert_pdf(doc, from_page=p_num, to_page=p_num)
            if len(temp_doc) == 0:
                return "لم يتم العثور على صفحات", 404
            if len(temp_doc) == 1:
                pix = temp_doc[0].get_pixmap(dpi=200)
                img_bytes = pix.tobytes("png")
            else:
                with fitz.open() as combined_doc:
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

# --- 8. بوابة المدربين والمسؤولين ---
@app.route("/trainer")
def trainer_login_page():
    return render_template("trainer_login.html")

@app.route("/trainer/login", methods=["POST"])
def trainer_login_action():
    client_ip = request.headers.get("X-Forwarded-For", request.remote_addr)
    if is_rate_limited(client_ip):
        return render_template("trainer_login.html", error="تم إيقاف المحاولات مؤقتاً بسبب تكرار الإدخال الخاطئ. حاول بعد 10 دقائق.")

    emp_id = clean_employee_id(request.form.get("employee_id", ""))
    if not emp_id:
        return render_template("trainer_login.html", error="يرجى إدخال الرقم الوظيفي.")

    user = get_staff_by_id(emp_id)

    if not user:
        df_sec = load_so09_dataframe()
        found_trainer = None
        if df_sec is not None and not df_sec.empty:
            if 'emp_clean' not in df_sec.columns:
                df_sec['emp_clean'] = df_sec['رقم الحاسب'].astype(str).apply(clean_employee_id)
            matched = df_sec[df_sec['emp_clean'] == emp_id]
            if not matched.empty:
                t_name = str(matched.iloc[0]['اسم المدرب']).strip()
                assign_staff_role_with_singleton_check(emp_id, t_name, "trainer", "", force_replace=True)
                found_trainer = {"employee_id": emp_id, "trainer_name": t_name, "department": "", "role": "trainer"}

        if not found_trainer:
            record_failed_attempt(client_ip)
            return render_template("trainer_login.html", error="الرقم الوظيفي غير مسجل في النظام. تواصل مع الإدارة لتفعيل حسابك.")
        user = found_trainer

    clear_failed_attempts(client_ip)
    session.permanent = True
    session["trainer_id"] = emp_id
    session["trainer_name"] = user["trainer_name"]
    session["trainer_role"] = user["role"]
    session["trainer_dept"] = user.get("department", "")
    return redirect(url_for("trainer_dashboard"))

@app.route("/trainer/dashboard")
def trainer_dashboard():
    if not session.get("trainer_id"):
        return redirect(url_for("trainer_login_page"))

    emp_id = session.get("trainer_id")
    user = get_staff_by_id(emp_id)
    if user:
        session["trainer_role"] = user["role"]
        session["trainer_dept"] = user.get("department", "")
        session["trainer_name"] = user["trainer_name"]

    role = session.get("trainer_role", "trainer")
    dept = session.get("trainer_dept", "")
    return render_template("trainer_dashboard.html",
                           trainer_id=emp_id,
                           trainer_name=session.get("trainer_name"),
                           trainer_role=role,
                           trainer_dept=dept,
                           role_label=get_role_label(role, dept),
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
        pdf_bytes = io.BytesIO()
        with fitz.open(TRAINERS_SCHEDULES_PDF) as doc, fitz.open() as out_doc:
            for p in pages:
                if 0 <= p < len(doc):
                    out_doc.insert_pdf(doc, from_page=p, to_page=p)
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
    user = get_staff_by_id(emp_id)
    if user:
        session["trainer_role"] = user["role"]
        session["trainer_dept"] = user.get("department", "")
        session["trainer_name"] = user["trainer_name"]

    role = session.get("trainer_role", "trainer")
    trainer_dept = session.get("trainer_dept", "")

    df_abs = load_absence_dataframe()
    departments = []
    programs = []
    courses = []

    if df_abs is not None and not df_abs.empty:
        if role in FULL_ACCESS_ROLES:
            if 'اسم القسم' in df_abs.columns:
                departments = sorted([d for d in df_abs['اسم القسم'].dropna().unique() if str(d).strip()])
            if 'اسم البرنامج' in df_abs.columns:
                programs = sorted([p for p in df_abs['اسم البرنامج'].dropna().unique() if str(p).strip()])
            if 'اسم المقرر' in df_abs.columns:
                courses = sorted([c for c in df_abs['اسم المقرر'].dropna().unique() if str(c).strip()])
        elif role == "head_of_dept":
            if trainer_dept and 'اسم القسم' in df_abs.columns:
                dept_records = df_abs[df_abs['اسم القسم'] == trainer_dept]
            else:
                dept_records = df_abs.iloc[0:0]
            if 'اسم البرنامج' in dept_records.columns:
                programs = sorted([p for p in dept_records['اسم البرنامج'].dropna().unique() if str(p).strip()])
            if 'اسم المقرر' in dept_records.columns:
                courses = sorted([c for c in dept_records['اسم المقرر'].dropna().unique() if str(c).strip()])
        else:
            assigned_sections = get_trainer_sections(emp_id)
            if assigned_sections:
                df_abs_copy = df_abs.copy()
                df_abs_copy['sec_clean'] = df_abs_copy['أرقام شعب المقرر'].astype(str).apply(lambda x: str(x).split('.')[0].strip())
                trainer_records = df_abs_copy[df_abs_copy['sec_clean'].isin(assigned_sections)]
                if 'اسم البرنامج' in trainer_records.columns:
                    programs = sorted([p for p in trainer_records['اسم البرنامج'].dropna().unique() if str(p).strip()])
                if 'اسم المقرر' in trainer_records.columns:
                    courses = sorted([c for c in trainer_records['اسم المقرر'].dropna().unique() if str(c).strip()])

    last_update = get_absence_last_update_text()

    return render_template("trainer_absence_view.html",
                           trainer_name=session.get("trainer_name"),
                           trainer_role=role,
                           trainer_dept=trainer_dept,
                           role_label=get_role_label(role, trainer_dept),
                           is_full_access=(role in FULL_ACCESS_ROLES),
                           departments=departments,
                           programs=programs,
                           courses=courses,
                           absence_last_update=last_update)

@app.route("/trainer/logout")
def trainer_logout():
    session.pop("trainer_id", None)
    session.pop("trainer_name", None)
    session.pop("trainer_role", None)
    session.pop("trainer_dept", None)
    return redirect(url_for("trainer_login_page"))

@app.route("/api/absence_records_query")
def api_absence_records_query():
    source = request.args.get("source", "")
    is_admin = session.get("logged_in", False)
    trainer_id = session.get("trainer_id", None)

    if source == "trainer" and not trainer_id:
        return jsonify({"success": False, "message": "غير مصرح للمدرب"}), 403
    elif source == "admin" and not is_admin:
        return jsonify({"success": False, "message": "غير مصرح للإدارة"}), 403
    elif not is_admin and not trainer_id:
        return jsonify({"success": False, "message": "غير مصرح"}), 403

    trainer_role = session.get("trainer_role", "trainer")
    trainer_dept = session.get("trainer_dept", "")

    if source == "trainer" and trainer_id:
        user = get_staff_by_id(trainer_id)
        if user:
            trainer_role = user["role"]
            trainer_dept = user.get("department", "")

    df = load_absence_dataframe()
    if df is None or df.empty:
        return jsonify({"success": False, "records": [], "stats": {}, "courses": [], "programs": []})

    filtered = df.copy()
    rate_col = 'إجمالي نسبة الغياب بعذر وبدون عذر'
    hours_col = 'إجمالي ساعات الغياب بعذر وبدون عذر'
    filtered['rate'] = pd.to_numeric(filtered[rate_col], errors='coerce').fillna(0.0)
    filtered['hours'] = pd.to_numeric(filtered[hours_col], errors='coerce').fillna(0.0)
    filtered['status_raw'] = filtered.apply(extract_row_course_status, axis=1)
    filtered['is_folded_or_deprived'] = filtered['status_raw'].apply(is_folded_or_deprived_text)

    dept_filter = request.args.get("department", "ALL")
    program_filter = request.args.get("program", "ALL")
    course_filter = request.args.get("course", "ALL")
    status_filter = request.args.get("status", "ALL")
    view_mode = request.args.get("view_mode", "role_scope")

    if source == "trainer":
        if trainer_role == "trainer" or view_mode == "my_sections":
            assigned_sections = get_trainer_sections(trainer_id)
            if assigned_sections:
                filtered['sec_clean'] = filtered['أرقام شعب المقرر'].astype(str).apply(lambda x: str(x).split('.')[0].strip())
                filtered = filtered[filtered['sec_clean'].isin(assigned_sections)]
            else:
                filtered = filtered.iloc[0:0]
        elif trainer_role == "head_of_dept":
            if trainer_dept and 'اسم القسم' in filtered.columns:
                filtered = filtered[filtered['اسم القسم'] == trainer_dept]
            else:
                filtered = filtered.iloc[0:0]
        elif trainer_role in FULL_ACCESS_ROLES:
            if dept_filter != "ALL" and 'اسم القسم' in filtered.columns:
                filtered = filtered[filtered['اسم القسم'] == dept_filter]
    else:
        if dept_filter != "ALL" and 'اسم القسم' in filtered.columns:
            filtered = filtered[filtered['اسم القسم'] == dept_filter]

    available_programs = sorted([p for p in filtered['اسم البرنامج'].dropna().unique() if str(p).strip()]) if 'اسم البرنامج' in filtered.columns else []

    if program_filter != "ALL" and 'اسم البرنامج' in filtered.columns:
        filtered = filtered[filtered['اسم البرنامج'] == program_filter]

    available_courses = sorted([c for c in filtered['اسم المقرر'].dropna().unique() if str(c).strip()]) if 'اسم المقرر' in filtered.columns else []

    if course_filter != "ALL" and 'اسم المقرر' in filtered.columns:
        filtered = filtered[filtered['اسم المقرر'] == course_filter]

    total_records = len(filtered)
    total_unique_trainees = int(filtered['رقم المتدرب'].nunique()) if 'رقم المتدرب' in filtered.columns else total_records

    danger_mask = (filtered['rate'] >= 20.0) | (filtered['is_folded_or_deprived'])
    warn2_mask = (~danger_mask) & (filtered['rate'] >= 15.0) & (filtered['rate'] < 20.0)
    warn1_mask = (~danger_mask) & (filtered['rate'] >= 10.0) & (filtered['rate'] < 15.0)
    safe_mask = (~danger_mask) & (filtered['rate'] < 10.0)

    danger_count = int(danger_mask.sum())
    warn2_count = int(warn2_mask.sum())
    warn1_count = int(warn1_mask.sum())
    safe_count = int(safe_mask.sum())

    if status_filter == "danger":
        filtered = filtered[danger_mask]
    elif status_filter == "warn2":
        filtered = filtered[warn2_mask]
    elif status_filter == "warn1":
        filtered = filtered[warn1_mask]
    elif status_filter == "safe":
        filtered = filtered[safe_mask]

    current_filtered_trainees = int(filtered['رقم المتدرب'].nunique()) if 'رقم المتدرب' in filtered.columns else len(filtered)

    filtered = filtered.sort_values(by=['is_folded_or_deprived', 'rate'], ascending=[False, False])
    records = []
    for _, row in filtered.iterrows():
        rate = float(row['rate'])
        hours = float(row['hours'])
        course_status_raw = row['status_raw']
        rem_hours, _ = compute_remaining_hours(rate, hours, row)

        if row['is_folded_or_deprived']:
            if "مطوي" in course_status_raw or "قيده" in course_status_raw or "انقطاع" in course_status_raw or "إنقطاع" in course_status_raw:
                st_text = f"مطوي قيده ({course_status_raw})"
            else:
                st_text = f"محروم ({course_status_raw})"
            st_badge = "danger"
        elif rate >= 20.0:
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
            "trainee_id": str(row.get('رقم المتدرب', '')).split('.')[0],
            "trainee_name": str(row.get('اسم المتدرب', '')),
            "department": str(row.get('اسم القسم', '')),
            "program": str(row.get('اسم البرنامج', '')),
            "course_name": str(row.get('اسم المقرر', '')),
            "section_no": str(row.get('أرقام شعب المقرر', '')).split('.')[0],
            "absence_rate": round(rate, 2),
            "absence_hours": round(hours, 1),
            "remaining_hours": rem_hours,
            "status_text": st_text,
            "status_badge": st_badge
        })

    return jsonify({
        "success": True,
        "records": records,
        "courses": available_courses,
        "programs": available_programs,
        "stats": {
            "total_records": total_records,
            "total_trainees": total_unique_trainees,
            "current_trainees": current_filtered_trainees,
            "danger": danger_count,
            "warn2": warn2_count,
            "warn1": warn1_count,
            "safe": safe_count
        }
    })

# --- 9. لوحة الإدارة الرئيسية ---
@app.route("/admin", methods=["GET", "POST"])
def admin():
    msg = None
    msg_type = None
    client_ip = request.headers.get("X-Forwarded-For", request.remote_addr)

    if request.method == "POST":
        action = request.form.get("action")

        if action == "login":
            if is_rate_limited(client_ip):
                msg = "تم حظر الدخول مؤقتاً لتجاوز عدد المحاولات المسموح بها. انتظر 10 دقائق."
                msg_type = "error"
            else:
                entered_pw = request.form.get("password", "").strip()
                if entered_pw == ADMIN_PASSWORD:
                    clear_failed_attempts(client_ip)
                    session.permanent = True
                    session["logged_in"] = True
                else:
                    record_failed_attempt(client_ip)
                    msg = "كلمة المرور غير صحيحة"
                    msg_type = "error"

        elif action == "upload_schedule":
            if not session.get("logged_in"): return redirect(url_for("admin"))
            file = request.files.get("pdf_file")
            if file and file.filename.lower().endswith(".pdf"):
                file.save(SCHEDULES_PDF)
                invalidate_caches()
                msg = "تم رفع جدول المتدربين وحفظه في مجلد OneDrive المؤسسي بنجاح!"
                msg_type = "success"

        elif action == "upload_trainers_schedule":
            if not session.get("logged_in"): return redirect(url_for("admin"))
            file = request.files.get("pdf_file")
            if file and file.filename.lower().endswith(".pdf"):
                file.save(TRAINERS_SCHEDULES_PDF)
                invalidate_caches()
                msg = "تم رفع جدول المدربين وحفظه في مجلد OneDrive المؤسسي بنجاح!"
                msg_type = "success"

        elif action == "upload_absence_file":
            if not session.get("logged_in"): return redirect(url_for("admin"))
            try:
                file = request.files.get("absence_file")
                if not file or not file.filename:
                    msg = "يرجى اختيار ملف الغياب أولاً."
                    msg_type = "error"
                elif not file.filename.lower().endswith((".csv", ".xlsx", ".xls")):
                    msg = "صيغة الملف غير مدعومة. يرجى رفع ملف بصيغة CSV أو Excel."
                    msg_type = "error"
                else:
                    ext = os.path.splitext(file.filename)[1].lower()
                    for old_ext in ['.xlsx', '.xls', '.csv']:
                        old_path = os.path.join(DIR_CURRENT, f"absence_data{old_ext}")
                        if os.path.exists(old_path) and old_ext != ext:
                            try: os.remove(old_path)
                            except Exception: pass

                    save_path = os.path.join(DIR_CURRENT, f"absence_data{ext}")
                    file.save(save_path)
                    invalidate_caches()

                    df_check = load_absence_dataframe()
                    rows_count = len(df_check) if df_check is not None else 0

                    now_str = get_ksa_now_formatted()
                    set_meta_value("absence_last_update", now_str)

                    msg = f"تم تحديث ملف الغياب ({rows_count} سجل) في سحابة OneDrive المؤسسية بنجاح! (تاريخ التحديث: {now_str})"
                    msg_type = "success"
            except Exception as e:
                msg = f"حدث خطأ أثناء رفع ملف الغياب: {str(e)}"
                msg_type = "error"

        elif action == "upload_so09_file":
            if not session.get("logged_in"): return redirect(url_for("admin"))
            file = request.files.get("so09_file")
            if file and file.filename.lower().endswith((".csv", ".xlsx", ".xls")):
                ext = os.path.splitext(file.filename)[1].lower()
                for old_ext in ['.xlsx', '.xls', '.csv']:
                    old_path = os.path.join(DIR_CURRENT, f"so09_data{old_ext}")
                    if os.path.exists(old_path) and old_ext != ext:
                        try: os.remove(old_path)
                        except Exception: pass
                save_path = os.path.join(DIR_CURRENT, f"so09_data{ext}")
                file.save(save_path)
                invalidate_caches()
                df_sec = load_so09_dataframe()
                if df_sec is not None and not df_sec.empty:
                    existing_records = load_staff_roles()
                    existing_ids = {r["employee_id"] for r in existing_records}
                    for _, row in df_sec.iterrows():
                        raw_emp = str(row.get('رقم الحاسب', ''))
                        clean_emp = clean_employee_id(raw_emp)
                        t_name = str(row.get('اسم المدرب', '')).strip()
                        if clean_emp and t_name and clean_emp not in existing_ids:
                            existing_records.append({
                                "employee_id": clean_emp,
                                "trainer_name": t_name,
                                "department": "",
                                "role": "trainer"
                            })
                            existing_ids.add(clean_emp)
                    save_staff_roles(existing_records)
                msg = "تم رفع ملف الشعب (SO09) واستيراد المدربين وحفظهم في OneDrive بنجاح!"
                msg_type = "success"

    df_abs = load_absence_dataframe()
    departments = []
    if df_abs is not None and not df_abs.empty and 'اسم القسم' in df_abs.columns:
        departments = sorted([d for d in df_abs['اسم القسم'].dropna().unique() if str(d).strip()])

    trainers_list = sorted(
        load_staff_roles(),
        key=lambda x: (1 if x.get("role", "trainer") == "trainer" else 0, x.get("trainer_name", ""))
    )

    return render_template("admin.html",
                           logged_in=session.get("logged_in", False),
                           trainers_list=trainers_list,
                           departments=departments,
                           onedrive_path=DATA_ROOT,
                           msg=msg,
                           msg_type=msg_type)

@app.route("/admin/delete_file/<file_type>", methods=["POST"])
def admin_delete_file(file_type):
    if not session.get("logged_in"):
        return redirect(url_for("admin"))

    if file_type == "schedules" and os.path.exists(SCHEDULES_PDF):
        try: os.remove(SCHEDULES_PDF)
        except Exception: pass
    elif file_type == "trainers_schedules" and os.path.exists(TRAINERS_SCHEDULES_PDF):
        try: os.remove(TRAINERS_SCHEDULES_PDF)
        except Exception: pass
    elif file_type == "absence":
        for ext in ['.xlsx', '.xls', '.csv']:
            p = os.path.join(DIR_CURRENT, f"absence_data{ext}")
            if os.path.exists(p):
                try: os.remove(p)
                except Exception: pass
        set_meta_value("absence_last_update", "")
    elif file_type == "so09":
        for ext in ['.xlsx', '.xls', '.csv']:
            p = os.path.join(DIR_CURRENT, f"so09_data{ext}")
            if os.path.exists(p):
                try: os.remove(p)
                except Exception: pass

    invalidate_caches()
    return redirect(url_for("admin"))

@app.route("/admin/trainer_role/update", methods=["POST"])
def update_trainer_role():
    if not session.get("logged_in"):
        return jsonify({"success": False, "message": "غير مصرح"}), 403
    emp_id = clean_employee_id(request.form.get("employee_id", ""))
    new_role = request.form.get("role", "trainer")
    new_dept = request.form.get("department", "").strip()
    force_replace = request.form.get("force_replace", "false").lower() == "true"

    if not emp_id:
        return jsonify({"success": False, "message": "رقم المدرب مفقود"})

    ok, message, conflict_user = assign_staff_role_with_singleton_check(
        emp_id=emp_id,
        trainer_name="",
        new_role=new_role,
        new_dept=new_dept,
        force_replace=force_replace
    )
    if not ok and conflict_user:
        return jsonify({
            "success": False,
            "conflict": True,
            "message": message,
            "conflict_name": conflict_user["trainer_name"]
        })

    return jsonify({"success": ok, "message": message})

@app.route("/admin/trainer_role/add", methods=["POST"])
def add_trainer_role():
    if not session.get("logged_in"):
        return redirect(url_for("admin"))
    emp_id = clean_employee_id(request.form.get("employee_id", ""))
    t_name = request.form.get("trainer_name", "").strip()
    role = request.form.get("role", "trainer")
    dept = request.form.get("department", "").strip()
    if emp_id and t_name:
        assign_staff_role_with_singleton_check(
            emp_id=emp_id,
            trainer_name=t_name,
            new_role=role,
            new_dept=dept,
            force_replace=True
        )
    return redirect(url_for("admin"))

@app.route("/admin/trainer_role/delete/<emp_id>", methods=["POST"])
def delete_trainer_role(emp_id):
    if not session.get("logged_in"):
        return redirect(url_for("admin"))
    clean_id = clean_employee_id(emp_id)
    records = [r for r in load_staff_roles() if r["employee_id"] != clean_id]
    save_staff_roles(records)
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
    programs = []
    courses = []
    if df is not None and not df.empty:
        if 'اسم القسم' in df.columns:
            departments = sorted([d for d in df['اسم القسم'].dropna().unique() if str(d).strip()])
        if 'اسم البرنامج' in df.columns:
            programs = sorted([p for p in df['اسم البرنامج'].dropna().unique() if str(p).strip()])
        if 'اسم المقرر' in df.columns:
            courses = sorted([c for c in df['اسم المقرر'].dropna().unique() if str(c).strip()])

    return render_template("admin_absence_dashboard.html",
                           has_data=df is not None and not df.empty,
                           departments=departments,
                           programs=programs,
                           courses=courses,
                           absence_last_update=get_absence_last_update_text())

@app.route('/admin/attendance')
def attendance_tracker():
    if not session.get("logged_in"):
        return redirect(url_for("admin"))
    return render_template('attendance_tracker.html', is_archived_view=False)

@app.route('/admin/attendance/approve', methods=['POST'])
def approve_report():
    """
    اعتماد التقرير الأسبوعي وتوليد ملف PDF رسمي مباشرة داخل مجلد 02_Admin_Reports في OneDrive
    """
    if not session.get("logged_in"):
        return jsonify({'success': False, 'message': 'غير مصرح'}), 403
    try:
        data = request.get_json()
        approval_date = get_ksa_now_formatted()
        data['approval_date'] = approval_date
        training_week = re.sub(r'[\\/*?:"<>|]', '_', str(data.get('training_week', 'أسبوع'))).strip()
        department = re.sub(r'[\\/*?:"<>|]', '_', str(data.get('department', 'المعهد'))).strip()
        date_stamp = datetime.now(KSA_TZ).strftime('%Y-%m-%d_%H%M')

        pdf_filename = f"تقرير_الحضور_{training_week}_{department}_{date_stamp}.pdf"
        pdf_filepath = os.path.join(DIR_REPORTS, pdf_filename)
        generate_weekly_admin_pdf(data, pdf_filepath)

        # حفظ نسخة JSON مرافقة لاستعراضها داخل الواجهة عند الحاجة
        json_filepath = pdf_filepath.replace(".pdf", ".json")
        with open(json_filepath, "w", encoding="utf-8") as jf:
            json.dump(data, jf, ensure_ascii=False, indent=2)

        return jsonify({'success': True, 'pdf_file': pdf_filename})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/admin/attendance/archive')
def attendance_archive():
    if not session.get("logged_in"):
        return redirect(url_for("admin"))
    reports = []
    if os.path.exists(DIR_REPORTS):
        pdf_files = sorted(
            [f for f in os.listdir(DIR_REPORTS) if f.lower().endswith(".pdf")],
            key=lambda f: os.path.getmtime(os.path.join(DIR_REPORTS, f)),
            reverse=True
        )
        for idx, fname in enumerate(pdf_files, 1):
            fpath = os.path.join(DIR_REPORTS, fname)
            jpath = fpath.replace(".pdf", ".json")
            meta = {}
            if os.path.exists(jpath):
                try:
                    with open(jpath, "r", encoding="utf-8") as jf:
                        meta = json.load(jf)
                except Exception:
                    meta = {}
            kpis = meta.get("kpis", {})
            reports.append({
                "id": fname,
                "filename": fname,
                "approval_date": meta.get("approval_date", datetime.fromtimestamp(os.path.getmtime(fpath)).strftime('%Y-%m-%d %H:%M')),
                "training_week": meta.get("training_week", fname.replace(".pdf", "")),
                "training_term": meta.get("training_term", "-"),
                "department": meta.get("department", "-"),
                "trainers_attendance_rate": kpis.get("trainers_attendance_rate", "-"),
                "trainees_attendance_rate": kpis.get("trainees_attendance_rate", "-"),
                "notes": meta.get("notes", "")
            })
    return render_template('attendance_archive.html', reports=reports)

@app.route('/admin/attendance/archive/download/<path:filename>')
def download_archived_pdf(filename):
    if not session.get("logged_in"):
        return redirect(url_for("admin"))
    safe_name = os.path.basename(filename)
    fpath = os.path.join(DIR_REPORTS, safe_name)
    if not os.path.exists(fpath):
        return "التقرير غير موجود", 404
    return send_file(fpath, mimetype="application/pdf", download_name=safe_name)

@app.route('/admin/attendance/archive/<path:report_id>')
def get_archived_report(report_id):
    if not session.get("logged_in"):
        return redirect(url_for("admin"))
    safe_name = os.path.basename(report_id)
    pdf_path = os.path.join(DIR_REPORTS, safe_name)
    json_path = pdf_path.replace(".pdf", ".json")
    if os.path.exists(json_path):
        with open(json_path, "r", encoding="utf-8") as jf:
            archived_json = jf.read()
        return render_template('attendance_tracker.html', archived_json=archived_json, is_archived_view=True)
    if os.path.exists(pdf_path):
        return send_file(pdf_path, mimetype="application/pdf", download_name=safe_name)
    return "التقرير غير موجود", 404

@app.route('/admin/attendance/archive/<path:report_id>/delete', methods=['POST'])
def delete_archived_report(report_id):
    if not session.get("logged_in"):
        return redirect(url_for("admin"))
    safe_name = os.path.basename(report_id)
    pdf_path = os.path.join(DIR_REPORTS, safe_name)
    json_path = pdf_path.replace(".pdf", ".json")
    for p in (pdf_path, json_path):
        if os.path.exists(p):
            try: os.remove(p)
            except Exception: pass
    return redirect(url_for('attendance_archive'))

if __name__ == "__main__":
    from waitress import serve
    print(f"OneDrive Data Directory: {DATA_ROOT}")
    print("المنصة تعمل الآن على المنفذ 5000...")
    serve(app, host="0.0.0.0", port=5000, threads=8)
