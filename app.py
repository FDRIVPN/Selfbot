import os
import json
import sqlite3
import asyncio
import threading
from concurrent.futures import TimeoutError as FutureTimeoutError
from flask import Flask, render_template, request, redirect, url_for, session, flash
from pyrogram import Client, filters
from pyrogram.types import Message
from pyrogram.enums import ChatType
from pyrogram.errors import (
    PhoneNumberInvalid,
    PhoneCodeInvalid,
    PhoneCodeExpired,
    SessionPasswordNeeded,
)

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "change-this-in-production-12345")

API_ID = int(os.getenv("API_ID", 0))
API_HASH = os.getenv("API_HASH", "")
if not API_ID or not API_HASH:
    raise ValueError("API_ID and API_HASH must be set")

BOT_USER_ID = 8299996037  # آیدی ربات بازی

DB_DIR = "/app/data" if os.getenv("RAILWAY_ENV") else "data"
DB_PATH = os.path.join(DB_DIR, "users.db")
os.makedirs(DB_DIR, exist_ok=True)

# ======================= دیتابیس =======================
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

def save_user(phone, session_string, selected_groups=None,
              meow_enabled=True, fish_enabled=True, smuggle_enabled=True, is_active=True):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''
        INSERT OR REPLACE INTO users
        (phone, session_string, selected_groups, meow_enabled, fish_enabled, smuggle_enabled, is_active)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    ''', (
        phone, session_string,
        json.dumps(selected_groups) if selected_groups else None,
        1 if meow_enabled else 0,
        1 if fish_enabled else 0,
        1 if smuggle_enabled else 0,
        1 if is_active else 0
    ))
    conn.commit()
    conn.close()

def get_user(phone):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''SELECT session_string, selected_groups, meow_enabled,
                 fish_enabled, smuggle_enabled, is_active
                 FROM users WHERE phone=?''', (phone,))
    row = c.fetchone()
    conn.close()
    if row:
        return {
            "session_string": row[0],
            "selected_groups": json.loads(row[1]) if row[1] else [],
            "meow_enabled": bool(row[2]),
            "fish_enabled": bool(row[3]),
            "smuggle_enabled": bool(row[4]),
            "is_active": bool(row[5])
        }
    return None

def get_all_users():
    """فقط اطلاعات غیرحساس را برمی‌گرداند (بدون session string)"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''SELECT phone, selected_groups, meow_enabled,
                 fish_enabled, smuggle_enabled, is_active FROM users''')
    rows = c.fetchall()
    conn.close()
    users = []
    for row in rows:
        groups = json.loads(row[1]) if row[1] else []
        users.append({
            "phone": row[0],
            "groups_count": len(groups),
            "meow_enabled": bool(row[2]),
            "fish_enabled": bool(row[3]),
            "smuggle_enabled": bool(row[4]),
            "is_active": bool(row[5])
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

# ======================= Event Loop همگانی =======================
ASYNC_LOOP = asyncio.new_event_loop()

def _async_loop_worker():
    asyncio.set_event_loop(ASYNC_LOOP)
    ASYNC_LOOP.run_forever()

threading.Thread(target=_async_loop_worker, name="pyrogram-event-loop", daemon=True).start()

def run_async(coro, timeout=120):
    future = asyncio.run_coroutine_threadsafe(coro, ASYNC_LOOP)
    try:
        return future.result(timeout=timeout)
    except FutureTimeoutError:
        future.cancel()
        return "error: operation timed out"
    except Exception as e:
        return f"error: {str(e)}"

pending_clients = {}       # موقت برای احراز هویت
selfbot_tasks = {}         # phone -> asyncio.Task

# ======================= توابع کمکی Pyrogram =======================
async def send_code_async(phone):
    if phone in pending_clients:
        try:
            await pending_clients[phone]["client"].disconnect()
        except:
            pass
        pending_clients.pop(phone, None)

    client = Client("temp", api_id=API_ID, api_hash=API_HASH, in_memory=True)
    await client.connect()
    try:
        sent = await client.send_code(phone)
        pending_clients[phone] = {
            "client": client,
            "hash": sent.phone_code_hash,
            "phone": phone
        }
        return sent.phone_code_hash
    except PhoneNumberInvalid:
        await client.disconnect()
        pending_clients.pop(phone, None)
        return None
    except Exception as e:
        await client.disconnect()
        pending_clients.pop(phone, None)
        return f"error: {str(e)}"

async def sign_in_async(phone, code):
    if phone not in pending_clients:
        return "error: session expired, please resend code"

    data = pending_clients[phone]
    client = data["client"]
    phone_code_hash = data["hash"]

    if not client.is_connected:
        try:
            await client.connect()
        except:
            pending_clients.pop(phone, None)
            return "error: session expired, please resend code"

    try:
        await client.sign_in(phone_number=phone,
                             phone_code_hash=phone_code_hash,
                             phone_code=code)
        session_string = await client.export_session_string()
        await client.disconnect()
        pending_clients.pop(phone, None)
        return session_string
    except SessionPasswordNeeded:
        return "need_password"
    except PhoneCodeInvalid:
        await client.disconnect()
        pending_clients.pop(phone, None)
        return "invalid_code"
    except PhoneCodeExpired:
        await client.disconnect()
        pending_clients.pop(phone, None)
        return "code_expired"
    except Exception as e:
        await client.disconnect()
        pending_clients.pop(phone, None)
        return f"error: {str(e)}"

async def check_password_async(phone, password):
    if phone not in pending_clients:
        return "error: session expired"

    client = pending_clients[phone]["client"]
    if not client.is_connected:
        try:
            await client.connect()
        except:
            pending_clients.pop(phone, None)
            return "error: session expired"

    try:
        await client.check_password(password)
        session_string = await client.export_session_string()
        await client.disconnect()
        pending_clients.pop(phone, None)
        return session_string
    except Exception as e:
        await client.disconnect()
        pending_clients.pop(phone, None)
        return f"error: {str(e)}"

async def get_groups_async(session_string):
    client = Client("temp_groups", session_string=session_string,
                    api_id=API_ID, api_hash=API_HASH, in_memory=True, no_updates=True)
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

# ======================= فیلتر و کلیک روی دکمه‌ها =======================
def create_chat_filter(chat_ids):
    async def func(flt, client, message):
        return message.chat.id in chat_ids
    return filters.create(func)

async def click_first_button(message):
    if not message.reply_markup:
        return False
    try:
        first_button = message.reply_markup.inline_keyboard[0][0]
        await first_button.click()
        return True
    except:
        return False

async def click_button_by_text(message, keywords):
    if not message.reply_markup:
        return False
    if isinstance(keywords, str):
        keywords = [keywords]
    for row in message.reply_markup.inline_keyboard:
        for btn in row:
            for kw in keywords:
                if kw in btn.text:
                    try:
                        await btn.click()
                        return True
                    except:
                        return False
    return False

# ======================= سلف‌بات Worker =======================
async def selfbot_worker(phone):
    print(f"🔄 worker شروع برای {phone}")
    while True:
        user = get_user(phone)
        if not user or not user["is_active"] or not user["session_string"] or not user["selected_groups"]:
            await asyncio.sleep(30)
            continue

        try:
            chat_ids = [int(g) for g in user["selected_groups"]]
        except:
            chat_ids = []
        if not chat_ids:
            await asyncio.sleep(30)
            continue

        client = Client(
            f"selfbot_{phone}",
            session_string=user["session_string"],
            api_id=API_ID,
            api_hash=API_HASH,
            in_memory=True
        )

        chat_filter = create_chat_filter(chat_ids)

        @client.on_message(chat_filter & filters.user(BOT_USER_ID))
        async def live_handler(c: Client, message: Message):
            # خواندن تنظیمات لحظه‌ای (ممکن است در حین اجرا تغییر کند)
            u = get_user(phone)
            if not u or not u["is_active"]:
                return
            text = message.text or ""
            print(f"📩 [{phone}] {text[:80]}")

            if u["meow_enabled"] and "میو پوینت" in text:
                await click_first_button(message)
            if u["fish_enabled"] and "پیشی" in text:
                await click_first_button(message)
            if u["smuggle_enabled"] and "قاچاق" in text:
                if "شروع قاچاق میویی" in text:
                    await click_button_by_text(message, ["شروع قاچاق", "شروع"])
                elif "دریافت دستمزد" in text:
                    await click_button_by_text(message, ["دریافت دستمزد", "تایید"])

        async def meow_loop():
            while True:
                u = get_user(phone)
                if not u or not u["is_active"] or not u["meow_enabled"]:
                    await asyncio.sleep(10)
                    continue
                for cid in chat_ids:
                    try:
                        await client.send_message(cid, "میو")
                        await asyncio.sleep(3)
                    except Exception as e:
                        print(f"❌ میو {phone}: {e}")
                # انتظار ۵ دقیقه با قابلیت توقف سریع
                for _ in range(300 // 5):
                    if not get_user(phone) or not get_user(phone)["is_active"]:
                        break
                    await asyncio.sleep(5)

        async def fish_loop():
            while True:
                u = get_user(phone)
                if not u or not u["is_active"] or not u["fish_enabled"]:
                    await asyncio.sleep(10)
                    continue
                for cid in chat_ids:
                    try:
                        await client.send_message(cid, "پیشی")
                        await asyncio.sleep(8)
                    except Exception as e:
                        print(f"❌ پیشی {phone}: {e}")
                for _ in range(600 // 5):
                    if not get_user(phone) or not get_user(phone)["is_active"]:
                        break
                    await asyncio.sleep(5)

        async def smuggle_loop():
            while True:
                u = get_user(phone)
                if not u or not u["is_active"] or not u["smuggle_enabled"]:
                    await asyncio.sleep(10)
                    continue

                # فاز اول: شروع قاچاق برای همه گروه‌ها
                for cid in chat_ids:
                    try:
                        await client.send_message(cid, "قاچاق میویی")
                        await asyncio.sleep(5)
                    except Exception as e:
                        print(f"❌ شروع قاچاق {phone}: {e}")

                # انتظار ۱ ساعت (قابل توقف در بازه‌های ۱۰ ثانیه)
                for _ in range(3600 // 10):
                    u = get_user(phone)
                    if not u or not u["is_active"]:
                        break
                    await asyncio.sleep(10)
                if not get_user(phone) or not get_user(phone)["is_active"]:
                    continue

                # فاز دوم: دریافت دستمزد برای همه گروه‌ها
                for cid in chat_ids:
                    try:
                        await client.send_message(cid, "قاچاق میویی دریافت دستمزد")
                        await asyncio.sleep(5)
                    except Exception as e:
                        print(f"❌ دستمزد قاچاق {phone}: {e}")

                # وقفه کوتاه پیش از شروع چرخه بعدی
                await asyncio.sleep(30)

        try:
            await client.start()
            print(f"✅ ربات {phone} آنلاین شد")

            # اطمینان از وجود گروه‌ها (اختیاری)
            valid_chats = []
            async for dialog in client.get_dialogs():
                if dialog.chat.id in chat_ids:
                    valid_chats.append(dialog.chat.id)

            # اجرای همزمان سه حلقه
            meow_task = asyncio.create_task(meow_loop())
            fish_task = asyncio.create_task(fish_loop())
            smuggle_task = asyncio.create_task(smuggle_loop())

            # منتظر بمانیم تا حساب غیرفعال شود یا تسک کنسل شود
            while get_user(phone) and get_user(phone)["is_active"]:
                await asyncio.sleep(5)

        except asyncio.CancelledError:
            print(f"🛑 تسک {phone} کنسل شد")
        except Exception as e:
            print(f"❌ خطا در worker {phone}: {e}")
        finally:
            # کنسل کردن حلقه‌ها
            for t in [meow_task, fish_task, smuggle_task]:
                t.cancel()
            await asyncio.gather(meow_task, fish_task, smuggle_task, return_exceptions=True)
            try:
                if client.is_initialized:
                    await client.stop()
                elif client.is_connected:
                    await client.disconnect()
            except:
                pass
            print(f"🛑 ربات {phone} متوقف شد")

        # قبل از تلاش مجدد، کمی صبر کن
        await asyncio.sleep(30)

# ======================= مدیریت ربات‌ها =======================
def start_all_bots():
    print("🚀 راه‌اندازی همه ربات‌های فعال...")
    users = get_all_users()
    started = 0
    for user in users:
        phone = user["phone"]
        # فقط اگر فعال باشد و session داشته باشد (از طریق get_user چک می‌کنیم)
        full = get_user(phone)
        if full and full["is_active"] and full["session_string"] and full["selected_groups"]:
            if phone not in selfbot_tasks or selfbot_tasks[phone].done():
                task = asyncio.run_coroutine_threadsafe(selfbot_worker(phone), ASYNC_LOOP)
                selfbot_tasks[phone] = task
                started += 1
    print(f"✅ {started} ربات اجرا شد")

def stop_all_bots():
    for phone, task in list(selfbot_tasks.items()):
        if not task.done():
            task.cancel()
    selfbot_tasks.clear()

def stop_bot(phone):
    if phone in selfbot_tasks and not selfbot_tasks[phone].done():
        selfbot_tasks[phone].cancel()
        del selfbot_tasks[phone]

# ======================= روت‌های Flask =======================
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
        cleanup_pending(phone)
        return "کد تایید منقضی شده است", 400
    elif isinstance(result, str) and result.startswith("error"):
        return f"خطا: {result}", 500
    elif isinstance(result, str) and len(result) > 50:
        # ورود موفق
        save_user(phone, result)
        session['authenticated'] = True
        session['managed_phone'] = phone   # ← شماره‌ای که الان مدیریت می‌شود
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
        save_user(phone, result)
        session['authenticated'] = True
        session['managed_phone'] = phone
        start_all_bots()
        return redirect(url_for('dashboard'))
    else:
        return f"خطای ناشناخته: {result}", 500

def cleanup_pending(phone):
    if phone in pending_clients:
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(pending_clients[phone]["client"].disconnect())
            loop.close()
        except:
            pass
        pending_clients.pop(phone, None)

@app.route('/dashboard')
def dashboard():
    if not session.get('authenticated'):
        return redirect(url_for('index'))

    # شماره‌ای که در حال مدیریت است (پیش‌فرض شماره ورود)
    managed = session.get('managed_phone')
    if not managed or not get_user(managed):
        # در صورت نبود، به اولین شماره‌ای که session دارد برگرد
        managed = session.get('phone')
        session['managed_phone'] = managed

    user = get_user(managed)
    if not user:
        session.clear()
        return redirect(url_for('index'))

    # دریافت گروه‌های این شماره
    groups = run_async(get_groups_async(user["session_string"]))
    if isinstance(groups, str) and groups.startswith("error"):
        flash(f"خطا در دریافت گروه‌ها: {groups}", "danger")
        groups = []

    all_users = get_all_users()  # بدون session string

    return render_template('dashboard.html',
                           managed_phone=managed,
                           groups=groups,
                           selected=user["selected_groups"],
                           meow_enabled=user["meow_enabled"],
                           fish_enabled=user["fish_enabled"],
                           smuggle_enabled=user["smuggle_enabled"],
                           is_active=user["is_active"],
                           all_users=all_users)

@app.route('/switch_user/<phone>')
def switch_user(phone):
    if not session.get('authenticated'):
        return redirect(url_for('index'))
    if get_user(phone):
        session['managed_phone'] = phone
    return redirect(url_for('dashboard'))

@app.route('/save_settings', methods=['POST'])
def save_settings():
    managed = session.get('managed_phone')
    if not managed or not session.get('authenticated'):
        return redirect(url_for('index'))

    user = get_user(managed)
    if not user:
        return redirect(url_for('index'))

    meow_enabled = request.form.get('meow_enabled') == 'on'
    fish_enabled = request.form.get('fish_enabled') == 'on'
    smuggle_enabled = request.form.get('smuggle_enabled') == 'on'
    is_active = request.form.get('is_active') == 'on'
    selected_groups = request.form.getlist('groups')

    save_user(managed, user["session_string"], selected_groups,
              meow_enabled, fish_enabled, smuggle_enabled, is_active)

    # اگر فعال شد، ربات‌ها را دوباره راه بینداز
    if is_active:
        start_all_bots()
    else:
        stop_bot(managed)

    return redirect(url_for('dashboard'))

@app.route('/toggle_user', methods=['POST'])
def toggle_user():
    if not session.get('authenticated'):
        return redirect(url_for('index'))
    target_phone = request.form.get('phone')
    action = request.form.get('action')
    u = get_user(target_phone)
    if not u:
        return redirect(url_for('dashboard'))

    new_status = action == 'enable'
    save_user(target_phone, u["session_string"], u["selected_groups"],
              u["meow_enabled"], u["fish_enabled"], u["smuggle_enabled"], new_status)

    if new_status:
        start_all_bots()
    else:
        stop_bot(target_phone)

    return redirect(url_for('dashboard'))

@app.route('/remove_user', methods=['POST'])
def remove_user():
    if not session.get('authenticated'):
        return redirect(url_for('index'))
    target_phone = request.form.get('phone')
    if target_phone == session.get('phone'):
        flash("نمی‌توانید حساب جاری خود را حذف کنید.", "danger")
        return redirect(url_for('dashboard'))

    stop_bot(target_phone)
    delete_user(target_phone)
    # اگر حساب حذف‌شده همان managed بود، به شماره ورود برگرد
    if session.get('managed_phone') == target_phone:
        session['managed_phone'] = session.get('phone')
    return redirect(url_for('dashboard'))

@app.route('/logout')
def logout():
    phone = session.get('phone')
    if phone:
        stop_bot(phone)
    cleanup_pending(phone)
    session.clear()
    return redirect(url_for('index'))

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)
