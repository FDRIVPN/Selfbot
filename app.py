import os
import asyncio
import threading
import time
import requests
from concurrent.futures import TimeoutError as FutureTimeoutError
from flask import Flask, render_template, request, redirect, url_for, session, flash

from config import SECRET_KEY, PANEL_PASSWORD, API_ID, API_HASH
from database import init_db, save_user, get_user, get_all_users, delete_user
from clients import send_code, sign_in, check_password, get_groups
from workers import start_worker, stop_worker, start_all_active

app = Flask(__name__)
app.secret_key = SECRET_KEY

# ---------------------- Event Loop ----------------------
LOOP = asyncio.new_event_loop()

def _run_loop():
    asyncio.set_event_loop(LOOP)
    LOOP.run_forever()

threading.Thread(target=_run_loop, daemon=True).start()

def run_async(coro, timeout=90):
    future = asyncio.run_coroutine_threadsafe(coro, LOOP)
    try:
        return future.result(timeout=timeout)
    except FutureTimeoutError:
        future.cancel()
        return "timeout"
    except Exception as e:
        return f"error: {str(e)}"

# ---------------------- Keep Alive ----------------------
def keep_alive():
    while True:
        try:
            url = os.getenv("KEEP_ALIVE_URL", "https://selfbot-production-1d01.up.railway.app/test")
            requests.get(url, timeout=10)
            print("💓 Keep-alive زده شد")
        except Exception as e:
            print(f"Keep-alive خطا: {e}")
        time.sleep(50)

threading.Thread(target=keep_alive, daemon=True).start()

# ---------------------- Init ----------------------
init_db()

# ---------------------- روت تست ----------------------
@app.route("/test")
def test():
    return "OK - Selfbot is alive"

# ---------------------- روت‌های لاگین اکانت ----------------------
@app.route("/")
def index():
    return render_template("login.html")

@app.route("/send_code", methods=["POST"])
def send_code_route():
    phone = request.form.get("phone", "").strip()
    if not phone:
        flash("شماره را وارد کنید", "danger")
        return redirect(url_for("index"))

    result = run_async(send_code(phone))
    if result is True:
        session["phone"] = phone
        return render_template("code.html", phone=phone)
    else:
        flash(str(result), "danger")
        return redirect(url_for("index"))

@app.route("/verify_code", methods=["POST"])
def verify_code():
    phone = session.get("phone")
    code = request.form.get("code", "").strip()
    if not phone:
        flash("نشست منقضی شده", "danger")
        return redirect(url_for("index"))

    result = run_async(sign_in(phone, code))

    if result == "need_password":
        return render_template("password.html", phone=phone)

    elif isinstance(result, str) and len(result) > 40:
        groups = run_async(get_groups(result))
        if not isinstance(groups, list):
            groups = []
        save_user(phone, result, cached_groups=groups, is_active=False)
        session.pop("phone", None)
        flash("اکانت با موفقیت اضافه شد", "success")
        return redirect(url_for("admin"))
    else:
        flash(str(result), "danger")
        return redirect(url_for("index"))

@app.route("/verify_password", methods=["POST"])
def verify_password():
    phone = session.get("phone")
    password = request.form.get("password", "").strip()
    if not phone:
        return redirect(url_for("index"))

    result = run_async(check_password(phone, password))

    if isinstance(result, str) and len(result) > 40:
        groups = run_async(get_groups(result))
        if not isinstance(groups, list):
            groups = []
        save_user(phone, result, cached_groups=groups, is_active=False)
        session.pop("phone", None)
        flash("اکانت با موفقیت اضافه شد", "success")
        return redirect(url_for("admin"))
    else:
        flash(str(result), "danger")
        return redirect(url_for("index"))

# ---------------------- پنل ادمین ----------------------
@app.route("/admin", methods=["GET", "POST"])
def admin():
    if request.method == "POST":
        if request.form.get("password") == PANEL_PASSWORD:
            session["is_admin"] = True
            start_all_active(LOOP)
            return redirect(url_for("dashboard"))
        flash("رمز اشتباه است", "danger")

    if session.get("is_admin"):
        return redirect(url_for("dashboard"))

    return render_template("admin_login.html")

@app.route("/dashboard")
def dashboard():
    if not session.get("is_admin"):
        return redirect(url_for("admin"))

    managed = session.get("managed_phone")
    all_users = get_all_users()

    if not managed and all_users:
        managed = all_users[0]["phone"]
        session["managed_phone"] = managed

    user = get_user(managed) if managed else None
    groups = []

    if user:
        groups = user.get("cached_groups") or []

    return render_template(
        "dashboard.html",
        managed_phone=managed,
        user=user,
        groups=groups,
        all_users=all_users
    )

@app.route("/switch/<phone>")
def switch_user(phone):
    if not session.get("is_admin"):
        return redirect(url_for("admin"))
    if get_user(phone):
        session["managed_phone"] = phone
    return redirect(url_for("dashboard"))

@app.route("/save", methods=["POST"])
def save_settings():
    if not session.get("is_admin"):
        return redirect(url_for("admin"))

    phone = session.get("managed_phone")
    user = get_user(phone)
    if not user:
        return redirect(url_for("dashboard"))

    selected = request.form.getlist("groups")

    manual_id = request.form.get("manual_group_id", "").strip()
    if manual_id:
        for part in manual_id.replace(" ", "").split(","):
            clean_id = "".join(c for c in part if c.isdigit() or c == "-")
            if clean_id and clean_id not in selected:
                selected.append(clean_id)

    meow = request.form.get("meow_enabled") == "on"
    fish = request.form.get("fish_enabled") == "on"
    rescue = request.form.get("rescue_enabled") == "on"
    active = request.form.get("is_active") == "on"

    harvest_button = request.form.get("harvest_button", "").strip() or "برداشت میو پوینت ها 🧲"
    rescue_button = request.form.get("rescue_button", "").strip() or "نجات پیشی خیابونی 🐱 🐈"

    try:
        meow_interval = int(request.form.get("meow_interval") or 300)
    except:
        meow_interval = 300
    try:
        fish_interval = int(request.form.get("fish_interval") or 600)
    except:
        fish_interval = 600

    save_user(
        phone,
        user["session_string"],
        selected_groups=selected,
        meow_enabled=meow,
        fish_enabled=fish,
        rescue_enabled=rescue,
        is_active=active,
        cached_groups=user.get("cached_groups"),
        harvest_button=harvest_button,
        rescue_button=rescue_button,
        meow_interval=meow_interval,
        fish_interval=fish_interval,
    )

    if active:
        start_worker(phone, LOOP)
    else:
        stop_worker(phone)

    flash("تنظیمات با موفقیت ذخیره شد", "success")
    return redirect(url_for("dashboard"))

@app.route("/refresh_groups", methods=["POST"])
def refresh_groups():
    if not session.get("is_admin"):
        return redirect(url_for("admin"))

    phone = session.get("managed_phone")
    user = get_user(phone)
    if not user:
        return redirect(url_for("dashboard"))

    groups = run_async(get_groups(user["session_string"]), timeout=60)

    if isinstance(groups, list):
        save_user(
            phone,
            user["session_string"],
            selected_groups=user["selected_groups"],
            meow_enabled=user["meow_enabled"],
            fish_enabled=user["fish_enabled"],
            rescue_enabled=user.get("rescue_enabled", True),
            is_active=user["is_active"],
            cached_groups=groups,
            harvest_button=user.get("harvest_button"),
            rescue_button=user.get("rescue_button"),
            meow_interval=user.get("meow_interval", 300),
            fish_interval=user.get("fish_interval", 600),
        )
        flash(f"{len(groups)} گروه دریافت شد", "success")
    else:
        flash(f"خطا در دریافت گروه‌ها: {groups}", "danger")

    return redirect(url_for("dashboard"))

@app.route("/remove", methods=["POST"])
def remove_user():
    if not session.get("is_admin"):
        return redirect(url_for("admin"))

    phone = request.form.get("phone")
    stop_worker(phone)
    delete_user(phone)

    if session.get("managed_phone") == phone:
        session["managed_phone"] = None

    flash("اکانت حذف شد", "success")
    return redirect(url_for("dashboard"))

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("admin"))

# ---------------------- Error Handler ----------------------
@app.errorhandler(Exception)
def handle_exception(e):
    import traceback
    print("=" * 50)
    print("ERROR:", str(e))
    traceback.print_exc()
    print("=" * 50)
    return f"""
    <div style="direction:rtl;text-align:center;margin-top:40px;font-family:tahoma;">
        <h2 style="color:red;">خطایی رخ داد</h2>
        <pre style="direction:ltr;text-align:left;background:#f5f5f5;padding:20px;
                    border-radius:8px;max-width:900px;margin:20px auto;overflow:auto;">
{traceback.format_exc()}
        </pre>
    </div>
    """, 500

# ---------------------- Start ----------------------
if __name__ == "__main__":
    start_all_active(LOOP)
    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
