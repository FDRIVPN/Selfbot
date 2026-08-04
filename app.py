import os
import json
import sqlite3
import asyncio
import threading
import re
from concurrent.futures import TimeoutError as FutureTimeoutError
from flask import Flask, render_template, request, redirect, url_for, session
from pyrogram import Client, filters
from pyrogram.types import Message
from pyrogram.enums import ChatType
from pyrogram.errors import (
    PhoneNumberInvalid,
    PhoneCodeInvalid,
    PhoneCodeExpired,
    SessionPasswordNeeded,
    PeerIdInvalid
)

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "change-this-in-production-12345")

API_ID = int(os.getenv("API_ID", 0))
API_HASH = os.getenv("API_HASH", "")

if not API_ID or not API_HASH:
    raise ValueError("API_ID and API_HASH must be set in Railway")

# ========== شناسه ربات توکنی ==========
BOT_USER_ID = 8299996037

# ========== دیتابیس ==========
DB_DIR = "/app/data" if os.getenv("RAILWAY_ENV") else "data"
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
            selected_groups TEXT,
            meow_enabled INTEGER DEFAULT 1,
            fish_enabled INTEGER DEFAULT 1,
            smuggle_enabled INTEGER DEFAULT 1,
            is_active INTEGER DEFAULT 1
        )
    ''')
    for col in ["meow_enabled", "fish_enabled", "smuggle_enabled", "is_active"]:
        try:
            c.execute(f"ALTER TABLE users ADD COLUMN {col} INTEGER DEFAULT 1")
        except sqlite3.OperationalError:
            pass
    conn.commit()
    conn.close()

def save_user(phone, session_string, selected_groups=None, meow_enabled=True, fish_enabled=True, smuggle_enabled=True, is_active=True):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        '''INSERT OR REPLACE INTO users 
        (phone, session_string, selected_groups, meow_enabled, fish_enabled, smuggle_enabled, is_active)
        VALUES (?, ?, ?, ?, ?, ?, ?)''',
        (phone, session_string, json.dumps(selected_groups) if selected_groups else None,
         1 if meow_enabled else 0, 1 if fish_enabled else 0, 1 if smuggle_enabled else 0,
         1 if is_active else 0)
    )
    conn.commit()
    conn.close()
    print(f"💾 اطلاعات کاربر {phone} ذخیره شد")

def get_user(phone):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('SELECT session_string, selected_groups, meow_enabled, fish_enabled, smuggle_enabled, is_active FROM users WHERE phone=?', (phone,))
    row = c.fetchone()
    conn.close()
    if row:
        return row[0], json.loads(row[1]) if row[1] else [], bool(row[2]), bool(row[3]), bool(row[4]), bool(row[5])
    return None, [], True, True, True, False

def get_all_users():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('SELECT phone, session_string, selected_groups, meow_enabled, fish_enabled, smuggle_enabled, is_active FROM users')
    rows = c.fetchall()
    conn.close()
    users = []
    for row in rows:
        users.append({
            "phone": row[0],
            "session_string": row[1],
            "selected_groups": json.loads(row[2]) if row[2] else [],
            "meow_enabled": bool(row[3]),
            "fish_enabled": bool(row[4]),
            "smuggle_enabled": bool(row[5]),
            "is_active": bool(row[6])
        })
    return users

def delete_user(phone):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('DELETE FROM users WHERE phone=?', (phone,))
    conn.commit()
    conn.close()

init_db()
print(f"✅ دیتابیس در {DB_PATH} آماده شد")

# ========== Event Loop دائمی ==========
ASYNC_LOOP = asyncio.new_event_loop()

def _async_loop_worker():
    asyncio.set_event_loop(ASYNC_LOOP)
    ASYNC_LOOP.run_forever()

threading.Thread(
    target=_async_loop_worker,
    name="pyrogram-event-loop",
    daemon=True
).start()

def run_async(coro, timeout=120):
    future = asyncio.run_coroutine_threadsafe(coro, ASYNC_LOOP)
    try:
        return future.result(timeout=timeout)
    except FutureTimeoutError:
        future.cancel()
        return "error: operation timed out"
    except Exception as e:
        return f"error: {str(e)}"

# ========== متغیرهای سراسری ==========
active_clients = {}
selfbot_tasks = {}
meow_timers = {}

# ========== توابع Pyrogram ==========
async def send_code_async(phone):
    if phone in active_clients:
        try:
            await active_clients[phone]["client"].disconnect()
        except:
            pass
        active_clients.pop(phone, None)

    client = Client("temp", api_id=API_ID, api_hash=API_HASH, in_memory=True)
    await client.connect()
    try:
        sent = await client.send_code(phone)
        active_clients[phone] = {
            "client": client,
            "hash": sent.phone_code_hash
        }
        return sent.phone_code_hash
    except PhoneNumberInvalid:
        await client.disconnect()
        active_clients.pop(phone, None)
        return None
    except Exception as e:
        await client.disconnect()
        active_clients.pop(phone, None)
        return f"error: {str(e)}"

async def sign_in_async(phone, code):
    if phone not in active_clients:
        return "error: session expired, please resend code"

    data = active_clients[phone]
    client = data["client"]
    phone_code_hash = data["hash"]

    try:
        await client.sign_in(
            phone_number=phone,
            phone_code_hash=phone_code_hash,
            phone_code=code
        )
        session_string = await client.export_session_string()
        await client.disconnect()
        active_clients.pop(phone, None)
        return session_string
    except SessionPasswordNeeded:
        return "need_password"
    except PhoneCodeInvalid:
        await client.disconnect()
        active_clients.pop(phone, None)
        return "invalid_code"
    except PhoneCodeExpired:
        await client.disconnect()
        active_clients.pop(phone, None)
        return "code_expired"
    except Exception as e:
        await client.disconnect()
        active_clients.pop(phone, None)
        return f"error: {str(e)}"

async def check_password_async(phone, password):
    if phone not in active_clients:
        return "error: session expired, please resend code"

    client = active_clients[phone]["client"]
    try:
        await client.check_password(password)
        session_string = await client.export_session_string()
        await client.disconnect()
        active_clients.pop(phone, None)
        return session_string
    except Exception as e:
        await client.disconnect()
        active_clients.pop(phone, None)
        return f"error: {str(e)}"

async def get_groups_async(session_string):
    client = Client(
        "session",
        session_string=session_string,
        api_id=API_ID,
        api_hash=API_HASH,
        in_memory=True,
        no_updates=True
    )
    try:
        await client.start()
        groups = []
        async for dialog in client.get_dialogs():
            if dialog.chat.type in (ChatType.GROUP, ChatType.SUPERGROUP):
                groups.append({
                    "id": str(dialog.chat.id),
                    "title": dialog.chat.title or "بدون نام",
                    "members": dialog.chat.members_count or 0
                })
        return groups
    except Exception as e:
        return f"error: {str(e)}"
    finally:
        try:
            if client.is_initialized:
                await client.stop()
            elif client.is_connected:
                await client.disconnect()
        except:
            pass

# ========== توابع کمکی ==========
def extract_meow_time(text):
    match = re.search(r'(\d+)\s*[:：]\s*(\d+)', text)
    if match:
        return int(match.group(1)) * 60 + int(match.group(2))
    match = re.search(r'(\d+)\s*دقیقه\s*و\s*(\d+)\s*ثانیه', text)
    if match:
        return int(match.group(1)) * 60 + int(match.group(2))
    match = re.search(r'(\d+)\s*دقیقه', text)
    if match:
        return int(match.group(1)) * 60
    match = re.search(r'(\d+)\s*ثانیه', text)
    if match:
        return int(match.group(1))
    return None

async def click_harvest_button(message):
    if not message.reply_markup:
        return False
    keywords = ["برداشت", "میوپوینت", "میو پیوند", "برداشت میو"]
    for row in message.reply_markup.inline_keyboard:
        for btn in row:
            for kw in keywords:
                if kw in btn.text:
                    try:
                        await btn.click()
                        print(f"✅ کلیک روی دکمه '{btn.text}' انجام شد")
                        return True
                    except:
                        return False
    return False

# ========== ربات سلف‌بات برای هر شماره ==========
async def selfbot_worker(phone):
    while True:
        try:
            session_string, selected_groups, meow_enabled, fish_enabled, smuggle_enabled, is_active = get_user(phone)
            if not session_string or not selected_groups or not is_active:
                print(f"⏸️ شماره {phone} غیرفعال است یا سشن ندارد")
                await asyncio.sleep(30)
                continue

            try:
                chat_ids = [int(g) for g in selected_groups]
            except:
                chat_ids = []
            if not chat_ids:
                print(f"❌ شماره {phone} هیچ گروهی انتخاب نشده")
                await asyncio.sleep(30)
                continue

            client = Client(
                f"selfbot_{phone}",
                session_string=session_string,
                api_id=API_ID,
                api_hash=API_HASH,
                in_memory=True,
                no_updates=True
            )

            @client.on_message(filters.group & filters.user(BOT_USER_ID))
            async def token_bot_handler(c: Client, message: Message):
                if message.chat.id not in chat_ids:
                    return
                text = message.text or ""
                print(f"📩 [{phone}] پیام از ربات: {text[:50]}...")

                if meow_enabled and "میو پوینت" in text and "بعد از" in text:
                    wait_time = extract_meow_time(text)
                    if wait_time and wait_time > 0:
                        meow_timers[f"{phone}_{message.chat.id}"] = wait_time
                        print(f"⏱️ [{phone}] تایم میو: {wait_time} ثانیه")

                if fish_enabled and "پیشی" in text:
                    print(f"🐱 [{phone}] کلیک روی دکمه برداشت...")
                    await click_harvest_button(message)

            try:
                await client.start()
                print(f"✅ ربات برای {phone} روشن شد")

                valid_chats = []
                async for dialog in client.get_dialogs():
                    if dialog.chat.type in (ChatType.GROUP, ChatType.SUPERGROUP):
                        if str(dialog.chat.id) in [str(cid) for cid in chat_ids]:
                            valid_chats.append(dialog.chat.id)
                            print(f"✅ [{phone}] گروه {dialog.chat.id} پیدا شد")

                if not valid_chats:
                    print(f"❌ [{phone}] هیچ گروه معتبری پیدا نشد")
                    await client.stop()
                    await asyncio.sleep(30)
                    continue

                async def meow_loop():
                    while True:
                        is_active_now = get_user(phone)[5]
                        if not is_active_now or not meow_enabled:
                            await asyncio.sleep(5)
                            continue
                        for chat_id in valid_chats:
                            try:
                                await client.send_message(chat_id, "میو")
                                print(f"😺 [{phone}] میو به {chat_id} ارسال شد")
                                timer_key = f"{phone}_{chat_id}"
                                for _ in range(15):
                                    if timer_key in meow_timers:
                                        wait = meow_timers.pop(timer_key)
                                        print(f"⏱️ [{phone}] صبر {wait} ثانیه")
                                        while wait > 0:
                                            if not get_user(phone)[5]:
                                                break
                                            sleep_time = min(wait, 10)
                                            await asyncio.sleep(sleep_time)
                                            wait -= sleep_time
                                        break
                                    await asyncio.sleep(1)
                                else:
                                    print(f"⚠️ [{phone}] تایم میو پیدا نشد، ۵ دقیقه صبر...")
                                    await asyncio.sleep(300)
                            except Exception as e:
                                print(f"❌ [{phone}] خطا: {e}")
                                await asyncio.sleep(60)
                        await asyncio.sleep(5)

                async def fish_loop():
                    while True:
                        is_active_now = get_user(phone)[5]
                        if not is_active_now or not fish_enabled:
                            await asyncio.sleep(5)
                            continue
                        for chat_id in valid_chats:
                            try:
                                await client.send_message(chat_id, "پیشی")
                                print(f"🐱 [{phone}] پیشی به {chat_id} ارسال شد")
                                await asyncio.sleep(8)
                            except Exception as e:
                                print(f"❌ [{phone}] خطا: {e}")
                        await asyncio.sleep(600)  # ۱۰ دقیقه

                tasks = [
                    asyncio.create_task(meow_loop()),
                    asyncio.create_task(fish_loop())
                ]

                while True:
                    is_active_now = get_user(phone)[5]
                    if not is_active_now:
                        break
                    await asyncio.sleep(5)

                for t in tasks:
                    t.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                await client.stop()
                print(f"🛑 ربات برای {phone} متوقف شد")

            except Exception as e:
                print(f"❌ [{phone}] خطا: {e}")
                try:
                    await client.stop()
                except:
                    pass
                await asyncio.sleep(30)

        except Exception as e:
            print(f"❌ [{phone}] خطای بحرانی: {e}")
            await asyncio.sleep(30)

# ========== مدیریت ربات‌ها ==========
def start_all_bots():
    """شروع ربات برای همه شماره‌های فعال"""
    users = get_all_users()
    for user in users:
        if user["is_active"] and user["session_string"] and user["selected_groups"]:
            phone = user["phone"]
            if phone not in selfbot_tasks or selfbot_tasks[phone].done():
                print(f"🚀 شروع ربات برای {phone}")
                task = asyncio.run_coroutine_threadsafe(selfbot_worker(phone), ASYNC_LOOP)
                selfbot_tasks[phone] = task

def stop_all_bots():
    """متوقف کردن همه ربات‌ها"""
    for phone in list(selfbot_tasks.keys()):
        if not selfbot_tasks[phone].done():
            # غیرفعال کردن در دیتابیس
            save_user(phone, "", [], True, True, True, False)
    selfbot_tasks.clear()
    print("🛑 همه ربات‌ها متوقف شدند")

def stop_bot(phone):
    """متوقف کردن یک ربات خاص"""
    if phone in selfbot_tasks and not selfbot_tasks[phone].done():
        save_user(phone, "", [], True, True, True, False)
        selfbot_tasks.pop(phone, None)
        print(f"🛑 ربات {phone} متوقف شد")

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
        session['phone'] = phone
        session['is_new_user'] = True
        return render_template('code.html')

@app.route('/verify_code', methods=['POST'])
def verify_code():
    code = request.form.get('code', '').strip()
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
    elif isinstance(result, str) and result.startswith("error"):
        return f"خطا: {result}", 500
    elif isinstance(result, str) and len(result) > 50:
        # ذخیره کاربر جدید با فعال بودن
        save_user(phone, result, [], True, True, True, True)
        session['authenticated'] = True
        # شروع ربات برای این شماره
        start_all_bots()
        return redirect(url_for('dashboard'))
    else:
        return f"خطای ناشناخته: {result}", 500

@app.route('/verify_password', methods=['POST'])
def verify_password():
    password = request.form.get('password', '').strip()
    phone = session.get('phone')
    if not phone or not password:
        return redirect(url_for('index'))
    result = run_async(check_password_async(phone, password))
    if isinstance(result, str) and result.startswith("error"):
        return f"خطا: {result}", 500
    elif isinstance(result, str) and len(result) > 50:
        save_user(phone, result, [], True, True, True, True)
        session['authenticated'] = True
        start_all_bots()
        return redirect(url_for('dashboard'))
    else:
        return f"خطای ناشناخته: {result}", 500

@app.route('/dashboard')
def dashboard():
    phone = session.get('phone')
    if not phone or not session.get('authenticated'):
        return redirect(url_for('index'))
    
    # اطلاعات کاربر فعلی
    session_string, selected, meow_enabled, fish_enabled, smuggle_enabled, is_active = get_user(phone)
    if not session_string:
        session.clear()
        return redirect(url_for('index'))
    
    groups = run_async(get_groups_async(session_string))
    if isinstance(groups, str) and groups.startswith("error"):
        return f"خطا در دریافت گروه‌ها: {groups}", 500
    
    # لیست همه کاربران
    all_users = get_all_users()
    
    return render_template('dashboard.html', 
        groups=groups, 
        selected=selected,
        meow_enabled=meow_enabled,
        fish_enabled=fish_enabled,
        smuggle_enabled=smuggle_enabled,
        is_active=is_active,
        phone=phone,
        all_users=all_users
    )

@app.route('/save_settings', methods=['POST'])
def save_settings():
    phone = session.get('phone')
    if not phone or not session.get('authenticated'):
        return redirect(url_for('index'))
    
    session_string, selected, _, _, _, _ = get_user(phone)
    if not session_string:
        return redirect(url_for('index'))
    
    meow_enabled = request.form.get('meow_enabled') == 'on'
    fish_enabled = request.form.get('fish_enabled') == 'on'
    smuggle_enabled = request.form.get('smuggle_enabled') == 'on'
    is_active = request.form.get('is_active') == 'on'
    selected_groups = request.form.getlist('groups')
    
    save_user(phone, session_string, selected_groups, meow_enabled, fish_enabled, smuggle_enabled, is_active)
    
    # ری‌استارت ربات‌ها
    stop_all_bots()
    start_all_bots()
    
    return redirect(url_for('dashboard'))

@app.route('/toggle_user', methods=['POST'])
def toggle_user():
    target_phone = request.form.get('phone')
    action = request.form.get('action')  # 'enable' or 'disable'
    admin_phone = session.get('phone')
    
    if not admin_phone or not session.get('authenticated'):
        return redirect(url_for('index'))
    
    session_string, selected, meow_enabled, fish_enabled, smuggle_enabled, is_active = get_user(target_phone)
    if session_string:
        new_status = action == 'enable'
        save_user(target_phone, session_string, selected, meow_enabled, fish_enabled, smuggle_enabled, new_status)
        if new_status:
            start_all_bots()
        else:
            stop_bot(target_phone)
    
    return redirect(url_for('dashboard'))

@app.route('/remove_user', methods=['POST'])
def remove_user():
    target_phone = request.form.get('phone')
    admin_phone = session.get('phone')
    
    if not admin_phone or not session.get('authenticated'):
        return redirect(url_for('index'))
    
    if target_phone == admin_phone:
        return "نمی‌توانید خودتان را حذف کنید!", 400
    
    stop_bot(target_phone)
    delete_user(target_phone)
    
    return redirect(url_for('dashboard'))

@app.route('/stop_bot')
def stop_bot_route():
    phone = session.get('phone')
    if phone:
        stop_bot(phone)
    return "ربات متوقف شد! <a href='/dashboard'>بازگشت</a>"

@app.route('/logout')
def logout():
    phone = session.get('phone')
    if phone:
        stop_bot(phone)
    session.clear()
    return redirect(url_for('index'))

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)
