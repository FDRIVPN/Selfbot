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
app.secret_key = os.getenv("SECRET_KEY", os.urandom(24).hex())

# ========== متغیرهای محیطی ==========
API_ID = int(os.getenv("API_ID", 0))
API_HASH = os.getenv("API_HASH", "")

if not API_ID or not API_HASH:
    raise ValueError("API_ID and API_HASH must be set in Railway")

# ========== دیتابیس ==========
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

# ========== مدیریت asyncio ==========
executor = ThreadPoolExecutor(max_workers=4)

def run_async(coro):
    future = executor.submit(asyncio.run, coro)
    return future.result()

# ========== توابع Pyrogram ==========
async def send_code_async(phone):
    """ارسال کد و برگرداندن phone_code_hash"""
    client = Client("temp", api_id=API_ID, api_hash=API_HASH, in_memory=True)
    await client.connect()
    try:
        sent = await client.send_code(phone)
        return sent.phone_code_hash
    except PhoneNumberInvalid:
        return None
    finally:
        await client.disconnect()

async def sign_in_async(phone, code, phone_code_hash, password=None):
    """ورود با استفاده از phone_code_hash دریافت شده از session"""
    client = Client("temp", api_id=API_ID, api_hash=API_HASH, in_memory=True)
    await client.connect()
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
    return session_string

async def check_password_async(phone, phone_code_hash, password):
    """بررسی رمز دوم با استفاده از hash موجود"""
    client = Client("temp", api_id=API_ID, api_hash=API_HASH, in_memory=True)
    await client.connect()
    try:
        # ابتدا sign_in را با کد null (که از قبل تایید شده) صدا می‌زنیم
        # ولی روش بهتر اینه که از client موجود استفاده کنیم، اما چون کلاینت جدید می‌سازیم،
        # باید دوباره sign_in بزنیم با کد قبلی که در session ذخیره نشده.
        # راه‌حل: از کلاینت نگهداری کنیم، ولی برای سادگی از روش زیر استفاده می‌کنیم:
        # ما phone_code_hash رو داریم، کد رو هم از session می‌گیریم.
        # پس بهتره این تابع رو حذف کنیم و همه‌چیز رو در sign_in_async با پارامتر password مدیریت کنیم.
        pass
    except Exception as e:
        return f"error: {str(e)}"
    finally:
        await client.disconnect()

# برای سادگی، sign_in_async را با پارامتر password کامل می‌کنیم:
async def sign_in_async_complete(phone, code, phone_code_hash, password=None):
    """ورود کامل با پشتیبانی از رمز دوم"""
    client = Client("temp", api_id=API_ID, api_hash=API_HASH, in_memory=True)
    await client.connect()
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
            await client.disconnect()
            return "need_password"
    except PhoneCodeInvalid:
        await client.disconnect()
        return "invalid_code"
    except PhoneCodeExpired:
        await client.disconnect()
        return "code_expired"
    except Exception as e:
        await client.disconnect()
        return f"error: {str(e)}"

    session_string = await client.export_session_string()
    await client.disconnect()
    return session_string

async def get_groups_async(session_string):
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
    session.clear()
    return render_template('login.html')

@app.route('/send_code', methods=['POST'])
def send_code_route():
    phone = request.form.get('phone')
    if not phone:
        return "شماره موبایل الزامی است", 400

    phone_code_hash = run_async(send_code_async(phone))
    if phone_code_hash:
        session['phone'] = phone
        session['phone_code_hash'] = phone_code_hash
        return render_template('code.html')
    else:
        return "شماره موبایل نامعتبر است", 400

@app.route('/verify_code', methods=['POST'])
def verify_code():
    code = request.form.get('code')
    phone = session.get('phone')
    phone_code_hash = session.get('phone_code_hash')

    if not phone or not phone_code_hash:
        return redirect(url_for('index'))

    result = run_async(sign_in_async_complete(phone, code, phone_code_hash))

    if result == "need_password":
        # ذخیره کد در session برای مرحله بعد
        session['temp_code'] = code
        return render_template('password.html')
    elif result == "invalid_code":
        return "کد تایید نامعتبر است", 400
    elif result == "code_expired":
        return "کد تایید منقضی شده است. دوباره درخواست کنید.", 400
    elif isinstance(result, str) and not result.startswith("error") and result not in ["need_password", "invalid_code", "code_expired"]:
        # اینجا سشن استرینگ معتبر است
        save_user(phone, result)
        session['session_string'] = result
        # پاک کردن داده‌های موقت
        session.pop('phone_code_hash', None)
        session.pop('temp_code', None)
        return redirect(url_for('dashboard'))
    else:
        return f"خطا: {result}", 500

@app.route('/verify_password', methods=['POST'])
def verify_password():
    phone = session.get('phone')
    phone_code_hash = session.get('phone_code_hash')
    code = session.get('temp_code')
    password = request.form.get('password')

    if not phone or not phone_code_hash or not code or not password:
        return "اطلاعات کامل نیست", 400

    result = run_async(sign_in_async_complete(phone, code, phone_code_hash, password))

    if isinstance(result, str) and not result.startswith("error") and result not in ["need_password", "invalid_code", "code_expired"]:
        save_user(phone, result)
        session['session_string'] = result
        session.pop('phone_code_hash', None)
        session.pop('temp_code', None)
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
