import asyncio
from pyrogram import Client, filters
from pyrogram.types import Message
from pyrogram.enums import ChatType
from config import API_ID, API_HASH, BOT_USER_ID
from database import get_user, get_all_users

# نگهداری تسک‌های فعال
active_tasks = {}  # phone -> task

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
                        return True
                    except Exception as e:
                        print(f"خطا در کلیک: {e}")
                        return False
    # روش جایگزین: اولین دکمه
    try:
        await message.click(0)
        return True
    except:
        return False

async def rescue_loop(client: Client, chat_id: int, msg_id: int):
    for _ in range(40):
        try:
            msg = await client.get_messages(chat_id, msg_id)
            if not msg or not msg.reply_markup:
                break
            await msg.click(0)
            await asyncio.sleep(1.4)
        except Exception:
            break

async def selfbot_worker(phone: str):
    print(f"🚀 Worker شروع شد برای {phone}")

    while True:
        user = get_user(phone)
        if not user or not user["is_active"] or not user["session_string"] or not user["selected_groups"]:
            await asyncio.sleep(20)
            continue

        chat_ids = [int(g) for g in user["selected_groups"]]

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

            # نجات پیشی خیابونی
            if u["fish_enabled"] and "نجات پیشی خیابونی" in text:
                asyncio.create_task(rescue_loop(c, message.chat.id, message.id))

            # برداشت میو
            if u["fish_enabled"] and ("پیشی" in text or "میو" in text):
                await click_button(message, ["برداشت میو پوینت", "برداشت"])

        try:
            await client.start()
            print(f"✅ {phone} آنلاین شد")

            # حلقه ارسال میو (هر ۵ دقیقه)
            async def meow_loop():
                while True:
                    u = get_user(phone)
                    if not u or not u["is_active"] or not u["meow_enabled"]:
                        await asyncio.sleep(15)
                        continue
                    for cid in chat_ids:
                        try:
                            await client.send_message(cid, "میو")
                            print(f"😺 [{phone}] میو → {cid}")
                            await asyncio.sleep(2)
                        except Exception as e:
                            print(f"خطا میو {phone}: {e}")
                    await asyncio.sleep(300)  # ۵ دقیقه

            # حلقه ارسال پیشی (هر ۱۰ دقیقه)
            async def fish_loop():
                while True:
                    u = get_user(phone)
                    if not u or not u["is_active"] or not u["fish_enabled"]:
                        await asyncio.sleep(15)
                        continue
                    for cid in chat_ids:
                        try:
                            await client.send_message(cid, "پیشی")
                            print(f"🐱 [{phone}] پیشی → {cid}")
                            await asyncio.sleep(4)
                        except Exception as e:
                            print(f"خطا پیشی {phone}: {e}")
                    await asyncio.sleep(600)  # ۱۰ دقیقه

            meow_task = asyncio.create_task(meow_loop())
            fish_task = asyncio.create_task(fish_loop())

            # نگه داشتن کلاینت تا وقتی فعال باشد
            while True:
                u = get_user(phone)
                if not u or not u["is_active"]:
                    break
                await asyncio.sleep(10)

            meow_task.cancel()
            fish_task.cancel()
            await asyncio.gather(meow_task, fish_task, return_exceptions=True)

        except Exception as e:
            print(f"❌ خطا در worker {phone}: {e}")
        finally:
            try:
                await client.stop()
            except:
                pass
            print(f"🛑 {phone} متوقف شد")

        await asyncio.sleep(15)

def start_worker(phone: str, loop: asyncio.AbstractEventLoop):
    if phone in active_tasks and not active_tasks[phone].done():
        return
    task = asyncio.run_coroutine_threadsafe(selfbot_worker(phone), loop)
    active_tasks[phone] = task

def stop_worker(phone: str):
    if phone in active_tasks and not active_tasks[phone].done():
        active_tasks[phone].cancel()
        del active_tasks[phone]

def start_all_active(loop: asyncio.AbstractEventLoop):
    for u in get_all_users():
        if u["is_active"]:
            start_worker(u["phone"], loop)
