import os
import json
import sqlite3
import asyncio
import threading
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

# ========== تابع کمکی برای کلیک روی دکمه ==========
async def click_button(message, button_text):
    if not message.reply_markup:
        return False
    for row in message.reply_markup.inline_keyboard:
        for btn in row:
            if btn.text == button_text:
                await btn.click()
                return True
    return False

# ========== ربات سلف‌بات با هندلر داخلی ==========
async def selfbot_worker(phone):
    global selfbot_running

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
            @client.on_message(filters.group & filters.regex(r'(میو پوینت|پیشی|قاچاق|دریافت دستمزد)'))
            async def token_bot_handler(c: Client, message: Message):
                # فقط گروه‌های مجاز
                if message.chat.id not in chat_ids:
                    return
                if not message.from_user or not message.from_user.is_bot:
                    return

                text = message.text

                # میو پوینت - فقط لاگ
                if "میو پوینت" in text:
                    print(f"📝 جواب میو دریافت شد: {text[:50]}...")

                # پیشی - کلیک روی دکمه برداشت میوپوینت
                if "پیشی" in text and "برداشت میوپوینت" in text:
                    clicked = await click_button(message, "برداشت میوپوینت")
                    if clicked:
                        print(f"✅ دکمه برداشت میوپوینت کلیک شد")
                    else:
                        print(f"⚠️ دکمه برداشت میوپوینت پیدا نشد")

                # قاچاق میویی - کلیک روی دکمه شروع قاچاق
                if "قاچاق" in text and "شروع قاچاق میویی" in text:
                    clicked = await click_button(message, "شروع قاچاق میویی")
                    if clicked:
                        print(f"✅ دکمه شروع قاچاق کلیک شد")
                    else:
                        print(f"⚠️ دکمه شروع قاچاق پیدا نشد")

                # دریافت دستمزد - کلیک روی دکمه تایید
                if "دریافت دستمزد" in text:
                    clicked = await click_button(message, "دریافت دستمزد")
                    if clicked:
                        print(f"✅ دکمه دریافت دستمزد کلیک شد")
                    else:
                        # دکمه با اسم دیگه
                        if message.reply_markup:
                            for row in message.reply_markup.inline_keyboard:
                                for btn in row:
                                    if "تایید" in btn.text or "دریافت" in btn.text:
                                        await btn.click()
                                        print(f"✅ دکمه {btn.text} کلیک شد")
                                        break

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

                # ========== حلقه اصلی ==========
                while True:
                    if not selfbot_running:
                        print("⏸️ ربات در حالت توقف است، منتظر فعال شدن...")
                        await asyncio.sleep(5)
                        continue

                    # ۱. ارسال میو
                    for chat_id in valid_chats:
                        if not selfbot_running:
                            break
                        try:
                            await client.send_message(chat_id, "میو")
                            print(f"✅ میو به گروه {chat_id} ارسال شد")
                            await asyncio.sleep(3)
                        except Exception as e:
                            print(f"❌ خطا در ارسال میو به {chat_id}: {e}")
                            if "Peer id invalid" in str(e) or "USER_NOT_PARTICIPANT" in str(e):
                                valid_chats.remove(chat_id)
                                save_user(phone, session_string, [str(cid) for cid in valid_chats])
                                print(f"⚠️ گروه {chat_id} از لیست حذف شد")

                    # ۲. ارسال پیشی (هر ۷ دقیقه)
                    for chat_id in valid_chats:
                        if not selfbot_running:
                            break
                        try:
                            await client.send_message(chat_id, "پیشی")
                            print(f"🐱 پیشی به گروه {chat_id} ارسال شد")
                            await asyncio.sleep(2)
                        except Exception as e:
                            print(f"❌ خطا در ارسال پیشی به {chat_id}: {e}")

                    # ۳. قاچاق میویی (هر ۱ ساعت)
                    for chat_id in valid_chats:
                        if not selfbot_running:
                            break
                        try:
                            await client.send_message(chat_id, "قاچاق میویی")
                            print(f"📦 قاچاق میویی به گروه {chat_id} ارسال شد")
                            await asyncio.sleep(2)

                            # منتظر ۱ ساعت
                            await asyncio.sleep(3600)

                            if not selfbot_running:
                                break

                            await client.send_message(chat_id, "قاچاق میویی دریافت دستمزد")
                            print(f"💰 دستمزد قاچاق به گروه {chat_id} درخواست شد")
                            await asyncio.sleep(2)

                        except Exception as e:
                            print(f"❌ خطا در قاچاق میویی به {chat_id}: {e}")

                    print("⏳ چرخه کامل شد، ۵ دقیقه صبر می‌کنم...")
                    for _ in range(10):
                        if not selfbot_running:
                            break
                        print(f"⏳ ربات زنده است... ({_+1}/10) - {int((_+1)*30)} ثانیه از ۳۰۰ ثانیه")
                        await asyncio.sleep(30)

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
