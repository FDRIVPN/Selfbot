import os
import json
import sqlite3
import asyncio
import threading
import time
from concurrent.futures import TimeoutError as FutureTimeoutError
from flask import Flask, render_template, request, redirect, url_for, session, flash
from pyrogram import Client, filters
from pyrogram.types import Message
from pyrogram.enums import ChatType
from pyrogram.errors import (
    PhoneNumberInvalid, PhoneCodeInvalid, PhoneCodeExpired, SessionPasswordNeeded
)

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "change-this-in-production-12345")

API_ID = int(os.getenv("API_ID", 0))
API_HASH = os.getenv("API_HASH", "")
PANEL_PASSWORD = os.getenv("PANEL_PASSWORD", "admin123")

if not API_ID or not API_HASH:
    raise ValueError("API_ID and API_HASH must be set")

BOT_USER_ID = 8299996037

DB_DIR = "/app/data" if os.getenv("RAILWAY_ENV") else "data"
DB_PATH = os.path.join(DB_DIR, "users.db")
os.makedirs(DB_DIR, exist_ok=True)

# ---------- دیتابیس ----------
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
            is_active INTEGER DEFAULT 0,
            cached_groups TEXT,
            cached_groups_time REAL
        )
    ''')
    for col, typ in [("meow_enabled", "INTEGER DEFAULT 1"),
                     ("fish_enabled", "INTEGER DEFAULT 1"),
                     ("is_active", "INTEGER DEFAULT 0"),
                     ("cached_groups", "TEXT"),
                     ("cached_groups_time", "REAL")]:
        try:
            c.execute(f"ALTER TABLE users ADD COLUMN {col} {typ}")
        except sqlite3.OperationalError:
            pass
    conn.commit()
    conn.close()

def save_user(phone, session_string, selected_groups=None,
              meow_enabled=True, fish_enabled=True, is_active=False, cached_groups=None):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''INSERT OR REPLACE INTO users
                 (phone, session_string, selected_groups, meow_enabled, fish_enabled,
                  is_active, cached_groups, cached_groups_time)
                 VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
              (phone, session_string,
               json.dumps(selected_groups) if selected_groups else None,
               1 if meow_enabled else 0,
               1 if fish_enabled else 0,
               1 if is_active else 0,
               json.dumps(cached_groups) if cached_groups else None,
               time.time() if cached_groups else None))
    conn.commit()
    conn.close()

def get_user(phone):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''SELECT session_string, selected_groups, meow_enabled,
                 fish_enabled, is_active,
                 cached_groups, cached_groups_time FROM users WHERE phone=?''', (phone,))
    row = c.fetchone()
    conn.close()
    if row:
        return {
            "session_string": row[0],
            "selected_groups": json.loads(row[1]) if row[1] else [],
            "meow_enabled": bool(row[2]),
            "fish_enabled": bool(row[3]),
            "is_active": bool(row[4]),
            "cached_groups": json.loads(row[5]) if row[5] else None,
            "cached_groups_time": row[6]
        }
    return None

def get_all_users():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''SELECT phone, selected_groups, meow_enabled,
                 fish_enabled, is_active FROM users''')
    rows = c.fetchall()
    conn.close()
    return [{
        "phone": r[0],
        "groups_count": len(json.loads(r[1])) if r[1] else 0,
        "meow_enabled": bool(r[2]),
        "fish_enabled": bool(r[3]),
        "is_active": bool(r[4])
    } for r in rows]

def delete_user(phone):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('DELETE FROM users WHERE phone=?', (phone,))
    conn.commit()
    conn.close()

def user_count():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('SELECT COUNT(*) FROM users')
    cnt = c.fetchone()[0]
    conn.close()
    return cnt

init_db()

# ---------- Event Loop ----------
ASYNC_LOOP = asyncio.new_event_loop()
def _async_loop_worker():
    asyncio.set_event_loop(ASYNC_LOOP)
    ASYNC_LOOP.run_forever()
threading.Thread(target=_async_loop_worker, daemon=True).start()

def run_async(coro, timeout=120):
    future = asyncio.run_coroutine_threadsafe(coro, ASYNC_LOOP)
    try:
        return future.result(timeout=timeout)
    except FutureTimeoutError:
        future.cancel()
        return "error: operation timed out"
    except Exception as e:
        return f"error: {str(e)}"

pending_clients = {}
selfbot_tasks = {}
rescue_tasks = {}

# ---------- عملیات تلگرام ----------
async def send_code_async(phone):
    if phone in pending_clients:
        try: await pending_clients[phone]["client"].disconnect()
        except: pass
        pending_clients.pop(phone, None)
    client = Client("temp", api_id=API_ID, api_hash=API_HASH, in_memory=True)
    await client.connect()
    try:
        sent = await client.send_code(phone)
        pending_clients[phone] = {
            "client": client, "hash": sent.phone_code_hash, "phone": phone
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
        return "error: session expired"
    data = pending_clients[phone]
    client = data["client"]
    phone_code_hash = data["hash"]
    if not client.is_connected:
        try: await client.connect()
        except:
            pending_clients.pop(phone, None)
            return "error: session expired"
    try:
        await client.sign_in(phone_number=phone, phone_code_hash=phone_code_hash, phone_code=code)
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
        try: await client.connect()
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
    client = Client("tmp", session_string=session_string, api_id=API_ID, api_hash=API_HASH,
                    in_memory=True, no_updates=True)
    try:
        await client.start()
        groups = []
        async for d in client.get_dialogs():
            if d.chat.type in (ChatType.GROUP, ChatType.SUPERGROUP):
                groups.append({"id": str(d.chat.id), "title": d.chat.title or "بدون نام",
                               "members": d.chat.members_count or 0})
        return groups
    except Exception as e:
        return f"error: {str(e)}"
    finally:
        try:
            if client.is_initialized: await client.stop()
            elif client.is_connected: await client.disconnect()
        except: pass

# ---------- ابزار دکمه‌ها ----------
def create_chat_filter(chat_ids):
    async def func(flt, client, message):
        return message.chat.id in chat_ids
    return filters.create(func)

async def click_first_button(message):
    if not message.reply_markup:
        return False
    try:
        # اول سعی می‌کنه با متن دقیق کلیک کنه
        await message.click(text="برداشت میو پوینت ها")
        return True
    except:
        try:
            # اگر نشد، اولین دکمه رو می‌زنه
            await message.click(0)
            return True
        except Exception as e:
            print(f"❌ خطا در کلیک دکمه: {e}")
            return False

async def click_button_by_text(message, keywords):
    if not message.reply_markup:
        return False
    if isinstance(keywords, str):
        keywords = [keywords]
    
    for row in message.reply_markup.inline_keyboard:
        for btn in row:
            for kw in keywords:
                if kw in (btn.text or ""):
                    try:
                        await message.click(text=btn.text)
                        return True
                    except Exception as e:
                        print(f"❌ خطا در کلیک دکمه '{btn.text}': {e}")
                        return False
    return False

def log_buttons(message):
    if not message.reply_markup or not message.reply_markup.inline_keyboard:
        return
    print(f"🔘 دکمه‌های پیام از {message.chat.id}:")
    for ri, row in enumerate(message.reply_markup.inline_keyboard):
        for bi, btn in enumerate(row):
            print(f"   ردیف{ri+1} دکمه{bi+1}: {btn.text}  | cb: {btn.callback_data} | url: {btn.url}")

# ---------- نجات خودکار (اسپم کلیک) ----------
async def rescue_spam(client: Client, chat_id: int, msg_id: int):
    print(f"🚑 شروع نجات خودکار برای پیام {msg_id} در چت {chat_id}")
    attempts = 0
    while True:
        attempts += 1
        try:
            msg = await client.get_messages(chat_id, msg_id)
        except Exception as e:
            print(f"❌ خطا در گرفتن پیام نجات: {e}")
            break
        if not msg or not msg.reply_markup or not msg.reply_markup.inline_keyboard:
            print("✅ دکمه ناپدید شد، نجات تمام شد")
            break
        try:
            await msg.reply_markup.inline_keyboard[0][0].click()
            print(f"🖱️ کلیک {attempts}")
        except Exception as e:
            print(f"❌ خطا در کلیک نجات: {e}")
            break
        await asyncio.sleep(1.5)
        if attempts > 60:
            print("⏱️ تایم‌اوت نجات")
            break
    rescue_tasks.pop((chat_id, msg_id), None)

# ---------- سلف‌بات Worker ----------
async def selfbot_worker(phone):
    print(f"🔄 worker شروع برای {phone}")
    while True:
        user = get_user(phone)
        if not user or not user["is_active"] or not user["session_string"] or not user["selected_groups"]:
            await asyncio.sleep(30)
            continue
        try: chat_ids = [int(g) for g in user["selected_groups"]]
        except: chat_ids = []
        if not chat_ids:
            await asyncio.sleep(30)
            continue

        client = Client(f"selfbot_{phone}", session_string=user["session_string"],
                        api_id=API_ID, api_hash=API_HASH, in_memory=True)
        chat_filter = create_chat_filter(chat_ids)

        @client.on_message(chat_filter & filters.user(BOT_USER_ID))
        async def live_handler(c: Client, message: Message):
            u = get_user(phone)
            if not u or not u["is_active"]: return
            text = message.text or ""
            print(f"📩 [{phone}] {text[:80]}")
            log_buttons(message)

            # نجات پیشی خیابونی
            if u["fish_enabled"] and "نجات پیشی خیابونی" in text:
                key = (message.chat.id, message.id)
                if key not in rescue_tasks:
                    task = asyncio.create_task(rescue_spam(c, message.chat.id, message.id))
                    rescue_tasks[key] = task

            # پیشی (کلیک روی برداشت)
            if u["fish_enabled"] and "پیشی" in text:
                print(f"🎣 پیشی پیدا شد → کلیک روی برداشت...")
                success = await click_button_by_text(message, ["برداشت میو پوینت", "برداشت"])
                if not success:
                    await click_first_button(message)  # روش جایگزین

        async def meow_loop():
            while True:
                u = get_user(phone)
                if not u or not u["is_active"] or not u["meow_enabled"]:
                    await asyncio.sleep(10); continue
                for cid in chat_ids:
                    try:
                        await client.send_message(cid, "میو")
                        print(f"😺 [{phone}] میو فرستاده شد به {cid}")
                        await asyncio.sleep(3)
                    except Exception as e:
                        print(f"❌ میو {phone}: {e}")
                        await asyncio.sleep(5)
                for _ in range(300 // 5):
                    if not get_user(phone) or not get_user(phone)["is_active"]:
                        break
                    await asyncio.sleep(5)

        async def fish_loop():
            while True:
                u = get_user(phone)
                if not u or not u["is_active"] or not u["fish_enabled"]:
                    await asyncio.sleep(10); continue
                for cid in chat_ids:
                    try:
                        await client.send_message(cid, "پیشی")
                        print(f"🐱 [{phone}] پیشی فرستاده شد به {cid}")
                        await asyncio.sleep(8)
                    except Exception as e:
                        print(f"❌ پیشی {phone}: {e}")
                        await asyncio.sleep(5)
                for _ in range(600 // 5):
                    if not get_user(phone) or not get_user(phone)["is_active"]:
                        break
                    await asyncio.sleep(5)

        try:
            await client.start()
            print(f"✅ ربات {phone} آنلاین شد")

            meow_task = asyncio.create_task(meow_loop())
            fish_task = asyncio.create_task(fish_loop())

            while get_user(phone) and get_user(phone)["is_active"]:
                await asyncio.sleep(5)

        except asyncio.CancelledError:
            print(f"🛑 تسک {phone} کنسل شد")
        except Exception as e:
            print(f"❌ خطا در worker {phone}: {e}")
        finally:
            for t in [meow_task, fish_task]:
                t.cancel()
            await asyncio.gather(meow_task, fish_task, return_exceptions=True)
            try:
                if client.is_initialized: await client.stop()
                elif client.is_connected: await client.disconnect()
            except: pass
            print(f"🛑 ربات {phone} متوقف شد")
        await asyncio.sleep(30)

# ---------- مدیریت ربات‌ها ----------
def start_all_bots():
    print("🚀 راه‌اندازی ربات‌های فعال...")
    users = get_all_users()
    started = 0
    for u in users:
        if u["is_active"]:
            full = get_user(u["phone"])
            if full and full["session_string"] and full["selected_groups"]:
                if u["phone"] not in selfbot_tasks or selfbot_tasks[u["phone"]].done():
                    task = asyncio.run_coroutine_threadsafe(selfbot_worker(u["phone"]), ASYNC_LOOP)
                    selfbot_tasks[u["phone"]] = task
                    started += 1
    print(f"✅ {started} ربات اجرا شد")

def stop_bot(phone):
    if phone in selfbot_tasks and not selfbot_tasks[phone].done():
        selfbot_tasks[phone].cancel()
        del selfbot_tasks[phone]

# ---------- روت‌های Flask ----------
@app.route('/')
def index():
    return render_template('login.html')

@app.route('/send_code', methods=['POST'])
def send_code_route():
    phone = request.form.get('phone', '').strip()
    if not phone:
        flash("شماره موبایل الزامی است", "danger")
        return redirect(url_for('index'))

    result = run_async(send_code_async(phone))
    if isinstance(result, str) and result.startswith("error"):
        flash(f"خطا: {result}", "danger")
        return redirect(url_for('index'))
    elif result is None:
        flash("شماره موبایل نامعتبر است", "danger")
        return redirect(url_for('index'))
    else:
        session['phone'] = phone
        return render_template('code.html')

@app.route('/verify_code', methods=['POST'])
def verify_code():
    code = request.form.get('code', '').strip()
    phone = session.get('phone')
    if not phone:
        flash("نشست منقضی شد", "danger")
        return redirect(url_for('index'))

    result = run_async(sign_in_async(phone, code))
    if result == "need_password":
        return render_template('password.html')
    elif result == "invalid_code":
        flash("کد تایید نامعتبر است", "danger")
        return redirect(url_for('index'))
    elif result == "code_expired":
        cleanup_pending(phone)
        flash("کد تایید منقضی شده است", "danger")
        return redirect(url_for('index'))
    elif isinstance(result, str) and result.startswith("error"):
        flash(f"خطا: {result}", "danger")
        return redirect(url_for('index'))
    elif isinstance(result, str) and len(result) > 50:
        groups = run_async(get_groups_async(result))
        if isinstance(groups, str) and groups.startswith("error"):
            groups = []
        save_user(phone, result, cached_groups=groups, is_active=False)
        session.pop('phone', None)
        return render_template('success.html', phone=phone)
    else:
        flash("خطای ناشناخته", "danger")
        return redirect(url_for('index'))

@app.route('/verify_password', methods=['POST'])
def verify_password():
    password = request.form.get('password', '').strip()
    phone = session.get('phone')
    if not phone or not password:
        flash("اطلاعات ناقص", "danger")
        return redirect(url_for('index'))

    result = run_async(check_password_async(phone, password))
    if isinstance(result, str) and result.startswith("error"):
        flash(f"خطا: {result}", "danger")
        return redirect(url_for('index'))
    elif isinstance(result, str) and len(result) > 50:
        groups = run_async(get_groups_async(result))
        if isinstance(groups, str) and groups.startswith("error"):
            groups = []
        save_user(phone, result, cached_groups=groups, is_active=False)
        session.pop('phone', None)
        return render_template('success.html', phone=phone)
    else:
        flash("خطای ناشناخته", "danger")
        return redirect(url_for('index'))

def cleanup_pending(phone):
    if phone in pending_clients:
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(pending_clients[phone]["client"].disconnect())
            loop.close()
        except: pass
        pending_clients.pop(phone, None)

# ----- پنل ادمین -----
@app.route('/admin', methods=['GET', 'POST'])
def admin_login():
    if request.method == 'GET':
        if session.get('is_admin'):
            return redirect(url_for('dashboard'))
        return render_template('admin_login.html')
    pwd = request.form.get('password', '')
    if pwd == PANEL_PASSWORD:
        session['is_admin'] = True
        return redirect(url_for('dashboard'))
    flash("رمز عبور اشتباه است", "danger")
    return redirect(url_for('admin'))

@app.route('/dashboard')
def dashboard():
    if not session.get('is_admin'):
        return redirect(url_for('admin'))

    managed = session.get('managed_phone')
    if not managed or not get_user(managed):
        managed = session.get('phone')
        session['managed_phone'] = managed

    user = get_user(managed) if managed else None
    groups = []
    if user:
        if user.get("cached_groups"):
            groups = user["cached_groups"]
            if (time.time() - user.get("cached_groups_time", 0)) > 3600:
                asyncio.run_coroutine_threadsafe(update_cached_groups(managed), ASYNC_LOOP)
        else:
            fresh = run_async(get_groups_async(user["session_string"]), timeout=10)
            if isinstance(fresh, list):
                groups = fresh
                save_user(managed, user["session_string"],
                          selected_groups=user["selected_groups"],
                          meow_enabled=user["meow_enabled"],
                          fish_enabled=user["fish_enabled"],
                          is_active=user["is_active"],
                          cached_groups=fresh)

    all_users = get_all_users()
    return render_template('dashboard.html',
                           managed_phone=managed,
                           groups=groups,
                           selected=user["selected_groups"] if user else [],
                           meow_enabled=user["meow_enabled"] if user else False,
                           fish_enabled=user["fish_enabled"] if user else False,
                           is_active=user["is_active"] if user else False,
                           all_users=all_users)

async def update_cached_groups(phone):
    user = get_user(phone)
    if not user or not user["session_string"]:
        return
    groups = await get_groups_async(user["session_string"])
    if isinstance(groups, list):
        save_user(phone, user["session_string"],
                  selected_groups=user["selected_groups"],
                  meow_enabled=user["meow_enabled"],
                  fish_enabled=user["fish_enabled"],
                  is_active=user["is_active"],
                  cached_groups=groups)

@app.route('/switch_user/<phone>')
def switch_user(phone):
    if not session.get('is_admin'):
        return redirect(url_for('admin'))
    if get_user(phone):
        session['managed_phone'] = phone
    return redirect(url_for('dashboard'))

@app.route('/save_settings', methods=['POST'])
def save_settings():
    if not session.get('is_admin'):
        return redirect(url_for('admin'))
    managed = session.get('managed_phone')
    if not managed:
        return redirect(url_for('dashboard'))
    user = get_user(managed)
    if not user:
        return redirect(url_for('dashboard'))

    meow_enabled = request.form.get('meow_enabled') == 'on'
    fish_enabled = request.form.get('fish_enabled') == 'on'
    is_active = request.form.get('is_active') == 'on'
    selected_groups = request.form.getlist('groups')

    save_user(managed, user["session_string"], selected_groups,
              meow_enabled, fish_enabled, is_active,
              cached_groups=user.get("cached_groups"))

    if is_active:
        start_all_bots()
    else:
        stop_bot(managed)
    flash("تنظیمات ذخیره شد", "success")
    return redirect(url_for('dashboard'))

@app.route('/toggle_user', methods=['POST'])
def toggle_user():
    if not session.get('is_admin'):
        return redirect(url_for('admin'))
    target = request.form.get('phone')
    action = request.form.get('action')
    u = get_user(target)
    if not u:
        return redirect(url_for('dashboard'))
    new_status = action == 'enable'
    save_user(target, u["session_string"], u["selected_groups"],
              u["meow_enabled"], u["fish_enabled"], new_status,
              cached_groups=u.get("cached_groups"))
    if new_status:
        start_all_bots()
    else:
        stop_bot(target)
    return redirect(url_for('dashboard'))

@app.route('/remove_user', methods=['POST'])
def remove_user():
    if not session.get('is_admin'):
        return redirect(url_for('admin'))
    target = request.form.get('phone')
    stop_bot(target)
    delete_user(target)
    if session.get('managed_phone') == target:
        session['managed_phone'] = None
    flash("حساب حذف شد", "success")
    return redirect(url_for('dashboard'))

@app.route('/admin_logout')
def admin_logout():
    if session.get('is_admin'):
        stop_all_bots()
        session.clear()
        return redirect(url_for('admin'))
    return "دسترسی غیرمجاز", 403

@app.route('/logout')
def logout():
    flash("خروج از پنل فقط توسط ادمین امکان‌پذیر است.", "warning")
    return redirect(url_for('index'))

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)
