import os
import json
import sqlite3
import asyncio
from concurrent.futures import ThreadPoolExecutor
from flask import Flask, render_template, request, redirect, url_for, session
from pyrogram import Client
from pyrogram.errors import (
    PhoneNumberInvalid,
    PhoneCodeInvalid,
    PhoneCodeExpired,
    SessionPasswordNeeded
)

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", os.urandom(24).hex())  # از env بخون

# ========== متغیرهای محیطی ==========
API_ID = int(os.getenv("API_ID", 0))
API_HASH = os.getenv("API_HASH", "")

if not API_ID or not API_HASH:
    raise ValueError("API_ID and API_HASH must be set in Railway")

# ========== دیتابیس (در مسیر پایدار) ==========
DB_DIR = "data"
DB_PATH = os.path.join(DB_DIR, "users.db")

if not os.path.exists(DB_DIR):
    os.makedirs(DB_DIR)

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS users (
            phone TEXT PRIMARY KEY,
            session_string TEXT,
            selected_groups TEXT
        )
    ''')
    conn.commit()
    conn.close()

def save_user(phone, session_string, selected_groups=None):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        'INSERT OR REPLACE INTO users (phone, session_string, selected_groups) VALUES (?, ?, ?)',
        (phone, session_string, json.dumps(selected_groups) if selected_groups else None)
    )
    conn.commit()
    conn.close()

def get_user(phone):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('SELECT session_string, selected_groups FROM users WHERE phone=?', (phone,))
    row = c.fetchone()
    conn.close()
    if row:
        return row[0], json.loads(row[1]) if row[1] else []
    return None, []

init_db()

# ========== مدیریت asyncio در Flask (با ThreadPoolExecutor) ==========
executor = ThreadPoolExecutor(max_workers=4)

def run_async(coro):
    """اجرای تابع async در یک thread جداگانه برای جلوگیری از تداخل با Gunicorn"""
    future = executor.submit(asyncio.run, coro)
    return future.result()

# ========== دیکشنری موقت برای ذخیره phone_code_hash و کلاینت ==========
temp_data = {}  # { phone: {"hash": str, "client": Client} }

# ========== توابع Pyrogram ==========
async def send_code_async(phone):
    """ارسال کد و ذخیره phone_code_hash و کلاینت"""
    client = Client("temp", api_id=API_ID, api_hash=API_HASH, in_memory=True)
    await client.connect()
    try:
        sent = await client.send_code(phone)
        temp_data[phone] = {
            "hash": sent.phone_code_hash,
            "client": client
        }
        return True
    except PhoneNumberInvalid:
        await client.disconnect()
        return False
    except Exception as e:
        await client.disconnect()
        return False

async def sign_in_async(phone, code, password=None):
    """ورود با استفاده از phone_code_hash ذخیره‌شده و کلاینت موجود"""
    data = temp_data.get(phone)
    if not data or "client" not in data:
        return "error: session expired, please resend code"

    client = data["client"]
    phone_code_hash = data["hash"]

    try:
        await client.sign_in(
            phone_number=phone,
            phone_code_hash=phone_code_hash,
            phone_code=code
        )
    except SessionPasswordNeeded:
        if password:
            await client.check_password(password)
        else:
            return "need_password"
    except PhoneCodeInvalid:
        return "invalid_code"
    except PhoneCodeExpired:
        return "code_expired"
    except Exception as e:
        return f"error: {str(e)}"

    session_string = await client.export_session_string()
    await client.disconnect()
    # پاک کردن داده‌های موقت
    temp_data.pop(phone, None)
    return session_string

async def get_groups_async(session_string):
    """دریافت لیست گروه‌ها با سشن استرینگ"""
    client = Client("session", session_string=session_string, api_id=API_ID, api_hash=API_HASH, in_memory=True)
    await client.start()
    try:
        groups = []
        async for dialog in client.get_dialogs():
            if dialog.chat.type in ["group", "supergroup"]:
                groups.append({
                    "id": str(dialog.chat.id),
                    "title": dialog.chat.title or "بدون نام",
                    "members": dialog.chat.members_count or 0
                })
        return groups
    finally:
        await client.disconnect()

# ========== روت‌های Flask ==========
@app.route('/')
def index():
    # پاک کردن session قبلی
    session.clear()
    return render_template('login.html')

@app.route('/send_code', methods=['POST'])
def send_code_route():
    phone = request.form.get('phone')
    if not phone:
        return "شماره موبایل الزامی است", 400
    session['phone'] = phone
    result = run_async(send_code_async(phone))
    if result:
        return render_template('code.html')
    else:
        return "شماره موبایل نامعتبر است", 400

@app.route('/verify_code', methods=['POST'])
def verify_code():
    code = request.form.get('code')
    phone = session.get('phone')
    if not phone:
        return redirect(url_for('index'))
    result = run_async(sign_in_async(phone, code))
    if result == "need_password":
        # کلاینت هنوز توی temp_data هست، فقط رمز دوم رو بگیر
        return render_template('password.html')
    elif result == "invalid_code":
        return "کد تایید نامعتبر است", 400
    elif result == "code_expired":
        return "کد تایید منقضی شده است. دوباره درخواست کنید.", 400
    elif isinstance(result, str) and "BA" in result and len(result) > 50:
        # چک درست سشن استرینگ (نه فقط startswith)
        save_user(phone, result)
        session['session_string'] = result
        return redirect(url_for('dashboard'))
    else:
        return f"خطا: {result}", 500

@app.route('/verify_password', methods=['POST'])
def verify_password():
    phone = session.get('phone')
    password = request.form.get('password')
    if not phone or not password:
        return "اطلاعات کامل نیست", 400

    # ما نیاز به کد نداریم، فقط رمز دوم رو به sign_in_async با کد null می‌فرستیم
    # ولی sign_in_async نیاز به code داره، پس از session یا دیتابیس موقت می‌خونیم
    # ساده‌ترین راه: از کاربر دوباره کد نگیریم، بلکه از session بخونیم
    # ولی چون user قبلاً کد رو وارد کرده، می‌تونیم از temp_data استفاده کنیم
    # اما sign_in_async با code=None کار نمی‌کنه. راه حل: کد رو از session بگیریم
    # ولی ما کد رو ذخیره نکردیم. پس بهتره رمز دوم رو با یه تابع جداگانه هندل کنیم.

    # راه‌حل: تابع جدید برای رمز دوم
    async def check_password_async(phone, password):
        data = temp_data.get(phone)
        if not data or "client" not in data:
            return "error: session expired, please resend code"
        client = data["client"]
        try:
            await client.check_password(password)
        except Exception as e:
            return f"error: {str(e)}"
        session_string = await client.export_session_string()
        await client.disconnect()
        temp_data.pop(phone, None)
        return session_string

    result = run_async(check_password_async(phone, password))
    if isinstance(result, str) and "BA" in result and len(result) > 50:
        save_user(phone, result)
        session['session_string'] = result
        return redirect(url_for('dashboard'))
    else:
        return f"خطا: {result}", 500

@app.route('/dashboard')
def dashboard():
    phone = session.get('phone')
    session_string = session.get('session_string')
    if not session_string:
        s, _ = get_user(phone)
        if s:
            session_string = s
            session['session_string'] = session_string
        else:
            return redirect(url_for('index'))
    groups = run_async(get_groups_async(session_string))
    _, selected = get_user(phone)
    return render_template('dashboard.html', groups=groups, selected=selected)

@app.route('/save_groups', methods=['POST'])
def save_groups():
    selected = request.form.getlist('groups')
    phone = session.get('phone')
    if not phone:
        return redirect(url_for('index'))
    save_user(phone, session.get('session_string'), selected)
    return "تنظیمات با موفقیت ذخیره شد! <a href='/dashboard'>بازگشت</a>"

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)
