import os
import time
from pyrogram import Client, filters

# ==================================================
#  تنظیمات اولیه - این مقادیر از Railway می‌خوان
# ==================================================
API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")
SESSION_STRING = os.getenv("SESSION_STRING")

# لیست چت‌آیدی گروه‌هایی که ربات توشون کار کنه
ALLOWED_CHATS = [
    -1003998125518,  # گروه شما
    # -1001234567890,  # گروه دوم (در صورت نیاز)
]

# دیکشنری برای ذخیره زمان آخرین میو هر کاربر
# ساختار: { user_id: timestamp }
last_miao = {}

# ==================================================
#  ساخت کلاینت یوزربات
# ==================================================
app = Client(
    name="userbot",
    session_string=SESSION_STRING,
    api_id=API_ID,
    api_hash=API_HASH,
    in_memory=True,  # برای Railway که فایل ذخیره نمیشه
)

# ==================================================
#  توابع کمکی
# ==================================================
def get_remaining_time(last_time, cooldown_seconds=300):
    """بازگرداندن زمان باقی‌مونده به ثانیه"""
    elapsed = time.time() - last_time
    if elapsed >= cooldown_seconds:
        return 0
    return int(cooldown_seconds - elapsed)

def format_time(seconds):
    """تبدیل ثانیه به رشته خوانا"""
    if seconds <= 0:
        return "همین الان"
    minutes = seconds // 60
    secs = seconds % 60
    if minutes > 0:
        return f"{minutes} دقیقه و {secs} ثانیه"
    return f"{secs} ثانیه"

# ==================================================
#  هندلر پیام‌های «میو» و «میو میو»
# ==================================================
@app.on_message(filters.text & filters.group & filters.regex(r'^(میو|میو میو)$'))
async def miao_handler(client, message):
    chat_id = message.chat.id
    user_id = message.from_user.id

    # ۱. چک کن که گروه مجاز هست یا نه
    if chat_id not in ALLOWED_CHATS:
        return  # هیچ کاری نکن

    # ۲. چک کن کاربر قبلاً میو کرده یا نه
    now = time.time()
    if user_id in last_miao:
        remaining = get_remaining_time(last_miao[user_id])
        if remaining > 0:
            # هنوز تایم نرسیده → پیام خطا بده
            time_str = format_time(remaining)
            await message.reply(
                f"⏳ {time_str} دیگه می‌تونی میو کنی!"
            )
            return

    # ۳. تایم گذشته یا اولین باره → اجازه میو بده
    last_miao[user_id] = now  # زمان جدید رو ثبت کن

    # جواب شبیه ربات اصلی (عدد میوپوینت ثابت)
    fake_points = 140396  # یه عدد نمونه
    await message.reply(
        f"401 میو پوینت گرفتی 🐾\n"
        f"💰 میو پوینت هات : {fake_points:,} 🪙\n"
        f"⏳ بعد از ۴:۲۵ میتونی دوباره میو میو کنی"
    )

# ==================================================
#  اجرای ربات
# ==================================================
if __name__ == "__main__":
    print("🤖 ربات میو روشن شد...")
    print(f"✅ گروه‌های فعال: {ALLOWED_CHATS}")
    app.run()
