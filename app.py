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

# ========== تنظیمات امنیتی ==========
app.secret_key = os.getenv("SECRET_KEY", "change-this-in-production-12345")

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
    try:
        future = executor.submit(asyncio.run, coro)
        return future.result(timeout=30)
    except Exception as e:
        return f"error: {str(e)}"

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
    except Exception as e:
        return f"error: {str(e)}"
    finally:
        await client.disconnect()

async def sign_in_async(phone, phone_code_hash, code, password=None):
    """تایید کد با استفاده از phone_code_hash"""
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
    except Exception as e:
        return f"error: {str(e)}"
    finally:
        await client.disconnect()

# ========== روت‌های Flask ==========
@app.route('/')
def index():
    session.clear()
    return render_template('login.html')

@app.route('/send_code', methods=['POST'])
def send_code_route():
    phone = request.form.get('phone', '').strip()
    if not phone:
        return "شماره موبایل الزامی است", 400

    result = run_async(send_code_async(phone))
    
    if isinstance(result, str) and result.startswith("error"):
        return f"خطا: {result}", 500
    elif result is None:
        return "شماره موبایل نامعتبر است", 400
    else:
        # result = phone_code_hash
        session['phone'] = phone
        session['phone_code_hash'] = result
        session['code_sent'] = True
        return render_template('code.html')

@app.route('/verify_code', methods=['POST'])
def verify_code():
    code = request.form.get('code', '').strip()
    phone = session.get('phone')
    phone_code_hash = session.get('phone_code_hash')
    code_sent = session.get('code_sent')

    if not phone or not phone_code_hash or not code_sent:
        return redirect(url_for('index'))

    result = run_async(sign_in_async(phone, phone_code_hash, code))

    if result == "need_password":
        # کد رو توی session نگه دار برای مرحله رمز دوم
        session['pending_code'] = code
        return render_template('password.html')
    elif result == "invalid_code":
        return "کد تایید نامعتبر است", 400
    elif result == "code_expired":
        return "کد تایید منقضی شده است. از اول شماره رو وارد کن.", 400
    elif isinstance(result, str) and result.startswith("error"):
        return f"خطا: {result}", 500
    elif isinstance(result, str) and len(result) > 50 and "BA" in result:
        # سشن استرینگ معتبر
        save_user(phone, result)
        session['session_string'] = result
        session.pop('phone_code_hash', None)
        session.pop('code_sent', None)
        session.pop('pending_code', None)
        return redirect(url_for('dashboard'))
    else:
        return f"خطای ناشناخته: {result}", 500

@app.route('/verify_password', methods=['POST'])
def verify_password():
    password = request.form.get('password', '').strip()
    phone = session.get('phone')
    phone_code_hash = session.get('phone_code_hash')
    code = session.get('pending_code')

    if not phone or not phone_code_hash or not code or not password:
        return redirect(url_for('index'))

    result = run_async(sign_in_async(phone, phone_code_hash, code, password))

    if isinstance(result, str) and result.startswith("error"):
        return f"خطا: {result}", 500
    elif result == "invalid_code":
        return "کد تایید نامعتبر است", 400
    elif result == "code_expired":
        return "کد تایید منقضی شده است. از اول شماره رو وارد کن.", 400
    elif isinstance(result, str) and len(result) > 50 and "BA" in result:
        save_user(phone, result)
        session['session_string'] = result
        session.pop('phone_code_hash', None)
        session.pop('code_sent', None)
        session.pop('pending_code', None)
        return redirect(url_for('dashboard'))
    else:
        return f"خطای ناشناخته: {result}", 500

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
    if isinstance(groups, str) and groups.startswith("error"):
        return f"خطا در دریافت گروه‌ها: {groups}", 500
    
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

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('index'))

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)
