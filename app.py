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

# ========== دریافت API_ID و API_HASH از محیط ==========
API_ID = int(os.getenv("API_ID", 0))
API_HASH = os.getenv("API_HASH", "")

if not API_ID or not API_HASH:
    raise ValueError("API_ID و API_HASH باید در متغیرهای محیطی تنظیم شوند")

# ========== دیتابیس ==========
def init_db():
    conn = sqlite3.connect('users.db')
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
    conn = sqlite3.connect('users.db')
    c = conn.cursor()
    c.execute(
        'INSERT OR REPLACE INTO users (phone, session_string, selected_groups) VALUES (?, ?, ?)',
        (phone, session_string, json.dumps(selected_groups) if selected_groups else None)
    )
    conn.commit()
    conn.close()

def get_user(phone):
    conn = sqlite3.connect('users.db')
    c = conn.cursor()
    c.execute('SELECT session_string, selected_groups FROM users WHERE phone=?', (phone,))
    row = c.fetchone()
    conn.close()
    if row:
        return row[0], json.loads(row[1]) if row[1] else []
    return None, []

# ========== توابع احراز هویت ==========
async def send_code(phone):
    """ارسال کد تایید به شماره موبایل"""
    client = Client("temp", api_id=API_ID, api_hash=API_HASH, in_memory=True)
    await client.connect()
    try:
        await client.send_code(phone)
        await client.disconnect()
        return True
    except PhoneNumberInvalid:
        await client.disconnect()
        return False

async def sign_in(phone, code, password=None):
    """ورود با کد تایید و رمز دوم (اختیاری)"""
    client = Client("temp", api_id=API_ID, api_hash=API_HASH, in_memory=True)
    await client.connect()
    try:
        await client.sign_in(phone, code)
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

    # گرفتن سشن استرینگ
    session_string = await client.export_session_string()
    await client.disconnect()
    return session_string

async def get_groups(session_string):
    """دریافت لیست گروه‌های کاربر با استفاده از سشن"""
    client = Client("session", session_string=session_string, api_id=API_ID, api_hash=API_HASH, in_memory=True)
    await client.start()
    groups = []
    async for dialog in client.get_dialogs():
        if dialog.chat.type in ["group", "supergroup"]:
            groups.append({
                "id": str(dialog.chat.id),
                "title": dialog.chat.title or "بدون نام",
                "members": dialog.chat.members_count or 0
            })
    await client.disconnect()
    return groups

# ========== روت‌های Flask ==========
@app.route('/')
def index():
    return render_template('login.html')

@app.route('/send_code', methods=['POST'])
def send_code_route():
    phone = request.form.get('phone')
    if not phone:
        return "شماره موبایل الزامی است", 400

    # ذخیره شماره در سشن
    session['phone'] = phone

    # ارسال کد
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    result = loop.run_until_complete(send_code(phone))

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

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    result = loop.run_until_complete(sign_in(phone, code))

    if result == "need_password":
        return render_template('password.html')
    elif result == "invalid_code":
        return "کد تایید نامعتبر است", 400
    elif result == "code_expired":
        return "کد تایید منقضی شده است. دوباره تلاش کنید.", 400
    elif isinstance(result, str) and result.startswith("BA"):
        # سشن استرینگ دریافت شد
        session_string = result
        save_user(phone, session_string)
        session['session_string'] = session_string
        return redirect(url_for('dashboard'))
    else:
        return "خطای ناشناخته در احراز هویت", 500

@app.route('/verify_password', methods=['POST'])
def verify_password():
    password = request.form.get('password')
    phone = session.get('phone')
    if not phone:
        return redirect(url_for('index'))

    # کد رو از سشن بگیر (قبلاً ذخیره نشده، از فرم دوباره می‌گیریم)
    # برای سادگی، از کاربر دوباره کد می‌خوایم؟
    # بهتره یک روش بهتر پیاده‌سازی کنیم، اما فعلاً فرض می‌کنیم کد قبلاً توی session نیست
    # پس یک فرم جداگانه با کد و رمز دوم می‌سازیم
    # ساده‌ترین راه: دوباره کد و رمز رو بگیریم
    return "این بخش نیاز به بازطراحی دارد", 501

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

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    groups = loop.run_until_complete(get_groups(session_string))

    # دریافت گروه‌های ذخیره‌شده قبلی
    _, selected = get_user(phone)
    return render_template('dashboard.html', groups=groups, selected=selected)

@app.route('/save_groups', methods=['POST'])
def save_groups():
    selected = request.form.getlist('groups')
    phone = session.get('phone')
    if not phone:
        return redirect(url_for('index'))

    # ذخیره گروه‌های انتخاب‌شده
    _, _ = get_user(phone)
    save_user(phone, session.get('session_string'), selected)

    return "تنظیمات با موفقیت ذخیره شد! <a href='/dashboard'>بازگشت به پنل</a>"

# ========== اجرا ==========
if __name__ == '__main__':
    init_db()
    app.run(host='0.0.0.0', port=5000, debug=True)
