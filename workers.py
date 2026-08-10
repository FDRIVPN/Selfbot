import asyncio
from pyrogram import Client, filters
from pyrogram.types import Message
from config import API_ID, API_HASH, BOT_USER_ID
from database import get_user, get_all_users

active_tasks = {}

async def click_exact(message: Message, exact_text: str) -> bool:
    """فقط اگر متن دکمه دقیقاً برابر باشه کلیک می‌کنه. هیچ fallback نداره."""
    if not exact_text or not message.reply_markup or not message.reply_markup.inline_keyboard:
        return False

    exact_text = exact_text.strip()
    for row in message.reply_markup.inline_keyboard:
        for btn in row:
            btn_text = (btn.text or "").strip()
            if btn_text == exact_text:
                try:
                    await message.click(exact_text)
                    print(f"✅ کلیک شد روی: {exact_text}")
                    return True
                except Exception as e:
                    print(f"❌ خطا در کلیک: {e}")
                    return False
    print(f"⚠️ دکمه «{exact_text}» پیدا نشد → کلیک نشد")
    return False


async def rescue_loop(client: Client, chat_id: int, msg_id: int, rescue_btn: str):
    """اسپم کلیک روی دکمه دقیق نجات (اگر پیدا نشد دکمه اول)"""
    for i in range(40):
        try:
            msg = await client.get_messages(chat_id, msg_id)
            if not msg or not msg.reply_markup or not msg.reply_markup.inline_keyboard:
                print("✅ دکمه نجات ناپدید شد")
                break

            clicked = False
            if rescue_btn:
                for row in msg.reply_markup.inline_keyboard:
                    for btn in row:
                        if (btn.text or "").strip() == rescue_btn.strip():
                            await msg.click(rescue_btn.strip())
                            clicked = True
                            break
                    if clicked:
                        break

            if not clicked:
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

        if user["selected_groups"]:
            chat_ids = [int(g) for g in user["selected_groups"]]
        else:
            chat_ids = [-1003998125518]
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
            print(f"📩 [{phone}] پیام دریافت شد: {text[:80]}")

            harvest_btn = u.get("harvest_button") or "برداشت میو پوینت ها 🧲"
            rescue_btn = u.get("rescue_button") or "نجات پیشی خیابونی 🐱 🐈"

            # نجات پیشی خیابونی
            if u.get("rescue_enabled") and "نجات پیشی خیابونی" in text:
                asyncio.create_task(rescue_loop(c, message.chat.id, message.id, rescue_btn))
                return

            # فقط کلیک روی دکمه دقیق برداشت (بدون جستجوی کلمه میو/پیشی)
            if u.get("fish_enabled"):
                await click_exact(message, harvest_btn)

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
                    interval = max(30, int(u.get("meow_interval") or 300))
                    for cid in chat_ids:
                        try:
                            await client.send_message(cid, "میو")
                            print(f"😺 [{phone}] میو فرستاده شد به {cid}")
                            await asyncio.sleep(2)
                        except Exception as e:
                            print(f"❌ خطا میو {phone}: {e}")
                    await asyncio.sleep(interval)

            async def fish_loop():
                while True:
                    u = get_user(phone)
                    if not u or not u["is_active"] or not u["fish_enabled"]:
                        await asyncio.sleep(15)
                        continue
                    interval = max(30, int(u.get("fish_interval") or 600))
                    for cid in chat_ids:
                        try:
                            await client.send_message(cid, "پیشی")
                            print(f"🐱 [{phone}] پیشی فرستاده شد به {cid}")
                            await asyncio.sleep(4)
                        except Exception as e:
                            print(f"❌ خطا پیشی {phone}: {e}")
                    await asyncio.sleep(interval)

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
