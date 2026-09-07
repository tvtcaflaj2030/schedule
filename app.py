SERVICES_FILE = "services.json"

def load_services():
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
    with open(SERVICES_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

@app.route("/services")
def services_page():
    services = load_services()
    return render_template("services.html", services=services)

# دمج حفظ الخدمات في مسار /admin
@app.route("/admin/save_services", methods=["POST"])
def admin_save_services():
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
    return redirect(url_for("admin"))
