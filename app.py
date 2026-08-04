import os
import json
import sqlite3
import asyncio
import threading
import re
from concurrent.futures import TimeoutError as FutureTimeoutError
from flask import Flask, render_template, request, redirect, url_for, session
from pyrogram import Client
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

# ========== دیکشنری برای نگهداری Clientهای فعال ==========
active_clients = {}
selfbot_running = False
selfbot_thread = None
selfbot_lock = threading.Lock()

# وضعیت قاچاق برای هر گروه
smuggle_status = {}  # {chat_id: "waiting" | "started" | "done" | "jail"}

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

# ========== تابع کلیک روی دکمه ==========
async def click_button(message, button_text):
    """کلیک روی دکمه با متن مشخص"""
    if not message or not message.reply_markup:
        return False
    for row in message.reply_markup.inline_keyboard:
        for btn in row:
            if btn.text == button_text:
                await btn.click()
                return True
    return False

# ========== ربات سلف‌بات با قابلیت میو، پیشی و قاچاق ==========
async def selfbot_worker(phone):
    global selfbot_running, smuggle_status

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
                # شمارنده‌ها برای زمان‌بندی
                meow_counter = 0
                fish_counter = 0
                smuggle_counter = 0
                last_meow_time = 0
                last_fish_time = 0
                last_smuggle_time = 0

                while True:
                    # اگر ربات باید متوقف بشه، صبر کن تا دوباره فعال بشه
                    while not selfbot_running:
                        print("⏸️ ربات در حالت توقف است، منتظر فعال شدن...")
                        await asyncio.sleep(5)
                        continue

                    now = asyncio.get_event_loop().time()

                    # ===== ۱. میو (هر ۵ دقیقه) =====
                    if now - last_meow_time >= 300 or last_meow_time == 0:
                        for chat_id in valid_chats:
                            if not selfbot_running:
                                break
                            try:
                                await client.send_message(chat_id, "میو")
                                print(f"✅ میو به گروه {chat_id} ارسال شد")
                                last_meow_time = now
                            except Exception as e:
                                print(f"❌ خطا در ارسال میو به {chat_id}: {e}")
                        await asyncio.sleep(3)

                    # ===== ۲. پیشی (هر ۷ دقیقه) =====
                    if now - last_fish_time >= 420 or last_fish_time == 0:
                        for chat_id in valid_chats:
                            if not selfbot_running:
                                break
                            try:
                                msg = await client.send_message(chat_id, "پیشی")
                                print(f"🐱 پیشی به گروه {chat_id} ارسال شد")
                                last_fish_time = now
                                # صبر برای دریافت جواب ربات و کلیک روی دکمه
                                await asyncio.sleep(5)
                            except Exception as e:
                                print(f"❌ خطا در ارسال پیشی به {chat_id}: {e}")
                        await asyncio.sleep(3)

                    # ===== ۳. قاچاق میویی (هر ۱ ساعت) =====
                    if now - last_smuggle_time >= 3600 or last_smuggle_time == 0:
                        for chat_id in valid_chats:
                            if not selfbot_running:
                                break
                            try:
                                # ارسال قاچاق میویی
                                msg = await client.send_message(chat_id, "قاچاق میویی")
                                print(f"📦 قاچاق میویی به گروه {chat_id} ارسال شد")
                                last_smuggle_time = now
                                # وضعیت قاچاق رو شروع می‌کنیم
                                smuggle_status[chat_id] = "waiting"
                                await asyncio.sleep(5)
                            except Exception as e:
                                print(f"❌ خطا در ارسال قاچاق به {chat_id}: {e}")
                        await asyncio.sleep(3)

                    # ===== ۴. چک کردن پیام‌های ربات توکنی =====
                    # این بخش توسط هندلر زیر انجام میشه

                    # ===== ۵. لاگ زنده بودن =====
                    print("⏳ ربات زنده است...")
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

# ========== هندلر پیام‌های ربات توکنی ==========
@app.on_message(filters.group & filters.text)
async def handle_token_bot_reply(client, message):
    chat_id = message.chat.id
    if not message.from_user or not message.from_user.is_bot:
        return

    # اگر پیام از ربات توکنی نیست، نادیده بگیر
    # اسم ربات توکنی رو می‌تونی عوض کنی
    if message.from_user.username != "MeowieeQBot":
        return

    text = message.text
    reply_to = message.reply_to_message

    # ===== ۱. پردازش میو =====
    if "میو پوینت" in text:
        # اگر ریپلای به خودمون بود و تایم داشت، می‌تونیم استخراج کنیم
        pass

    # ===== ۲. پردازش پیشی =====
    if "پیشی" in text and "برداشت میوپوینت" in text:
        clicked = await click_button(message, "برداشت میوپوینت")
        if clicked:
            print(f"✅ دکمه برداشت میوپوینت کلیک شد")
        else:
            print(f"⚠️ دکمه برداشت میوپوینت پیدا نشد")

    # ===== ۳. پردازش قاچاق میویی =====
    if "قاچاق" in text:
        # شروع قاچاق
        if "شروع قاچاق میویی" in text:
            clicked = await click_button(message, "شروع قاچاق میویی")
            if clicked:
                print(f"✅ دکمه شروع قاچاق کلیک شد")
                smuggle_status[chat_id] = "started"
                # بعد از ۱ ساعت دستمزد بگیریم (اینجا حلقه اصلی انجام میده)
            else:
                print(f"⚠️ دکمه شروع قاچاق پیدا نشد")

        # دریافت دستمزد
        elif "دریافت دستمزد" in text or "دستمزد" in text:
            # سعی کن روی دکمه تایید کلیک کنه
            clicked = await click_button(message, "تایید")
            if not clicked:
                clicked = await click_button(message, "دریافت دستمزد")
            if clicked:
                print(f"✅ دستمزد قاچاق دریافت شد")
                smuggle_status[chat_id] = "done"
            else:
                print(f"⚠️ دکمه دریافت دستمزد پیدا نشد")

        # زندان
        elif "زندان" in text or "رفتی زندان" in text:
            print(f"⚠️ کاربر به زندان رفت! گروه {chat_id}")
            smuggle_status[chat_id] = "jail"
            # می‌توانیم بعداً یه کاری بکنیم

        # استیکر قاچاق (هیچی)
        elif "قاچاق میویی" in text and "استیکر" in text:
            print(f"ℹ️ استیکر قاچاق دریافت شد - صبر کنید")

    # ===== ۴. پیام‌های دیگه =====
    if "پیشی" in text and "شمایی" in text and "زندان" in text:
        print(f"⚠️ کاربر به زندان رفت! گروه {chat_id}")
        smuggle_status[chat_id] = "jail"

def start_selfbot(phone):
    global selfbot_running, selfbot_thread
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

# ========== بقیه روت‌های Flask (همون قبلی) ==========
# ... (همه روت‌های لاگین، داشبورد و ... مثل قبل)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)
