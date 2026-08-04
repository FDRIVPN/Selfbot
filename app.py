from flask import Flask, request, jsonify, render_template, make_response
from pyrogram import Client
import asyncio
import os
import json
from datetime import datetime

app = Flask(__name__)

# ==================== دریافت همه تنظیمات از متغیرهای محیطی ====================
API_ID = int(os.environ.get('API_ID', 0))
API_HASH = os.environ.get('API_HASH', '')
SECRET_KEY = os.environ.get('SECRET_KEY', '')

# بررسی وجود اطلاعات ضروری
if API_ID == 0 or not API_HASH:
    print("\n⚠️  خطا: API_ID و API_HASH را در محیط (Environment) ست کنید!")
    print("مثال برای لینوکس/ترمینال:")
    print("export API_ID=12345")
    print("export API_HASH=your_hash_here")
    print("سپس دوباره برنامه را اجرا کنید.\n")
    exit(1)

if not SECRET_KEY:
    print("\n⚠️  هشدار: SECRET_KEY در محیط ست نشده! یک کلید تصادفی موقت ساخته شد.")
    print("برای ساخت کلید دائمی، از دستور زیر استفاده کن:")
    print("python -c 'import os; print(os.urandom(24).hex())'")
    print("سپس: export SECRET_KEY='کلید_ساخته_شده'\n")
    SECRET_KEY = os.urandom(24).hex()  # ساخت کلید موقت

app.secret_key = SECRET_KEY

# دیکشنری برای نگهداری کلاینت‌های فعال
active_clients = {}
sessions = {}

# ==================== توابع کمکی ====================

def load_sessions():
    global sessions
    if os.path.exists('sessions.json'):
        with open('sessions.json', 'r') as f:
            sessions = json.load(f)
    else:
        sessions = {}

def save_sessions():
    with open('sessions.json', 'w') as f:
        json.dump(sessions, f)

# ==================== تابع اصلی ارسال کد (بدون قطع کردن) ====================

async def send_code_async(phone, proxy=None):
    try:
        # فقط کلاینت قبلی رو از حافظه حذف کن (قطع نمی‌کنه)
        if phone in active_clients:
            active_clients.pop(phone, None)
        
        client = Client(
            f"sessions/{phone}",
            api_id=API_ID,
            api_hash=API_HASH,
            proxy=proxy
        )
        
        await client.connect()
        
        if await client.is_user_authorized():
            active_clients[phone] = {
                "client": client,
                "phone_code_hash": None
            }
            return {"status": "authorized", "phone": phone}
        
        sent_code = await client.send_code(phone)
        active_clients[phone] = {
            "client": client,
            "phone_code_hash": sent_code.phone_code_hash
        }
        
        return {"status": "code_sent", "phone": phone}
    except Exception as e:
        return {"status": "error", "message": str(e)}

async def sign_in_async(phone, code):
    try:
        if phone not in active_clients:
            return {"status": "error", "message": "شماره پیدا نشد"}
        
        client = active_clients[phone]["client"]
        phone_code_hash = active_clients[phone]["phone_code_hash"]
        
        await client.sign_in(phone, code, phone_code_hash)
        
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
    phone = request.cookies.get('phone')
    logged_in = request.cookies.get('logged_in')
    
    if phone and logged_in == 'true' and phone in sessions:
        return render_template('dashboard.html', phone=phone)
    return render_template('login.html')

@app.route('/send_code', methods=['POST'])
def send_code():
    data = request.get_json(force=True)
    phone = data.get('phone')
    proxy = data.get('proxy')
    
    if not phone:
        return jsonify({"status": "error", "message": "شماره تلفن الزامی است"})
    
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    result = loop.run_until_complete(send_code_async(phone, proxy))
    loop.close()
    
    return jsonify(result)

@app.route('/verify_code', methods=['POST'])
def verify_code():
    data = request.get_json(force=True)
    phone = data.get('phone')
    code = data.get('code')
    
    if not phone or not code:
        return jsonify({"status": "error", "message": "شماره و کد الزامی است"})
    
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    result = loop.run_until_complete(sign_in_async(phone, code))
    loop.close()
    
    if result["status"] == "success":
        resp = make_response(jsonify(result))
        resp.set_cookie('phone', phone, max_age=60*60*24*30)
        resp.set_cookie('logged_in', 'true', max_age=60*60*24*30)
        return resp
    
    return jsonify(result)

@app.route('/logout', methods=['POST'])
def logout():
    try:
        data = request.get_json(force=True)
        phone = data.get('phone')
        
        if not phone:
            return jsonify({"status": "error", "message": "شماره تلفن ارسال نشده"})
        
        # فقط از حافظه حذف کن (قطع نمی‌کنه)
        if phone in active_clients:
            active_clients.pop(phone, None)
        
        if phone in sessions:
            sessions.pop(phone, None)
            save_sessions()
        
        resp = make_response(jsonify({"status": "success", "message": "خروج موفق"}))
        resp.set_cookie('phone', '', expires=0)
        resp.set_cookie('logged_in', '', expires=0)
        
        return resp
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})

@app.route('/get_clients', methods=['GET'])
def get_clients():
    clients_list = []
    for phone, data in active_clients.items():
        clients_list.append({
            "phone": phone,
            "connected": data["client"].is_connected if hasattr(data["client"], "is_connected") else False
        })
    return jsonify({"clients": clients_list, "count": len(clients_list)})

@app.route('/status')
def status():
    return jsonify({
        "active_clients": len(active_clients),
        "sessions": len(sessions),
        "status": "running"
    })

# ==================== اجرا ====================

if __name__ == '__main__':
    if not os.path.exists('sessions'):
        os.makedirs('sessions')
    
    load_sessions()
    app.run(host='0.0.0.0', port=5000, debug=True)
