import os
import re
import asyncio
from pyrogram import Client, filters

# ========== تنظیمات ==========
API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")
SESSION_STRING = os.getenv("SESSION_STRING")

# شناسه گروهی که قراره توش کار کنه
ALLOWED_CHATS = [-1003998125518]  

# اسم ربات توکنی که به «میو» پاسخ میده (بدون @)
TOKEN_BOT_USERNAME = "YourTokenBotUsername"  # این رو با اسم واقعی ربات توکنی عوض کن!

# دیکشنری برای ذخیره تسک‌های در حال اجرا (برای هر گروه)
pending_tasks = {}

# ========== ساخت کلاینت ==========
app = Client(
    name="userbot",
    session_string=SESSION_STRING,
    api_id=API_ID,
    api_hash=API_HASH
)

# ========== تابع استخراج زمان از متن ربات ==========
def extract_wait_time(text):
    """
    زمان رو از متن ربات توکنی استخراج میکنه و به ثانیه برمیگردونه
    مثال‌های پشتیبانی‌شده:
    - "بعد از ۴:۲۵ میتونی..."  (پشتیبانی از اعداد فارسی و انگلیسی)
    - "⏳ بعد از ۴:۲۵ میتونی دوباره میو میو کنی"
    - "۴ دقیقه و ۲۵ ثانیه"
    - "۵ دقیقه"
    - "۳۰ ثانیه"
    """

    # الگوی "دقیقه:ثانیه" (مثل ۴:۲۵)
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

    return None  # هیچ زمانی پیدا نشد

# ========== تابع ارسال «میو» بعد از تایم مشخص ==========
async def send_meow_after_delay(chat_id, delay_seconds):
    """بعد از delay_seconds، یک «میو» به گروه میفرسته"""
    try:
        await asyncio.sleep(delay_seconds)
        await app.send_message(chat_id, "میو")
        print(f"✅ میو جدید ارسال شد به گروه {chat_id}")
    except Exception as e:
        print(f"❌ خطا در ارسال میو: {e}")
    finally:
        # بعد از ارسال، تسک رو از لیست پاک کن
        pending_tasks.pop(chat_id, None)

# ========== هندلر پیام‌های ربات توکنی ==========
@app.on_message(filters.group & filters.regex(r'میو پوینت'))
async def handle_token_bot_reply(client, message):
    chat_id = message.chat.id

    # فقط گروه‌های مجاز
    if chat_id not in ALLOWED_CHATS:
        return

    # بررسی کن که فرستنده، ربات توکنی ما باشه
    if not message.from_user or not message.from_user.is_bot:
        return
    if message.from_user.username != TOKEN_BOT_USERNAME:
        return

    # بررسی کن که این پیام، ریپلای به پیام خودمون باشه
    if not message.reply_to_message:
        return

    # گرفتن ID خودمون (یوزربات)
    me = await client.get_me()
    if message.reply_to_message.from_user.id != me.id:
        return  # این ریپلای به ما نیست، پس بیخیال

    # استخراج زمان از متن پیام
    wait_time = extract_wait_time(message.text)
    if wait_time is None or wait_time <= 0:
        print(f"⚠️ زمانی در متن پیدا نشد: {message.text}")
        return

    print(f"⏳ زمان استخراج شد: {wait_time} ثانیه برای گروه {chat_id}")

    # اگر قبلاً یک تایمر برای این گروه تنظیم شده، اون رو لغو کن (جلوگیری از تداخل)
    if chat_id in pending_tasks:
        pending_tasks[chat_id].cancel()
        pending_tasks.pop(chat_id, None)

    # تنظیم تایمر جدید برای ارسال «میو»
    task = asyncio.create_task(send_meow_after_delay(chat_id, wait_time))
    pending_tasks[chat_id] = task

# ========== اجرا ==========
if __name__ == "__main__":
    print("🤖 ربات میو خودکار روشن شد...")
    print(f"📌 گروه فعال: {ALLOWED_CHATS}")
    print(f"📌 ربات توکنی: @{TOKEN_BOT_USERNAME}")
    app.run()
