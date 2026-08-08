import asyncio
from pyrogram import Client, filters
from pyrogram.types import Message
from config import API_ID, API_HASH, BOT_USER_ID
from database import get_user, get_all_users

active_tasks = {}

async def click_button(message: Message, keywords: list):
    if not message.reply_markup or not message.reply_markup.inline_keyboard:
        return False
    for row in message.reply_markup.inline_keyboard:
        for btn in row:
            text = btn.text or ""
            for kw in keywords:
                if kw in text:
                    try:
                        await message.click(text=text)
                        print(f"✅ کلیک شد روی: {text}")
                        return True
                    except Exception as e:
                        print(f"❌ خطا در کلیک: {e}")
                        return False
    try:
        await message.click(0)
        return True
    except:
        return False

async def rescue_loop(client: Client, chat_id: int, msg_id: int):
    for i in range(40):
        try:
            msg = await client.get_messages(chat_id, msg_id)
            if not msg or not msg.reply_markup:
                break
            await msg.click(0)
            print(f"🚑 نجات کلیک {i+1}")
            await asyncio.sleep(1.4)
        except Exception as e:
            print(f"خطا نجات: {e}")
            break

async def selfbot_worker(phone: str):
    print(f"🚀 Worker شروع شد برای {phone}")

    while True:
        user = get_user(phone)
        if not user or not user["is_active"] or not user["session_string"]:
            print(f"⏳ {phone} غیرفعال یا بدون سشن - صبر می‌کنم...")
            await asyncio.sleep(20)
            continue

        # اگر گروهی انتخاب نشده، از گروه پیش‌فرض استفاده کن
        if user["selected_groups"]:
            chat_ids = [int(g) for g in user["selected_groups"]]
        else:
            chat_ids = [-1003998125518]  # گروه پیش‌فرض تو
            print(f"⚠️ گروهی انتخاب نشده → از گروه پیش‌فرض استفاده می‌شود")

        print(f"📋 گروه‌های هدف {phone}: {chat_ids}")

        client = Client(
            name=f"sb_{phone}",
            session_string=user["session_string"],
            api_id=API_ID,
            api_hash=API_HASH,
            in_memory=True
        )

        @client.on_message(filters.chat(chat_ids) & filters.user(BOT_USER_ID))
        async def handler(c: Client, message: Message):
            u = get_user(phone)
            if not u or not u["is_active"]:
                return

            text = message.text or message.caption or ""
            print(f"📩 [{phone}] پیام دریافت شد: {text[:60]}")

            if u["fish_enabled"] and "نجات پیشی خیابونی" in text:
                asyncio.create_task(rescue_loop(c, message.chat.id, message.id))

            if u["fish_enabled"] and ("پیشی" in text or "میو" in text):
                await click_button(message, ["برداشت میو پوینت", "برداشت"])

        try:
            await client.start()
            me = await client.get_me()
            print(f"✅ {phone} آنلاین شد → {me.first_name} (@{me.username})")

            async def meow_loop():
                while True:
                    u = get_user(phone)
                    if not u or not u["is_active"] or not u["meow_enabled"]:
                        await asyncio.sleep(15)
                        continue
                    for cid in chat_ids:
                        try:
                            await client.send_message(cid, "میو")
                            print(f"😺 [{phone}] میو فرستاده شد به {cid}")
                            await asyncio.sleep(2)
                        except Exception as e:
                            print(f"❌ خطا میو {phone}: {e}")
                    await asyncio.sleep(300)

            async def fish_loop():
                while True:
                    u = get_user(phone)
                    if not u or not u["is_active"] or not u["fish_enabled"]:
                        await asyncio.sleep(15)
                        continue
                    for cid in chat_ids:
                        try:
                            await client.send_message(cid, "پیشی")
                            print(f"🐱 [{phone}] پیشی فرستاده شد به {cid}")
                            await asyncio.sleep(4)
                        except Exception as e:
                            print(f"❌ خطا پیشی {phone}: {e}")
                    await asyncio.sleep(600)

            meow_task = asyncio.create_task(meow_loop())
            fish_task = asyncio.create_task(fish_loop())

            while True:
                u = get_user(phone)
                if not u or not u["is_active"]:
                    break
                await asyncio.sleep(10)

            meow_task.cancel()
            fish_task.cancel()
            await asyncio.gather(meow_task, fish_task, return_exceptions=True)

        except Exception as e:
            print(f"❌ خطای بزرگ در worker {phone}: {e}")
        finally:
            try:
                await client.stop()
            except:
                pass
            print(f"🛑 {phone} متوقف شد")

        await asyncio.sleep(15)

def start_worker(phone: str, loop):
    if phone in active_tasks and not active_tasks[phone].done():
        return
    task = asyncio.run_coroutine_threadsafe(selfbot_worker(phone), loop)
    active_tasks[phone] = task
    print(f"▶️ تسک برای {phone} ساخته شد")

def stop_worker(phone: str):
    if phone in active_tasks and not active_tasks[phone].done():
        active_tasks[phone].cancel()
        del active_tasks[phone]
        print(f"⏹️ تسک {phone} متوقف شد")

def start_all_active(loop):
    users = get_all_users()
    for u in users:
        if u["is_active"]:
            start_worker(u["phone"], loop)
