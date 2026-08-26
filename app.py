import io
import re
import fitz  # PyMuPDF
from flask import Flask, render_template, request, send_file, jsonify

app = Flask(__name__)
PDF_FILE_PATH = "schedules.pdf"

def normalize_digits(text):
    """تحويل الأرقام العربية إلى إنجليزية"""
    arabic_digits = "٠١٢٣٤٥٦٧٨٩"
    english_digits = "0123456789"
    translation_table = str.maketrans(arabic_digits, english_digits)
    return text.translate(translation_table)

def find_student_pages(pdf_path, trainee_id):
    """
    البحث بمطابقة تامة للرقم التدريبي الكامل فقط لمنع استعراض جداول الآخرين
    """
    clean_id = normalize_digits(trainee_id).strip()
    
    # حماية إضافية: رفض الإدخال إذا كان أقل من 9 خانات
    if len(clean_id) < 9 or not clean_id.isdigit():
        return []

    # نمط regex للمطابقة الدقيقة لرقم المتدرب ككيان مستقل
    # يدعم البحث عن الرقم بشكله المعتاد أو المعكوس
    id_pattern = re.compile(rf'(?<!\d){re.escape(clean_id)}(?!\d)|(?<!\d){re.escape(clean_id[::-1])}(?!\d)')
    
    matched_pages = []
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

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/search", methods=["POST"])
def search():
    trainee_id = request.form.get("trainee_id", "").strip()
    trainee_id = normalize_digits(trainee_id)

    if not trainee_id:
        return jsonify({"success": False, "message": "يرجى إدخال الرقم التدريبي."})

    # شرط عدم قبول جزء من الرقم
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
    if not pages_param:
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

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
