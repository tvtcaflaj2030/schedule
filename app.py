import io
import re
import os
import json
import fitz  # PyMuPDF
from flask import Flask, render_template, request, send_file, jsonify, redirect, url_for, session

app = Flask(__name__)
app.secret_key = "secret_admin_key_turki_aflaj"
ADMIN_PASSWORD = "."
PDF_FILE_PATH = "schedules.pdf"
SERVICES_FILE = "services.json"

def normalize_digits(text):
    """تحويل الأرقام العربية إلى إنجليزية"""
    arabic_digits = "٠١٢٣٤٥٦٧٨٩"
    english_digits = "0123456789"
    translation_table = str.maketrans(arabic_digits, english_digits)
    return text.translate(translation_table)

def find_student_pages(pdf_path, trainee_id):
    """البحث بمطابقة تامة للرقم التدريبي الكامل فقط"""
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

def load_services():
    """تحميل قائمة الخدمات من ملف JSON، وإنشاء الافتراضية إذا لم يتوفر الملف"""
    if not os.path.exists(SERVICES_FILE):
        default_services = [
            {"title": "التسجيل الذاتي للمتدربين", "url": "https://ugate.tvtc.gov.sa/AFrontGate/", "icon": "fa-id-card"},
            {"title": "الخدمات الذاتية للمتدربين", "url": "https://rayat.tvtc.gov.sa", "icon": "fa-user-gear"},
            {"title": "عرض جدول المتدرب", "url": "/", "icon": "fa-table-cells"},
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
    """حفظ تعديلات الخدمات في ملف JSON"""
    with open(SERVICES_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/services")
def services():
    services_data = load_services()
    return render_template("services.html", services=services_data)

@app.route("/search", methods=["POST"])
def search():
    trainee_id = request.form.get("trainee_id", "").strip()
    trainee_id = normalize_digits(trainee_id)

    if not trainee_id:
        return jsonify({"success": False, "message": "يرجى إدخال الرقم التدريبي."})

    if len(trainee_id) < 9 or not trainee_id.isdigit():
        return jsonify({"success": False, "message": "يرجى إدخال الرقم التدريبي كاملاً وبشكل صحيح."})

    pages = find_student_pages(PDF_FILE_PATH, trainee_id)
    if not pages:
        return jsonify({"success": False, "message": "لم يتم العثور على جدول مطابق لهذا الرقم التدريبي."})

    pages_str = ",".join(map(str, pages))
    return jsonify({"success": True, "pages": pages_str})

@app.route("/get_schedule_image")
def get_schedule_image():
    pages_param = request.args.get("pages", "")
    if not pages_param or not os.path.exists(PDF_FILE_PATH):
        return "معلمات غير صالحة", 400

    try:
        page_indices = [int(p) for p in pages_param.split(",") if p.isdigit()]
        doc = fitz.open(PDF_FILE_PATH)
        
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
        print(f"Error generating image: {e}")
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

        elif action == "upload":
            if not session.get("logged_in"):
                return redirect(url_for("admin"))
            
            uploaded_file = request.files.get("pdf_file")
            if uploaded_file and uploaded_file.filename.endswith(".pdf"):
                uploaded_file.save(PDF_FILE_PATH)
                msg = "تم رفع وتحديث ملف الجداول بنجاح!"
                msg_type = "success"
            else:
                msg = "يرجى اختيار ملف PDF صالح"
                msg_type = "error"

        elif action == "save_services":
            if not session.get("logged_in"):
                return redirect(url_for("admin"))
            
            titles = request.form.getlist("title[]")
            urls = request.form.getlist("url[]")
            icons = request.form.getlist("icon[]")
            
            updated_services = []
            for t, u, i in zip(titles, urls, icons):
                if t.strip() and u.strip():
                    updated_services.append({
                        "title": t.strip(),
                        "url": u.strip(),
                        "icon": i.strip() if i.strip() else "fa-link"
                    })
            
            save_services(updated_services)
            msg = "تم تحديث وحفظ خدمات المتدربين بنجاح!"
            msg_type = "success"

    services_list = load_services() if session.get("logged_in") else []
    return render_template("admin.html", 
                           logged_in=session.get("logged_in", False), 
                           msg=msg, 
                           msg_type=msg_type,
                           services=services_list)

@app.route("/admin/logout")
def admin_logout():
    session.pop("logged_in", None)
    return redirect(url_for("admin"))

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
