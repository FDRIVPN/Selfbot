import asyncio
from pyrogram import Client
from pyrogram.errors import (
    PhoneNumberInvalid, PhoneCodeInvalid, PhoneCodeExpired, SessionPasswordNeeded
)
from config import API_ID, API_HASH

pending = {}  # phone -> {client, phone_code_hash}

async def send_code(phone: str):
    if phone in pending:
        try:
            await pending[phone]["client"].disconnect()
        except:
            pass
        pending.pop(phone, None)

    client = Client("temp", api_id=API_ID, api_hash=API_HASH, in_memory=True)
    await client.connect()
    try:
        sent = await client.send_code(phone)
        pending[phone] = {
            "client": client,
            "phone_code_hash": sent.phone_code_hash
        }
        return True
    except PhoneNumberInvalid:
        await client.disconnect()
        return "شماره نامعتبر است"
    except Exception as e:
        await client.disconnect()
        return f"خطا: {str(e)}"

async def sign_in(phone: str, code: str):
    if phone not in pending:
        return "نشست منقضی شده، دوباره شماره وارد کنید"

    data = pending[phone]
    client = data["client"]
    try:
        if not client.is_connected:
            await client.connect()

        await client.sign_in(
            phone_number=phone,
            phone_code_hash=data["phone_code_hash"],
            phone_code=code
        )
        session_string = await client.export_session_string()
        await client.disconnect()
        pending.pop(phone, None)
        return session_string
    except SessionPasswordNeeded:
        return "need_password"
    except PhoneCodeInvalid:
        return "کد اشتباه است"
    except PhoneCodeExpired:
        await client.disconnect()
        pending.pop(phone, None)
        return "کد منقضی شده"
    except Exception as e:
        await client.disconnect()
        pending.pop(phone, None)
        return f"خطا: {str(e)}"

async def check_password(phone: str, password: str):
    if phone not in pending:
        return "نشست منقضی شده"

    client = pending[phone]["client"]
    try:
        if not client.is_connected:
            await client.connect()
        await client.check_password(password)
        session_string = await client.export_session_string()
        await client.disconnect()
        pending.pop(phone, None)
        return session_string
    except Exception as e:
        await client.disconnect()
        pending.pop(phone, None)
        return f"خطا: {str(e)}"

async def get_groups(session_string: str):
    client = Client(
        "tmp_groups",
        session_string=session_string,
        api_id=API_ID,
        api_hash=API_HASH,
        in_memory=True,
        no_updates=True
    )
    try:
        await client.start()
        groups = []
        async for dialog in client.get_dialogs(limit=200):
            chat = dialog.chat
            if chat and chat.type in ("group", "supergroup"):
                groups.append({
                    "id": str(chat.id),
                    "title": chat.title or "بدون نام",
                    "members": getattr(chat, "members_count", 0) or 0
                })
        print(f"✅ {len(groups)} گروه پیدا شد")
        return groups
    except Exception as e:
        print(f"❌ خطا در دریافت گروه‌ها: {e}")
        return f"error: {str(e)}"
    finally:
        try:
            await client.stop()
        except:
            pass
