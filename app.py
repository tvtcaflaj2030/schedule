import io
import re
import os
import fitz  # PyMuPDF
from flask import Flask, render_template, request, send_file, jsonify, redirect, url_for, session

app = Flask(__name__)
app.secret_key = "secret_admin_key_turki_aflaj"
ADMIN_PASSWORD = "turki2026"
SCHEDULES_PDF_PATH = "schedules.pdf"
ATTENDANCE_PDF_PATH = "attendance.pdf"

def normalize_digits(text):
    arabic_digits = "٠١٢٣٤٥٦٧٨٩"
    english_digits = "0123456789"
    translation_table = str.maketrans(arabic_digits, english_digits)
    return text.translate(translation_table)

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
            page = doc[page_num]
            clean_page_text = normalize_digits(page.get_text())
            if id_pattern.search(clean_page_text):
                matched_pages.append(page_num)
        return matched_pages
    except Exception as e:
        print(f"Error reading PDF: {e}")
        return []

def extract_attendance_data(trainee_id):
    clean_id = normalize_digits(trainee_id).strip()
    if not os.path.exists(ATTENDANCE_PDF_PATH):
        return None

    try:
        doc = fitz.open(ATTENDANCE_PDF_PATH)
        target_page = None
        id_pattern = re.compile(rf'(?<!\d){re.escape(clean_id)}(?!\d)|(?<!\d){re.escape(clean_id[::-1])}(?!\d)')

        for page_num in range(len(doc)):
            text = normalize_digits(doc[page_num].get_text())
            if id_pattern.search(text):
                target_page = doc[page_num]
                break

        if not target_page:
            return None

        # استخراج الجداول المنظمة بواسطة PyMuPDF
        tabs = target_page.find_tables()
        courses = []
        student_name = ""

        page_raw_text = target_page.get_text()
        name_match = re.search(r'اسم المتدرب\s+([^\n\r]+)', page_raw_text)
        if name_match:
            student_name = name_match.group(1).strip()

        if tabs.tables:
            tab = tabs.tables[0]
            df_data = tab.extract()
            for row in df_data:
                clean_row = [normalize_digits(str(c or '')).strip() for c in row if str(c or '').strip()]
                # البحث عن أسطر المقررات ونسب الغياب
                row_str = " ".join(clean_row)
                
                # التقاط الأرقام العشرية التي تمثل نسب الغياب
                percentages = re.findall(r'\b\d+(?:\.\d+)?\b', row_str)
                float_vals = [float(p) for p in percentages]
                
                # استخراج أسماء المقررات المعتادة
                if any(k in row_str for k in ['حاسب', 'سلك', 'سلم', 'مهني', 'فيزي', 'نشاط', 'عرب', 'رياضيات', 'مقرر']):
                    course_name_match = re.search(r'(\b\d+\s+[\u0621-\u064A\s\d]+|[A-Za-z0-9\u0621-\u064A\s]{4,35})', row_str)
                    c_name = clean_row[-1] if len(clean_row) > 0 else "مقرر دراسي"
                    
                    # اختيار النسبة الإجمالية الأنسب
                    pct = 0.0
                    for val in float_vals:
                        if val <= 100.0 and val > 0:
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
                        "course_name": c_name.replace("\n", " "),
                        "percentage": pct,
                        "status": status
                    })

        # طريقة احتياطية عبر النص في حال تعذر قراءة الجدول الهيكلي
        if not courses:
            lines = [normalize_digits(l).strip() for l in page_raw_text.splitlines() if l.strip()]
            for line in lines:
                if any(k in line for k in ['حاسب', 'سلوك', 'ثقافة', 'مهارات', 'فيزياء', 'مقدمة', 'قواعد']):
                    pct_match = re.search(r'(\d+(?:\.\d+)?)\s*%', line)
                    pct = float(pct_match.group(1)) if pct_match else 0.0
                    status = "حرمان" if pct >= 20.0 or "حرمان" in line else ("إنذار" if pct >= 10.0 else "منتظم")
                    courses.append({
                        "course_name": line,
                        "percentage": pct,
                        "status": status
                    })

        return {
            "student_name": student_name,
            "courses": courses
        }
    except Exception as e:
        print(f"Error extracting attendance: {e}")
        return None

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/search", methods=["POST"])
def search():
    trainee_id = request.form.get("trainee_id", "").strip()
    trainee_id = normalize_digits(trainee_id)

    if not trainee_id:
        return jsonify({"success": False, "message": "يرجى إدخال الرقم التدريبي."})

    if len(trainee_id) < 9 or not trainee_id.isdigit():
        return jsonify({"success": False, "message": "يرجى إدخال الرقم التدريبي كاملاً وبشكل صحيح."})

    # استعلام الجدول
    schedule_pages = find_student_pages(SCHEDULES_PDF_PATH, trainee_id)
    has_schedule = len(schedule_pages) > 0

    # استعلام الغياب
    attendance_data = extract_attendance_data(trainee_id)

    if not has_schedule and not attendance_data:
        return jsonify({"success": False, "message": "لم يتم العثور على سجلات مطابقة لهذا الرقم التدريبي."})

    pages_str = ",".join(map(str, schedule_pages)) if has_schedule else ""
    return jsonify({
        "success": True,
        "has_schedule": has_schedule,
        "schedule_pages": pages_str,
        "attendance": attendance_data
    })

@app.route("/get_schedule_image")
def get_schedule_image():
    pages_param = request.args.get("pages", "")
    if not pages_param or not os.path.exists(SCHEDULES_PDF_PATH):
        return "معلمات غير صالحة", 400

    try:
        page_indices = [int(p) for p in pages_param.split(",") if p.isdigit()]
        doc = fitz.open(SCHEDULES_PDF_PATH)
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

            combined_pix = new_page.get_pixmap(dpi=200)
            img_bytes = combined_pix.tobytes("png")

        return send_file(
            io.BytesIO(img_bytes),
            mimetype="image/png",
            as_attachment=False,
            download_name="schedule.png"
        )
    except Exception as e:
        return str(e), 500

@app.route("/admin", methods=["GET", "POST"])
def admin():
    msg = None
    msg_type = None

    if request.method == "POST":
        action = request.form.get("action")
        if action == "login":
            pwd = request.form.get("password")
            if pwd == ADMIN_PASSWORD:
                session["logged_in"] = True
            else:
                msg = "كلمة المرور غير صحيحة"
                msg_type = "error"

        elif action == "upload_schedule":
            if not session.get("logged_in"):
                return redirect(url_for("admin"))
            file = request.files.get("schedule_file")
            if file and file.filename.endswith(".pdf"):
                file.save(SCHEDULES_PDF_PATH)
                msg = "تم تحديث ملف الجداول بنجاح!"
                msg_type = "success"
            else:
                msg = "يرجى اختيار ملف PDF صالح للجدول"
                msg_type = "error"

        elif action == "upload_attendance":
            if not session.get("logged_in"):
                return redirect(url_for("admin"))
            file = request.files.get("attendance_file")
            if file and file.filename.endswith(".pdf"):
                file.save(ATTENDANCE_PDF_PATH)
                msg = "تم تحديث تقرير الغياب بنجاح!"
                msg_type = "success"
            else:
                msg = "يرجى اختيار ملف PDF صالح لتقرير الغياب"
                msg_type = "error"

    return render_template("admin.html", logged_in=session.get("logged_in", False), msg=msg, msg_type=msg_type)

@app.route("/admin/logout")
def admin_logout():
    session.pop("logged_in", None)
    return redirect(url_for("admin"))

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
