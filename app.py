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
    print(f"💾 اطلاعات کاربر {phone} ذخیره شد")

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
selfbot_running = False
selfbot_lock = threading.Lock()
meow_timers = {}  # {chat_id: seconds_remaining} برای ذخیره تایم میو

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

# ========== تابع استخراج زمان از متن میو ==========
def extract_meow_time(text):
    """
    استخراج زمان از پاسخ ربات توکنی برای دستور میو
    مثال‌ها:
    - "بعد از 4:25 میتونی دوباره میو میو کنی"
    - "⏳ بعد از ۴:۲۵ میتونی دوباره میو میو کنی"
    - "بعد از 5 دقیقه"
    - "بعد از 30 ثانیه"
    برمی‌گردونه: تعداد ثانیه‌ها (int) یا None
    """
    # الگوی دقیقه:ثانیه (با اعداد فارسی یا انگلیسی)
    match = re.search(r'(\d+)\s*[:：]\s*(\d+)', text)
    if match:
        minutes = int(match.group(1))
        seconds = int(match.group(2))
        return minutes * 60 + seconds

    # الگوی "X دقیقه و Y ثانیه"
    match = re.search(r'(\d+)\s*دقیقه\s*و\s*(\d+)\s*ثانیه', text)
    if match:
        minutes = int(match.group(1))
        seconds = int(match.group(2))
        return minutes * 60 + seconds

    # الگوی "X دقیقه"
    match = re.search(r'(\d+)\s*دقیقه', text)
    if match:
        return int(match.group(1)) * 60

    # الگوی "X ثانیه"
    match = re.search(r'(\d+)\s*ثانیه', text)
    if match:
        return int(match.group(1))

    return None

# ========== تابع کلیک روی اولین دکمه ==========
async def click_first_button(message):
    """کلیک روی اولین دکمه از بالا (برای برداشت میوپوینت در پیشی)"""
    if not message.reply_markup:
        return False
    try:
        # اولین دکمه از اولین ردیف
        btn = message.reply_markup.inline_keyboard[0][0]
        await btn.click()
        return True
    except Exception as e:
        print(f"⚠️ خطا در کلیک روی اولین دکمه: {e}")
        return False

# ========== ربات سلف‌بات ==========
async def selfbot_worker(phone):
    global selfbot_running, meow_timers

    while True:
        try:
            session_string, selected_groups = get_user(phone)
            if not session_string or not selected_groups:
                print("❌ سشن یا گروه‌ها پیدا نشد، ۱۰ ثانیه بعد دوباره تلاش می‌کنم...")
                await asyncio.sleep(10)
                continue

            try:
                chat_ids = [int(g) for g in selected_groups]
            except:
                chat_ids = []
            if not chat_ids:
                print("❌ هیچ گروهی انتخاب نشده، ۱۰ ثانیه بعد دوباره تلاش می‌کنم...")
                await asyncio.sleep(10)
                continue

            client = Client(
                "selfbot",
                session_string=session_string,
                api_id=API_ID,
                api_hash=API_HASH,
                in_memory=True,
                no_updates=True
            )

            # ========== هندلر پیام‌های ربات توکنی ==========
            @client.on_message(filters.group & filters.regex(r'(میو پوینت|پیشی|میو پیوند)'))
            async def token_bot_handler(c: Client, message: Message):
                if message.chat.id not in chat_ids:
                    return
                if not message.from_user or not message.from_user.is_bot:
                    return

                text = message.text

                # ===== پردازش میو: استخراج تایم =====
                if "میو پوینت" in text and "بعد از" in text:
                    wait_time = extract_meow_time(text)
                    if wait_time and wait_time > 0:
                        # ذخیره تایم برای گروه مربوطه
                        meow_timers[message.chat.id] = wait_time
                        print(f"⏱️ تایم میو برای گروه {message.chat.id}: {wait_time} ثانیه")
                    else:
                        print(f"⚠️ تایم میو پیدا نشد در: {text[:50]}...")

                # ===== پردازش پیشی: کلیک روی اولین دکمه =====
                if "پیشی" in text:
                    # کلیک روی اولین دکمه (برداشت)
                    clicked = await click_first_button(message)
                    if clicked:
                        print(f"✅ کلیک روی اولین دکمه برای پیشی انجام شد")
                    else:
                        print(f"⚠️ هیچ دکمه‌ای برای پیشی پیدا نشد")

            try:
                await client.start()
                print(f"✅ ربات سلف‌بات برای {phone} روشن شد")

                valid_chats = []
                async for dialog in client.get_dialogs():
                    if dialog.chat.type in (ChatType.GROUP, ChatType.SUPERGROUP):
                        if str(dialog.chat.id) in [str(cid) for cid in chat_ids]:
                            valid_chats.append(dialog.chat.id)
                            print(f"✅ گروه {dialog.chat.id} ({dialog.chat.title}) پیدا شد")

                if not valid_chats:
                    print("❌ هیچ گروه معتبری پیدا نشد، ۳۰ ثانیه بعد دوباره تلاش می‌کنم...")
                    await client.stop()
                    await asyncio.sleep(30)
                    continue

                save_user(phone, session_string, [str(cid) for cid in valid_chats])

                # ========== وظیفه ۱: حلقه میو (با تایم متغیر) ==========
                async def meow_loop():
                    while True:
                        if not selfbot_running:
                            await asyncio.sleep(5)
                            continue

                        for chat_id in valid_chats:
                            if not selfbot_running:
                                break
                            try:
                                await client.send_message(chat_id, "میو")
                                print(f"😺 میو به گروه {chat_id} ارسال شد")
                                
                                # منتظر جواب ربات (حداکثر ۱۰ ثانیه)
                                for _ in range(10):
                                    if chat_id in meow_timers:
                                        wait = meow_timers.pop(chat_id)
                                        print(f"⏱️ تایم میو برای {chat_id}: {wait} ثانیه")
                                        # صبر به اندازه تایم
                                        for _ in range(wait // 10):
                                            if not selfbot_running:
                                                break
                                            await asyncio.sleep(10)
                                        if wait % 10 != 0:
                                            await asyncio.sleep(wait % 10)
                                        break
                                    await asyncio.sleep(1)
                                else:
                                    # اگر تایم نیومد، ۵ دقیقه پیش‌فرض
                                    print(f"⚠️ تایم میو برای {chat_id} پیدا نشد، ۵ دقیقه صبر می‌کنم...")
                                    await asyncio.sleep(300)

                            except Exception as e:
                                print(f"❌ خطا در ارسال میو به {chat_id}: {e}")
                                await asyncio.sleep(60)

                # ========== وظیفه ۲: حلقه پیشی (هر ۱۰ دقیقه) ==========
                async def fish_loop():
                    while True:
                        if not selfbot_running:
                            await asyncio.sleep(5)
                            continue

                        for chat_id in valid_chats:
                            if not selfbot_running:
                                break
                            try:
                                await client.send_message(chat_id, "پیشی")
                                print(f"🐱 پیشی به گروه {chat_id} ارسال شد")
                                await asyncio.sleep(5)  # صبر برای جواب ربات و کلیک
                            except Exception as e:
                                print(f"❌ خطا در ارسال پیشی به {chat_id}: {e}")

                        # هر ۱۰ دقیقه یکبار
                        for _ in range(600 // 10):
                            if not selfbot_running:
                                break
                            await asyncio.sleep(10)

                # ========== شروع وظایف ==========
                tasks = [
                    asyncio.create_task(meow_loop()),
                    asyncio.create_task(fish_loop())
                ]

                # منتظر بمان تا ربات متوقف شود
                while selfbot_running:
                    await asyncio.sleep(5)

                # لغو تسک‌ها
                for t in tasks:
                    t.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)

                # توقف کلاینت
                await client.stop()
                print("🛑 ربات سلف‌بات متوقف شد")

            except Exception as e:
                print(f"❌ خطا در سلف‌بات: {e}")
                try:
                    await client.stop()
                except:
                    pass
                print("🔄 ری‌استارت ربات در ۳۰ ثانیه...")
                await asyncio.sleep(30)
                continue

        except Exception as e:
            print(f"❌ خطای بحرانی در حلقه اصلی: {e}")
            await asyncio.sleep(30)
            continue

# ========== شروع و توقف ربات ==========
def start_selfbot(phone):
    global selfbot_running
    with selfbot_lock:
        if selfbot_running:
            print("⚠️ ربات از قبل روشن است")
            return
        selfbot_running = True

    asyncio.run_coroutine_threadsafe(selfbot_worker(phone), ASYNC_LOOP)
    print("🚀 ربات سلف‌بات شروع شد")

def stop_selfbot():
    global selfbot_running
    with selfbot_lock:
        selfbot_running = False
    print("🛑 توقف ربات درخواست شد")

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
        return "کد تایید منقضی شده است. از اول شماره رو وارد کن.", 400
    elif isinstance(result, str) and result.startswith("error"):
        return f"خطا: {result}", 500
    elif isinstance(result, str) and len(result) > 50:
        save_user(phone, result)
        session['authenticated'] = True
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
        save_user(phone, result)
        session['authenticated'] = True
        return redirect(url_for('dashboard'))
    else:
        return f"خطای ناشناخته: {result}", 500

@app.route('/dashboard')
def dashboard():
    phone = session.get('phone')
    if not phone or not session.get('authenticated'):
        return redirect(url_for('index'))

    session_string, selected = get_user(phone)
    if not session_string:
        session.clear()
        return redirect(url_for('index'))

    groups = run_async(get_groups_async(session_string))
    if isinstance(groups, str) and groups.startswith("error"):
        return f"خطا در دریافت گروه‌ها: {groups}", 500

    return render_template('dashboard.html', groups=groups, selected=selected)

@app.route('/save_groups', methods=['POST'])
def save_groups():
    phone = session.get('phone')
    if not phone or not session.get('authenticated'):
        return redirect(url_for('index'))

    session_string, _ = get_user(phone)
    if not session_string:
        return redirect(url_for('index'))

    selected = request.form.getlist('groups')
    save_user(phone, session_string, selected)

    start_selfbot(phone)

    return redirect(url_for('dashboard'))

@app.route('/stop_bot')
def stop_bot():
    stop_selfbot()
    return "ربات متوقف شد! <a href='/dashboard'>بازگشت</a>"

@app.route('/logout')
def logout():
    stop_selfbot()
    phone = session.get('phone')
    if phone and phone in active_clients:
        try:
            future = asyncio.run_coroutine_threadsafe(
                active_clients[phone]["client"].disconnect(),
                ASYNC_LOOP
            )
            future.result(timeout=5)
        except:
            pass
        active_clients.pop(phone, None)
    session.clear()
    return redirect(url_for('index'))

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)
