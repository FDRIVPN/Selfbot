import os
import json
import sqlite3
import asyncio
from flask import Flask, render_template, request, redirect, url_for, session
from pyrogram import Client
from pyrogram.errors import (
    PhoneNumberInvalid,
    PhoneCodeInvalid,
    SessionPasswordNeeded,
    PhoneCodeExpired
)

app = Flask(__name__)
app.secret_key = os.urandom(24)

# ========== متغیرهای محیطی ==========
API_ID = int(os.getenv("API_ID", 0))
API_HASH = os.getenv("API_HASH", "")

if not API_ID or not API_HASH:
    raise ValueError("API_ID و API_HASH باید در Railway تنظیم شوند")

# ========== دیتابیس ==========
DB_PATH = "/tmp/users.db" if os.getenv("RAILWAY_ENV") else "users.db"

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

# ========== توابع کمکی برای اجرای async ==========
def run_async(coro):
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        return loop.run_until_complete(coro)
    finally:
        loop.close()

# ========== توابع احراز هویت ==========
async def send_code_async(phone):
    client = Client("temp", api_id=API_ID, api_hash=API_HASH, in_memory=True)
    await client.connect()
    try:
        await client.send_code(phone)
        return True
    except PhoneNumberInvalid:
        return False
    finally:
        await client.disconnect()

async def sign_in_async(phone, code, password=None):
    client = Client("temp", api_id=API_ID, api_hash=API_HASH, in_memory=True)
    await client.connect()
    try:
        # درست: await client.sign_in(phone, code)
        await client.sign_in(phone, code)
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
        print(f"❌ خطا در sign_in: {e}")
        return f"error: {str(e)}"

    session_string = await client.export_session_string()
    await client.disconnect()
    return session_string

async def get_groups_async(session_string):
    client = Client("session", session_string=session_string, api_id=API_ID, api_hash=API_HASH, in_memory=True)
    try:
        await client.start()
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

# ========== روت‌ها ==========
@app.route('/')
def index():
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
        return render_template('password.html')
    elif result == "invalid_code":
        return "کد تایید نامعتبر است", 400
    elif result == "code_expired":
        return "کد تایید منقضی شده است", 400
    elif isinstance(result, str) and result.startswith("BA"):
        # ذخیره سشن و هدایت به داشبورد
        save_user(phone, result)
        session['session_string'] = result
        return redirect(url_for('dashboard'))
    else:
        return f"خطا: {result}", 500

@app.route('/verify_password', methods=['POST'])
def verify_password():
    phone = session.get('phone')
    code = request.form.get('code')
    password = request.form.get('password')
    if not phone or not code or not password:
        return "اطلاعات کامل نیست", 400
    result = run_async(sign_in_async(phone, code, password))
    if isinstance(result, str) and result.startswith("BA"):
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

# ========== اجرا ==========
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)
